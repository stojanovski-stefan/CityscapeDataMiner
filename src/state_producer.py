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

The producer half of the pipeline: fetching one state's bulk sources.

A producer thread takes a StateWork item off the state queue, downloads the
five statewide archives that every city in that state is carved out of, and
then pushes that state's cities onto the city queue for the consumers.

Downloading per state rather than per city is what makes the whole program
tractable.  The Geofabrik extract alone is 0.3-1.3 GB; fetching it once and
clipping every Ohio city out of it locally is far faster and far kinder to the
upstream servers than hitting Overpass once per city.

Thread safety: each StateWork is handled by exactly one producer, because the
pipeline deduplicates states by FIPS before filling the queue.  Every path
written here embeds that state's FIPS or USPS code, so two producers running
concurrently never touch the same file even though cache/zips and cache/tiger
are flat directories shared by all states.
"""

import queue

from .fetch_utility import FetchUtility
from .logger import get_logger
from .models import ResolvedCity, StateWork
from .paths import DataPaths, SourceUrls
from .states import STATE_TABLE

logger = get_logger(__name__)


class StateProducer:
    """Downloads a state's bulk sources, then releases its cities downstream.

    An instance is shared by every thread in the producer pool; it holds no
    mutable state of its own, only references to collaborators that are
    themselves safe to share.
    """

    def __init__(
        self,
        fetcher: FetchUtility,
        paths: DataPaths,
        urls: SourceUrls,
        city_queue: "queue.Queue[object]",
    ) -> None:
        """Wire up the collaborators and the downstream queue.

        Args:
            fetcher: performs the downloads and unpacking.
            paths: where each archive is cached.
            urls: where each archive is fetched from.
            city_queue: the queue consumers drain; this is the producer's
                output.
        """
        self.fetcher = fetcher
        self.paths = paths
        self.urls = urls
        self.city_queue = city_queue

    def __call__(self, work: StateWork) -> None:
        """Handle one state: download it, then enqueue its cities.

        This is the callable the producer pool runs.  The two steps are
        deliberately fused into one unit of work: a city must never reach a
        consumer before its state's caches are on disk, and the only way to
        guarantee that without a second synchronisation primitive is to have
        the same thread do the download and then the enqueue.

        A state whose download failed still has its cities enqueued. Each city 
        then reports exactly which file it is missing, so one run tells the user
        everything that is wrong rather than only the first thing.

        Args:
            work: the state to fetch, and the cities requested from it.
        """
        logger.info("=== state %s (FIPS %s) ===", work.usps, work.fips)

        if not self.download_state_data(work):
            logger.warning(
                "some sources for %s are missing; its cities may fail below", work.usps
            )

        for city in work.cities:
            self.city_queue.put(city)

        logger.info(
            "state %s ready, %d city/cities queued", work.usps, len(work.cities)
        )

    def download_state_data(self, work: StateWork) -> bool:
        """Fetch every bulk source for one state.

        Five archives in all: TIGER/Line PLACE (city boundaries) and PUMA20
        (public use microdata areas), the ACS PUMS person and housing
        microdata, and the Geofabrik OSM extract.

        Every source is attempted even after an earlier one fails, so a single
        run reports everything missing for a state rather than only the first
        thing.

        Args:
            work: the state to fetch.

        Returns:
            True if every source for the state is on disk afterwards.
        """
        complete = self._download_tiger_place(work)
        complete = self._download_tiger_puma(work) and complete
        complete = self._download_pums(work, "p") and complete
        complete = self._download_pums(work, "h") and complete
        complete = self._download_osm_extract(work) and complete

        return complete

    def _download_tiger_place(self, work: StateWork) -> bool:
        """Fetch the statewide TIGER/Line PLACE layer.

        This is the source of every city boundary: already an ESRI shapefile,
        so a consumer only has to select the one place it wants out of it.

        Args:
            work: the state to fetch.

        Returns:
            True if the unpacked shapefile is on disk afterwards.
        """
        stem = self.paths.place_stem(work.fips)

        return self.fetcher.fetch_archive(
            self.urls.tiger_place(stem),
            self.paths.place_zip(work.fips),
            self.paths.tiger,
            f"{stem}.shp",
        )

    def _download_tiger_puma(self, work: StateWork) -> bool:
        """Fetch the statewide TIGER/Line PUMA layer.

        Args:
            work: the state to fetch.

        Returns:
            True if the unpacked shapefile is on disk afterwards.
        """
        stem = self.paths.puma_stem(work.fips)

        return self.fetcher.fetch_archive(
            self.urls.tiger_puma(stem),
            self.paths.puma_zip(work.fips),
            self.paths.tiger,
            f"{stem}.shp",
        )

    def _download_pums(self, work: StateWork, kind: str) -> bool:
        """Fetch one half of the ACS PUMS microdata for a state.

        Args:
            work: the state to fetch.
            kind: "p" for person records, "h" for housing records.

        Returns:
            True if the unpacked CSV is on disk afterwards.
        """
        return self.fetcher.fetch_archive(
            self.urls.pums(kind, work.lower_usps),
            self.paths.pums_zip(kind, work.lower_usps),
            self.paths.pums_dir(kind, work.lower_usps),
            self.paths.pums_csv_name(kind, work.fips),
        )

    def _download_osm_extract(self, work: StateWork) -> bool:
        """Fetch the statewide Geofabrik OSM extract.

        This is the big one, 0.3-1.3 GB depending on the state, and the reason
        the producers are worth parallelising at all: the transfer is bound by
        the remote server, so several states in flight at once finish in not
        much more than the slowest single one.

        Unlike the other four sources this is not an archive, so there is no
        sentinel to check -- the downloaded file *is* the deliverable, and
        FetchUtility's own cache check covers the re-run case.

        Args:
            work: the state to fetch.

        Returns:
            True if the extract is on disk and non-empty afterwards.
        """
        pbf = self.paths.state_pbf(work.geofabrik_slug)

        self.fetcher.download(self.urls.geofabrik_extract(work.geofabrik_slug), pbf)

        if not pbf.exists() or pbf.stat().st_size == 0:
            logger.error("could not obtain OSM extract for %s", work.geofabrik_slug)
            return False

        return True


def group_cities_by_state(cities: list[ResolvedCity]) -> list[StateWork]:
    """Collapse resolved cities into one work item per state.

    This is the step that makes the producer pool safe.  Without it, two cities
    from Ohio would become two Ohio work items, two producers would download
    tl_2025_39_place.zip into the same path at the same time, and the result
    would be a corrupt archive or a lost race in unzip.  With it, each state
    appears exactly once, so each cached file has exactly one writer.

    Args:
        cities: the resolved cities, in request order.

    Returns:
        One StateWork per distinct state, in first-appearance order, each
        carrying every city requested from that state.
    """
    grouped: dict[str, list[ResolvedCity]] = {}
    for city in cities:
        grouped.setdefault(city.fips, []).append(city)

    work: list[StateWork] = []
    for fips, state_cities in grouped.items():
        # checked during resolution, so the state is known to be present here
        state = STATE_TABLE.by_code(int(fips))
        assert state is not None, f"unresolved FIPS {fips} reached the pipeline"

        work.append(
            StateWork(
                fips=fips,
                usps=state_cities[0].usps,
                geofabrik_slug=state.name,
                cities=tuple(state_cities),
            )
        )

    return work
