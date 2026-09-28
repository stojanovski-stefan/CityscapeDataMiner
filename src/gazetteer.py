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

Downloading, parsing and resolving against the Census place Gazetteer.

The Gazetteer is a geographic index the Census Bureau publishes annually.  For
each place it gives:

=====  ========  =====================================================================
  #    Column    What it is
=====  ========  =====================================================================
1      USPS      two-letter state code -- the state filter
2      GEOID     7-digit place id, state FIPS + 5-digit place FIPS, leading zeros
                 significant
3      ANSICODE  8-digit GNIS id; unused here
4      NAME      name **with the legal designator appended**: Abbeville city, Abanda CDP
5      LSAD      numeric code for that designator
6      FUNCSTAT  functional status -- A active government, S statistical entity
7-10   ALAND, AWATER, ALAND_SQMI, AWATER_SQMI -- land/water area, square metres then miles
11-12  INTPTLAT, INTPTLONG -- an internal point guaranteed to fall inside the polygon
=====  ========  =====================================================================

Only columns 1, 2 and 4 are kept; the rest are area and centroid figures this
program has no use for.

Resolution happens up front, on the main thread, before any worker starts.
This is by design, a typo should fail in the first second of a run rather
than halfway through a multi-gigabyte download, and the pipeline cannot know
which states to enqueue until it knows which cities resolved.
"""

from .fetch_utility import FetchUtility
from .logger import get_logger
from .models import GazetteerEntry, RequestedCity, ResolvedCity
from .paths import DataPaths, SourceUrls
from .states import STATE_TABLE
from .text import is_geoid, slugify, split_tabs, strip_designator, to_lower

logger = get_logger(__name__)

# USPS, GEOID, ANSICODE, NAME, ... -- a row with fewer fields than this is a
# truncated download rather than a place, and is skipped.
MINIMUM_GAZETTEER_FIELDS = 4


class GazetteerResolver:
    """Turns requested cities into resolved ones.

    Construction is cheap; the Gazetteer is only read when ``download`` and
    ``parse`` are called.
    """

    def __init__(self, fetcher: FetchUtility, paths: DataPaths, urls: SourceUrls) -> None:
        """Wire up the collaborators.

        Args:
            fetcher: used to download and unpack the Gazetteer archive.
            paths: where the Gazetteer is cached.
            urls: where it is downloaded from.
        """
        self.fetcher = fetcher
        self.paths = paths
        self.urls = urls

    def download(self) -> bool:
        """Fetch and unpack the Gazetteer archive for the configured vintage.

        Returns:
            True if the unpacked Gazetteer text file is on disk afterwards.
        """
        return self.fetcher.fetch_archive(
            self.urls.gazetteer(),
            self.paths.gazetteer_zip,
            self.paths.gazetteer_dir,
            self.paths.gazetteer_text_file.name,
        )

    def parse(self) -> list[GazetteerEntry]:
        """Read the unpacked Gazetteer into memory.

        The whole file is roughly 32,000 rows of three short strings, so
        holding it is a few megabytes and saves re-reading it once per
        requested city.

        Returns:
            Every place in the national Gazetteer; empty on failure.
        """
        entries: list[GazetteerEntry] = []
        path = self.paths.gazetteer_text_file

        try:
            # the Census ships this as Latin-1; a handful of Puerto Rican place
            # names carry accented characters that would abort a strict UTF-8
            # read partway through the file
            with open(path, "r", encoding="latin-1") as gazetteer_file:
                # the first row is the column header, not a place
                next(gazetteer_file, None)

                for line in gazetteer_file:
                    line = line.rstrip("\n")
                    if not line:
                        continue

                    fields = split_tabs(line)
                    if len(fields) < MINIMUM_GAZETTEER_FIELDS:
                        continue

                    entries.append(
                        GazetteerEntry(usps=fields[0], geoid=fields[1], namelsad=fields[3])
                    )
        except OSError as error:
            logger.error("could not open gazetteer %s: %s", path, error)
            return []

        logger.info("gazetteer: %d places from %s", len(entries), path.name)
        return entries

    def resolve_all(self, requested: list[RequestedCity]) -> list[ResolvedCity]:
        """Resolve every requested city, reporting and dropping the rest.

        Args:
            requested: the cities read from the user's file.

        Returns:
            The cities that matched exactly one Gazetteer row, in request
            order.
        """
        resolved: list[ResolvedCity] = []

        entries = self.parse()
        if not entries:
            return resolved

        # One pass over the Gazetteer builds both indexes, so resolving N
        # cities is O(gazetteer + N) 
        by_geoid, by_state_and_name = self._build_indexes(entries)

        # Deduplicated by GEOID.  One place can be requested more than once in
        # a single file -- "Oxford, OH", "Oxford city, OH" and "3959234" are
        # all the same city -- and under a consumer pool that is not merely
        # wasted work: two consumers would run ogr2ogr against the same
        # boundary.shp at the same time and corrupt it.  Deduplicating here is
        # what lets the consumers stay lock-free.
        seen: set[str] = set()

        for city in requested:
            match = self._resolve_one(city, by_geoid, by_state_and_name)
            if match is None:
                continue

            if match.geoid in seen:
                logger.info('"%s" is already requested as %s; skipping the duplicate',
                            city.name, match.slug)
                continue

            seen.add(match.geoid)
            resolved.append(match)

        logger.info(
            "resolved %d of %d requested city/cities", len(resolved), len(requested)
        )
        for city in resolved:
            logger.info("         %s  ->  %s", city, city.slug)

        return resolved

    @staticmethod
    def _build_indexes(
        entries: list[GazetteerEntry],
    ) -> tuple[
        dict[str, list[GazetteerEntry]], dict[tuple[str, str], list[GazetteerEntry]]
    ]:
        """Index the Gazetteer for both ways a city can be requested.

        Values are lists rather than single entries because ambiguity has to
        be *detected*, not silently resolved: two places in one state can share
        a name once the designator is stripped ("Oxford city" and "Oxford
        township"), and the user has to be told rather than guessed at.

        Args:
            entries: the parsed Gazetteer rows.

        Returns:
            A pair of dicts: GEOID -> rows, and (lower state, lower name) ->
            rows.  The second is keyed under both the full Gazetteer spelling
            ("oxford city") and the bare name ("oxford").
        """
        by_geoid: dict[str, list[GazetteerEntry]] = {}
        by_state_and_name: dict[tuple[str, str], list[GazetteerEntry]] = {}

        for entry in entries:
            by_geoid.setdefault(entry.geoid, []).append(entry)

            state = to_lower(entry.usps)
            full = to_lower(entry.namelsad)
            bare = strip_designator(full)

            by_state_and_name.setdefault((state, full), []).append(entry)

            # only when stripping actually changed something, otherwise the
            # same row would be filed twice under one key and every lookup
            # would look ambiguous
            if bare != full:
                by_state_and_name.setdefault((state, bare), []).append(entry)

        return by_geoid, by_state_and_name

    def _resolve_one(
        self,
        city: RequestedCity,
        by_geoid: dict[str, list[GazetteerEntry]],
        by_state_and_name: dict[tuple[str, str], list[GazetteerEntry]],
    ) -> ResolvedCity | None:
        """Match one requested city against the Gazetteer.

        Args:
            city: the request, a name plus state or a bare GEOID.
            by_geoid: GEOID index from _build_indexes().
            by_state_and_name: (state, name) index from _build_indexes().

        Returns:
            The single match, or None when unmatched, ambiguous, or in a state
            with no OSM extract.
        """
        if is_geoid(city.name):
            matches = by_geoid.get(city.name, [])
        else:
            if not city.state:
                logger.warning(
                    '"%s" needs a state, e.g. "%s, OH" (or pass the 7-digit GEOID)',
                    city.name,
                    city.name,
                )
                return None

            key = (to_lower(city.state), to_lower(city.name))
            matches = by_state_and_name.get(key, [])

        if not matches:
            logger.warning(
                '"%s" has no match in the %d Gazetteer',
                city.name,
                self.paths.config.gaz_year,
            )
            return None

        # never guess between two real places -- make the user pick a GEOID
        if len(matches) > 1:
            logger.warning(
                '"%s" is ambiguous, %d matches -- re-run with one of these GEOIDs:',
                city.name,
                len(matches),
            )
            for entry in matches:
                logger.warning(
                    "         %s  %s, %s", entry.geoid, entry.namelsad, entry.usps
                )
            return None

        return self._to_resolved_city(matches[0])

    @staticmethod
    def _to_resolved_city(entry: GazetteerEntry) -> ResolvedCity | None:
        """Promote a matched Gazetteer row to a ResolvedCity.

        Args:
            entry: the single matching row.

        Returns:
            The resolved city, or None when its state has no Geofabrik region
            and therefore no OSM extract to clip the city out of.
        """
        fips = entry.geoid[:2]

        if STATE_TABLE.by_code(int(fips)) is None:
            logger.warning(
                '"%s": state FIPS %s has no Geofabrik region; skipping',
                entry.namelsad,
                fips,
            )
            return None

        return ResolvedCity(
            geoid=entry.geoid,
            namelsad=entry.namelsad,
            usps=entry.usps,
            fips=fips,
            slug=f"{slugify(entry.namelsad)}-{to_lower(entry.usps)}-{entry.geoid}",
        )
