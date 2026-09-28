r"""
---------------------------------------------------------------------------

Copyright (c) PC2Lab Development Team
All rights reserved.

This file is part of free(dom) software -- you can redistribute it
and/or modify it under the terms of the GNU General Public
License (GPL)as published by the Free Software Foundation, either
version 3 (GPL v3), or (at your option) a later version.

The software is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

Miami University and PC2Lab makes no representations or warranties
about the suitability of the software, either express or implied,
including but not limited to the implied warranties of
merchantability, fitness for a particular purpose, or
non-infringement.  Miami University and PC2Lab is not be liable for
any damages suffered by licensee as a result of using, result of
using, modifying or distributing this software or its derivatives.

By using or copying this Software, Licensee agrees to abide by the
intellectual property laws, and all other applicable laws of the
U.S., and the terms of this license.

Authors: Stefan Stojanovski      stojansz@miamiOH.edu

---------------------------------------------------------------------------

The consumer half of the pipeline: deriving one city's deliverables.

A consumer thread takes a ResolvedCity off the city queue and produces, in
``<output_dir>/cities/<slug>/``:

* ``boundary.shp`` + sidecars -- the city outline, for modelgen
* ``boundary.geojson``        -- the same outline, as the OSM clip polygon
* ``city.osm.pbf``            -- the state extract clipped to that outline
* ``city.osm``                -- the same clip as the XML modelgen reads
* ``puma.shp`` + sidecars     -- the statewide PUMA geometry
* ``pums_p.csv`` / ``pums_h.csv`` -- the state's person and housing microdata
* ``modelgen.args``           -- an argument list naming all of the above

Thread safety: a consumer only ever *reads* the shared caches and only ever
writes inside its own city's directory, and no two consumers are given the same
city.  So although several consumers run at once, none of them shares a file
with another, and none of them needs a lock.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .fetch_utility import FetchUtility
from .logger import get_logger
from .models import ResolvedCity
from .paths import DataPaths
from .results import Results
from .states import STATE_TABLE

logger = get_logger(__name__)

# The five files GDAL writes for one ESRI shapefile.  Stale copies of all of
# them have to be cleared before a re-run, otherwise ogr2ogr appends to the
# existing layer instead of replacing it.
SHAPEFILE_SIDECARS = (".shp", ".dbf", ".shx", ".prj", ".cpg")


class CityConsumer:
    """Builds one city's deliverables out of its state's cached sources.

    An instance is shared by every thread in the consumer pool; it holds no
    mutable state of its own beyond the Results recorder, which has its own
    lock.
    """

    def __init__(self, fetcher: FetchUtility, paths: DataPaths, results: Results) -> None:
        """Wire up the collaborators.

        Args:
            fetcher: used only to run ogr2ogr, ogrinfo and osmium.
            paths: where the cached sources and the city directory live.
            results: where each city's outcome is recorded.
        """
        self.fetcher = fetcher
        self.paths = paths
        self.results = results

    def __call__(self, city: ResolvedCity) -> None:
        """Build one city.

        This is the callable the consumer pool runs.

        Every stage is attempted even if an earlier one failed, so one run
        reports everything wrong with a city rather than only the first thing.
        The OSM clip in particular will fail loudly when the boundary step did
        not produce its GeoJSON, which is more informative than skipping it.

        Args:
            city: the city to build.
        """
        logger.info("=== %s ===", city)
        self.paths.city_directory(city).mkdir(parents=True, exist_ok=True)

        built = self.extract_city_boundary(city)
        built = self.extract_city_osm(city) and built

        puma_column_dbf, puma_column_pums, puma_ok = self.extract_puma_and_pums(city)
        built = puma_ok and built

        if not built:
            logger.error("%s is incomplete", city.slug)
            self.results.record(city.slug, ok=False)
            return

        self.write_modelgen_args(city, puma_column_dbf, puma_column_pums)
        self.results.record(city.slug, ok=True)
        logger.info("built    %s", city.slug)

    # ------------------------------------------------------------------
    # boundary
    # ------------------------------------------------------------------

    def extract_city_boundary(self, city: ResolvedCity) -> bool:
        """Clip the one place out of the statewide TIGER PLACE layer.

        Produces both the ESRI shapefile modelgen consumes and the GeoJSON used
        as the OSM clip polygon.

        Args:
            city: the city to build.

        Returns:
            True on success.
        """
        boundary = self.paths.city_file(city, "boundary.shp")
        geojson = self.paths.city_file(city, "boundary.geojson")
        place_shapefile = self.paths.place_shapefile(city.fips)

        logger.info("boundary %s", city)

        if not place_shapefile.exists():
            logger.error("missing %s", place_shapefile)
            return False

        # ogr2ogr appends to an existing layer rather than replacing it, so
        # every trace of a previous run has to go first
        self._remove_shapefile(boundary)
        geojson.unlink(missing_ok=True)

        # the place GEOID is the one column that uniquely identifies a city.
        # This predicate goes to ogr2ogr as a single argv entry, so the quotes
        # in it are SQL quotes and no shell ever sees them.
        where = f"GEOID='{city.geoid}'"

        if not self.fetcher.run_command(
            [
                "ogr2ogr", "-f", "ESRI Shapefile",
                str(boundary), str(place_shapefile), "-where", where,
            ]
        ):
            logger.error("ogr2ogr failed for %s", city.geoid)
            return False

        if not self.fetcher.run_command(
            [
                "ogr2ogr", "-f", "GeoJSON",
                str(geojson), str(place_shapefile), "-where", where,
            ]
        ):
            logger.error("ogr2ogr (geojson) failed for %s", city.geoid)
            return False

        # a -where that matched nothing still writes a well-formed file, just
        # one whose "features" array is empty -- which would otherwise surface
        # much later as an inexplicably empty model
        if not self._has_features(geojson):
            logger.error(
                "empty boundary for GEOID %s -- wrong TIGER vintage?", city.geoid
            )
            return False

        return True

    # ------------------------------------------------------------------
    # OpenStreetMap
    # ------------------------------------------------------------------

    def extract_city_osm(self, city: ResolvedCity) -> bool:
        """Clip the city out of the statewide extract and convert it to XML.

        Args:
            city: the city to build.

        Returns:
            True on success.
        """
        geojson = self.paths.city_file(city, "boundary.geojson")
        clipped_pbf = self.paths.city_file(city, "city.osm.pbf")
        city_osm = self.paths.city_file(city, "city.osm")

        # checked during resolution, so the state is known to be present here
        state = STATE_TABLE.by_code(int(city.fips))
        assert state is not None, f"unresolved FIPS {city.fips} reached the consumer"

        state_pbf = self.paths.state_pbf(state.name)

        logger.info("osm      clipping %s -> %s", state.name, city.slug)

        if not state_pbf.exists():
            logger.error("missing %s", state_pbf)
            return False

        if not geojson.exists():
            logger.error("missing %s -- the boundary step did not run", geojson)
            return False

        # -p clips to the boundary polygon itself instead of its bounding box
        if not self.fetcher.run_command(
            [
                "osmium", "extract", "--overwrite",
                "-p", str(geojson), str(state_pbf),
                "-o", str(clipped_pbf),
            ]
        ):
            logger.error("osmium extract failed for %s", city.slug)
            return False

        # modelgen reads OSM XML, not the compact binary the clip is written in
        if not self.fetcher.run_command(
            ["osmium", "cat", "--overwrite", str(clipped_pbf), "-o", str(city_osm)]
        ):
            logger.error("osmium cat failed for %s", city.slug)
            return False

        return True

    # ------------------------------------------------------------------
    # PUMA geometry and PUMS microdata
    # ------------------------------------------------------------------

    def extract_puma_and_pums(self, city: ResolvedCity) -> tuple[str, str, bool]:
        """Copy the state's PUMA geometry and PUMS microdata into the city.

        Args:
            city: the city to build.

        Returns:
            A triple of (PUMA id column in the DBF, PUMA id column in the PUMS
            CSV, success).  Either column name may be empty if it could not be
            detected; that is a warning, not a failure.
        """
        puma_shapefile = self.paths.city_file(city, "puma.shp")
        person_csv = self.paths.city_file(city, "pums_p.csv")
        housing_csv = self.paths.city_file(city, "pums_h.csv")

        puma_source = self.paths.puma_shapefile(city.fips)
        person_source = self.paths.pums_csv("p", city.lower_usps, city.fips)
        housing_source = self.paths.pums_csv("h", city.lower_usps, city.fips)

        logger.info("puma/pums %s", city.usps)

        for source in (puma_source, person_source, housing_source):
            if not source.exists():
                logger.error("missing %s", source)
                return "", "", False

        # The whole statewide PUMA layer is kept: a city's households can be
        # drawn from any PUMA the city touches, so there is nothing to clip
        # away here.
        self._remove_shapefile(puma_shapefile)
        if not self.fetcher.run_command(
            ["ogr2ogr", "-f", "ESRI Shapefile", str(puma_shapefile), str(puma_source)]
        ):
            logger.error("ogr2ogr failed for PUMA %s", city.fips)
            return "", "", False

        for source, target in ((person_source, person_csv), (housing_source, housing_csv)):
            try:
                shutil.copyfile(source, target)
            except OSError as error:
                logger.error("could not copy %s: %s", source, error)
                return "", "", False

        # The PUMA id column differs between the two sources: PUMS CSVs call it
        # "PUMA", while the TIGER shapefile's DBF calls it "PUMACE20" (or
        # "PUMACE10" for the 2010 vintage).  modelgen defaults BOTH to "PUMA",
        # so the join silently finds nothing unless we name them explicitly.
        puma_column_dbf = self._detect_puma_column_in_dbf(puma_shapefile)
        if not puma_column_dbf:
            logger.warning("no PUMA id column found in %s", puma_shapefile)

        puma_column_pums = self._detect_puma_column_in_pums(person_csv)
        if not puma_column_pums:
            logger.warning("no PUMA id column found in %s", person_csv)

        return puma_column_dbf, puma_column_pums, True

    def _detect_puma_column_in_dbf(self, puma_shapefile: Path) -> str:
        """Read the PUMA id column name back out of the shapefile's DBF.

        It is PUMACE20 for the 2020 vintage and PUMACE10 for 2010, so the name
        is detected with ogrinfo rather than assumed.

        Args:
            puma_shapefile: path to the copied puma.shp.

        Returns:
            The column name, or an empty string if none was recognised.
        """
        info = self.fetcher.capture_command(
            ["ogrinfo", "-so", str(puma_shapefile), "puma"]
        )

        # ogrinfo prints one "NAME: type (width.precision)" line per field
        fallback = ""
        for line in info.splitlines():
            text = line.strip()

            name, separator, _ = text.partition(":")
            if not separator:
                continue

            field = name.strip()
            if field.startswith("PUMACE"):
                return field

            # any other column carrying PUMA in its name is a plausible second
            # choice, but only if nothing better turns up further down
            if not fallback and "PUMA" in field:
                fallback = field

        return fallback

    @staticmethod
    def _detect_puma_column_in_pums(pums_csv: Path) -> str:
        """Confirm the PUMA id column in the PUMS CSV header.

        The Census names it plainly "PUMA", but it is confirmed against the
        real header rather than assumed: a missing column here makes modelgen's
        PUMA join silently match nothing.

        Args:
            pums_csv: path to the copied pums_p.csv.

        Returns:
            "PUMA" if the header has it, an empty string otherwise.
        """
        try:
            with open(pums_csv, "r", encoding="utf-8", errors="replace") as csv_file:
                header = csv_file.readline()
        except OSError:
            return ""

        for field in header.split(","):
            # headers arrive quoted and CRLF-terminated in some vintages
            if field.strip().strip('"') == "PUMA":
                return "PUMA"

        return ""

    # ------------------------------------------------------------------
    # modelgen argument list
    # ------------------------------------------------------------------

    def write_modelgen_args(
        self, city: ResolvedCity, puma_column_dbf: str, puma_column_pums: str
    ) -> None:
        """Write ``<city>/modelgen.args``, naming every file built for a city.

        Args:
            city: the city that was built.
            puma_column_dbf: PUMA id column in the DBF; may be empty.
            puma_column_pums: PUMA id column in the PUMS CSV; may be empty.
        """
        # Absolute, so the argument file works no matter where modelgen is run
        # from.  resolve() also folds away the "." that an output_dir like
        # "./cityscape_data" would otherwise scatter through every path.
        city_dir = self.paths.city_directory(city).resolve()
        args_file = city_dir / "modelgen.args"

        lines = [
            f"# modelgen inputs for {city.namelsad}, {city.usps} (GEOID {city.geoid})",
            f"# generated {self._utc_timestamp()} by CityscapeAutomation",
            "# NOTE: --pop-gis is not produced by this program.",
            f"--shape {city_dir / 'boundary.shp'}",
            f"--dbf {city_dir / 'boundary.dbf'}",
            f"--osm-xml {city_dir / 'city.osm'}",
            f"--puma-shp {city_dir / 'puma.shp'}",
            f"--puma-dbf {city_dir / 'puma.dbf'}",
            f"--pums-p {city_dir / 'pums_p.csv'}",
            f"--pums-h {city_dir / 'pums_h.csv'}",
        ]

        if puma_column_dbf:
            lines.append(f"--puma-id-col-dbf {puma_column_dbf}")

        if puma_column_pums:
            lines.append(f"--puma-id-col-pums {puma_column_pums}")

        try:
            args_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as error:
            logger.warning("could not write %s: %s", args_file, error)

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_shapefile(shapefile: Path) -> None:
        """Delete every sidecar of a shapefile.

        e.g. boundary.shp/.dbf/.shx/.prj/.cpg.  ogr2ogr will not overwrite an
        existing layer, so the old one has to go first.

        Args:
            shapefile: path to the .shp; its siblings are derived from it.
        """
        for extension in SHAPEFILE_SIDECARS:
            shapefile.with_suffix(extension).unlink(missing_ok=True)

    @staticmethod
    def _has_features(geojson: Path) -> bool:
        """Check that ogr2ogr actually matched the requested GEOID.

        A -where clause that matches nothing still produces a valid GeoJSON
        file, just one whose "features" array is empty.

        Args:
            geojson: path to the boundary GeoJSON.

        Returns:
            True if the file holds at least one feature.
        """
        try:
            with open(geojson, "r", encoding="utf-8") as geojson_file:
                data = json.load(geojson_file)
        except (OSError, json.JSONDecodeError):
            return False

        return bool(data.get("features"))

    @staticmethod
    def _utc_timestamp() -> str:
        """UTC timestamp for the modelgen.args provenance header.

        Returns:
            The current time as an ISO-8601 instant, e.g. 2026-09-27T14:03:11Z.
        """
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
