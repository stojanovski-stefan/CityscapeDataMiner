r"""
--------------------------------------------------------------------------

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

Where everything lives, locally and remotely.

Two classes, deliberately kept side by side:

* ``DataPaths`` answers "where on disk does X go?"
* ``SourceUrls`` answers "where on the internet does X come from?"

Every file name and URL template in the program is spelled out exactly once,
here.  That matters more than it looks: the TIGER stem ``tl_2025_39_place``
appears in the archive URL, in the cached zip name, and in the unpacked
shapefile name, and the three must agree or the sentinel-file caching in
FetchUtility silently re-downloads on every run.
"""

from pathlib import Path

from .config import Config
from .models import ResolvedCity


class DataPaths:
    """Derives every path under the configured output directory.

    The tree, which is also what ``setup_data_file_tree`` creates::

        <output_dir>/
         |-- cache/                shared across cities, persists between runs
         |   |-- zips/             every archive downloaded, kept after unpacking
         |   |-- gazetteer/        the unpacked national place Gazetteer
         |   |-- tiger/            flat -- all states unpack into one directory
         |   |-- pums/
         |   |   |-- p<st>/        lowercase USPS, e.g. pdc/
         |   |   +-- h<st>/
         |   +-- osm/              raw statewide .pbf extracts
         +-- cities/
             +-- <slug>/           one per resolved city: name-designator-st-geoid

    Instances hold no mutable state once constructed, so the single instance is
    shared freely between the producer and consumer threads.
    """

    def __init__(self, config: Config) -> None:
        """Derive the fixed directories from the configuration.

        Args:
            config: the loaded configuration.
        """
        self.config = config

        self.root = Path(config.output_dir)
        self.cache = self.root / "cache"
        self.zips = self.cache / "zips"
        self.gazetteer_dir = self.cache / "gazetteer"
        self.tiger = self.cache / "tiger"
        self.pums = self.cache / "pums"
        self.osm = self.cache / "osm"
        self.cities = self.root / "cities"

    # ------------------------------------------------------------------
    # tree creation
    # ------------------------------------------------------------------

    def setup_data_file_tree(self) -> None:
        """Create the whole output tree, doing nothing where it already exists.

        Called once on the main thread before any worker starts.  Doing it up
        front rather than lazily inside the workers means no two threads ever
        race to create the same directory, and a permission problem surfaces
        before a single byte has been downloaded.
        """
        for directory in (
            self.gazetteer_dir,
            self.zips,
            self.tiger,
            self.pums,
            self.osm,
            self.cities,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # gazetteer
    # ------------------------------------------------------------------

    @property
    def gazetteer_text_file(self) -> Path:
        """The unpacked Gazetteer for the configured vintage.

        Returns:
            <output_dir>/cache/gazetteer/<year>_Gaz_place_national.txt
        """
        return self.gazetteer_dir / f"{self.config.gaz_year}_Gaz_place_national.txt"

    @property
    def gazetteer_zip(self) -> Path:
        """The downloaded Gazetteer archive.

        Returns:
            <output_dir>/cache/zips/<year>_gazetteer.zip
        """
        return self.zips / f"{self.config.gaz_year}_gazetteer.zip"

    # ------------------------------------------------------------------
    # TIGER/Line, per state
    # ------------------------------------------------------------------

    def place_stem(self, fips: str) -> str:
        """Base name shared by the PLACE archive and the shapefile inside it.

        Args:
            fips: two-digit state FIPS code, e.g. "39".

        Returns:
            e.g. "tl_2025_39_place".
        """
        return f"tl_{self.config.tiger_year}_{fips}_place"

    def place_zip(self, fips: str) -> Path:
        """Cached TIGER/Line PLACE archive for a state.

        Args:
            fips: two-digit state FIPS code.

        Returns:
            The path the archive is kept at.
        """
        return self.zips / f"{self.place_stem(fips)}.zip"

    def place_shapefile(self, fips: str) -> Path:
        """Unpacked statewide PLACE shapefile, the source of city boundaries.

        Args:
            fips: two-digit state FIPS code.

        Returns:
            The path to the .shp.
        """
        return self.tiger / f"{self.place_stem(fips)}.shp"

    def puma_stem(self, fips: str) -> str:
        """Base name shared by the PUMA archive and the shapefile inside it.

        Args:
            fips: two-digit state FIPS code.

        Returns:
            e.g. "tl_2025_39_puma20".
        """
        return f"tl_{self.config.tiger_year}_{fips}_puma20"

    def puma_zip(self, fips: str) -> Path:
        """Cached TIGER/Line PUMA archive for a state.

        Args:
            fips: two-digit state FIPS code.

        Returns:
            The path the archive is kept at.
        """
        return self.zips / f"{self.puma_stem(fips)}.zip"

    def puma_shapefile(self, fips: str) -> Path:
        """Unpacked statewide PUMA shapefile.

        Args:
            fips: two-digit state FIPS code.

        Returns:
            The path to the .shp.
        """
        return self.tiger / f"{self.puma_stem(fips)}.shp"

    # ------------------------------------------------------------------
    # ACS PUMS, per state
    # ------------------------------------------------------------------

    def pums_dir(self, kind: str, lower_usps: str) -> Path:
        """Directory one PUMS archive unpacks into.

        Args:
            kind: "p" for person records, "h" for housing records.
            lower_usps: lower-cased state code, e.g. "oh".

        Returns:
            e.g. <output_dir>/cache/pums/poh
        """
        return self.pums / f"{kind}{lower_usps}"

    def pums_zip(self, kind: str, lower_usps: str) -> Path:
        """Cached PUMS archive.

        Args:
            kind: "p" for person records, "h" for housing records.
            lower_usps: lower-cased state code, e.g. "oh".

        Returns:
            e.g. <output_dir>/cache/zips/csv_poh.zip
        """
        return self.zips / f"csv_{kind}{lower_usps}.zip"

    def pums_csv_name(self, kind: str, fips: str) -> str:
        """Name of the CSV inside a PUMS archive.

        Note the asymmetry with pums_zip(): the Census names the *archive*
        after the USPS code but the *CSV inside it* after the FIPS code, so
        csv_poh.zip contains psam_p39.csv.  Getting this wrong makes the
        sentinel check never match and every run re-download.

        Args:
            kind: "p" for person records, "h" for housing records.
            fips: two-digit state FIPS code.

        Returns:
            e.g. "psam_p39.csv".
        """
        return f"psam_{kind}{fips}.csv"

    def pums_csv(self, kind: str, lower_usps: str, fips: str) -> Path:
        """Unpacked PUMS CSV for a state.

        Args:
            kind: "p" for person records, "h" for housing records.
            lower_usps: lower-cased state code, e.g. "oh".
            fips: two-digit state FIPS code.

        Returns:
            The path to the CSV.
        """
        return self.pums_dir(kind, lower_usps) / self.pums_csv_name(kind, fips)

    # ------------------------------------------------------------------
    # OpenStreetMap, per state
    # ------------------------------------------------------------------

    def state_pbf(self, geofabrik_slug: str) -> Path:
        """Cached statewide Geofabrik extract.

        Args:
            geofabrik_slug: the region name, e.g. "ohio".

        Returns:
            <output_dir>/cache/osm/<slug>-latest.osm.pbf
        """
        return self.osm / f"{geofabrik_slug}-latest.osm.pbf"

    # ------------------------------------------------------------------
    # per-city deliverables
    # ------------------------------------------------------------------

    def city_directory(self, city: ResolvedCity) -> Path:
        """The directory a city's deliverables are written to.

        Args:
            city: a resolved city.

        Returns:
            <output_dir>/cities/<slug>
        """
        return self.cities / city.slug

    def city_file(self, city: ResolvedCity, name: str) -> Path:
        """One named file inside a city's directory.

        Args:
            city: a resolved city.
            name: the file name, e.g. "boundary.shp".

        Returns:
            The full path.
        """
        return self.city_directory(city) / name


class SourceUrls:
    """Builds every upstream URL the program fetches from.

    Separate from DataPaths so that pointing the program at a mirror is a
    matter of changing two config values and nothing else.
    """

    def __init__(self, config: Config) -> None:
        """Remember the base URLs and vintages.

        Args:
            config: the loaded configuration.
        """
        self.config = config

        # a trailing slash in the config would otherwise produce '//' in every
        # URL, which most servers tolerate and some emphatically do not
        self.census = config.census.rstrip("/")
        self.geofabrik = config.geofabrik.rstrip("/")

    def gazetteer(self) -> str:
        """The national place Gazetteer archive for the configured vintage.

        Returns:
            The download URL.
        """
        year = self.config.gaz_year
        return (
            f"{self.census}/geo/docs/maps-data/data/gazetteer/"
            f"{year}_Gazetteer/{year}_Gaz_place_national.zip"
        )

    def tiger_place(self, stem: str) -> str:
        """The statewide TIGER/Line PLACE archive.

        Args:
            stem: the archive stem from DataPaths.place_stem().

        Returns:
            The download URL.
        """
        return (
            f"{self.census}/geo/tiger/TIGER{self.config.tiger_year}/PLACE/{stem}.zip"
        )

    def tiger_puma(self, stem: str) -> str:
        """The statewide TIGER/Line PUMA archive.

        The directory on census.gov is PUMA20, not PUMA -- the 2020 vintage
        lives under its own path and the unsuffixed one is the 2010 geography.

        Args:
            stem: the archive stem from DataPaths.puma_stem().

        Returns:
            The download URL.
        """
        return (
            f"{self.census}/geo/tiger/TIGER{self.config.tiger_year}/PUMA20/{stem}.zip"
        )

    def pums(self, kind: str, lower_usps: str) -> str:
        """The ACS PUMS archive for one state.

        Args:
            kind: "p" for person records, "h" for housing records.
            lower_usps: lower-cased state code, e.g. "oh".

        Returns:
            The download URL.
        """
        return (
            f"{self.census}/programs-surveys/acs/data/pums/"
            f"{self.config.pums_year}/{self.config.pums_span}/"
            f"csv_{kind}{lower_usps}.zip"
        )

    def geofabrik_extract(self, geofabrik_slug: str) -> str:
        """The statewide OSM extract.

        Args:
            geofabrik_slug: the region name, e.g. "ohio".

        Returns:
            The download URL.
        """
        return f"{self.geofabrik}/{geofabrik_slug}-latest.osm.pbf"
