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

The pipeline starts as a serial prologue, then two thread pools passing work between
them.

::

    [states]                          [cities]
       |                                 |
       v                                 v
    state_queue  --> producer pool --> city_queue --> consumer pool --> disk
                     (N threads)                       (M threads)
                     download a state                  build a city
                     enqueue its cities

The work is embarrassingly data parallel: states do not depend on each other,
and cities do not depend on each other once their state's caches are on disk.
The only ordering constraint in the whole program is that one -- a city needs
its state first -- and the producer/consumer split encodes exactly it and
nothing more.

The two halves are bound by different resources.  Producers wait on the network
and spend almost all of their time waiting on a socket; consumers drive osmium
and ogr2ogr and spend almost all of theirs blocked on a subprocess. Sizing them
separately lets a machine run, say, four downloads and eight clips at once 
without either side starving the other. It also means the first state's cities 
start building while later states are still downloading, rather than after.
"""

import queue

from .city_consumer import CityConsumer
from .city_file import CityFileReader
from .config import Config
from .fetch_utility import FetchUtility
from .gazetteer import GazetteerResolver
from .logger import get_logger
from .models import ResolvedCity
from .paths import DataPaths, SourceUrls
from .results import Results
from .state_producer import StateProducer, group_cities_by_state
from .thread_pool import WorkerPool

logger = get_logger(__name__)

# External tools the derivation steps shell out to.  Checked before anything is
# downloaded, because discovering that osmium is missing after pulling down two
# gigabytes on its behalf is a waste of time and compute resources.
REQUIRED_TOOLS = (
    ("unzip", "Install it with your package manager."),
    ("ogr2ogr", "Part of GDAL: conda install -c conda-forge gdal"),
    ("osmium", "conda install -c conda-forge osmium-tool"),
)


class Pipeline:
    """Runs the whole job: resolve, download per state, build per city."""

    def __init__(self, city_file_path: str, config: Config) -> None:
        """Assemble the collaborators from the two command-line inputs.

        Args:
            city_file_path: path to the user's cities file.
            config: the loaded configuration.
        """
        self.config = config
        self.city_file_path = city_file_path

        self.paths = DataPaths(config)
        self.urls = SourceUrls(config)
        self.fetcher = FetchUtility(config)
        self.results = Results()

    def run(self) -> bool:
        """Execute the pipeline end to end.

        Returns:
            True if every resolved city was built without error.
        """
        if not self._check_tools():
            return False

        cities = self._prepare()
        if not cities:
            return False

        self._run_pools(cities)

        # A city that no worker reported on at all -- because a thread died in
        # a way the pool could not attribute -- must not quietly disappear from
        # the tally.
        self.results.mark_missing_as_failed([city.slug for city in cities])

        return self._report(len(cities))

    # ------------------------------------------------------------------
    # serial
    # ------------------------------------------------------------------

    def _check_tools(self) -> bool:
        """Confirm every external tool is on PATH.

        Returns:
            True if all of them were found.
        """
        # not short-circuited: a user missing two tools should be told about
        # both in one run rather than one per attempt
        found = [self.fetcher.have_command(tool, hint) for tool, hint in REQUIRED_TOOLS]
        return all(found)

    def _prepare(self) -> list[ResolvedCity]:
        """Create the tree, fetch the Gazetteer, and resolve every city.

        This part is serial: the set of states to download is not
        known until the cities resolve, and resolving needs the Gazetteer.
        Doing it up front also means a typo fails in the first second of a run
        rather than halfway through a multi-gigabyte download.

        Returns:
            The resolved cities, or an empty list if there is nothing to do.
        """
        self.paths.setup_data_file_tree()

        resolver = GazetteerResolver(self.fetcher, self.paths, self.urls)
        if not resolver.download():
            logger.error("could not obtain the Gazetteer; nothing can be resolved")
            return []

        requested = CityFileReader(self.city_file_path).read()
        if not requested:
            logger.error("no cities requested; nothing to do")
            return []

        cities = resolver.resolve_all(requested)
        if not cities:
            logger.error("no cities resolved; nothing to do")
            return []

        return cities

    # ------------------------------------------------------------------
    # parallel
    # ------------------------------------------------------------------

    def _run_pools(self, cities: list[ResolvedCity]) -> None:
        """Start both pools, feed them, and wait for them to drain.

        The ordering here is the whole correctness argument, so it is worth
        stating explicitly:

        1. States are deduplicated before they are enqueued, so each state's
           cached archives have exactly one writer and the producers need no
           lock over the shared cache directories.
        2. The state queue is filled completely *before* the producers start,
           so no producer can ever observe an empty queue and mistake it for
           the end of the work.
        3. The consumers start first.  They block on an empty city queue and
           begin the moment the first producer finishes a state, which is what
           overlaps building with downloading.
        4. The producers are joined before a single consumer sentinel is
           enqueued.  That is what makes the shutdown safe: a consumer cannot
           see the poison pill while a producer still has cities to push,
           because the pill is not created until every producer has stopped.

        Args:
            cities: the resolved cities to build.
        """
        state_work = group_cities_by_state(cities)

        state_queue: "queue.Queue[object]" = queue.Queue()
        city_queue: "queue.Queue[object]" = queue.Queue()

        # (2) fill the state queue before anything drains it
        for work in state_work:
            state_queue.put(work)

        logger.info(
            "pipeline: %d state(s) -> %d city/cities, %d producer(s), %d consumer(s)",
            len(state_work),
            len(cities),
            self.config.producer_threads,
            self.config.consumer_threads,
        )

        producers = WorkerPool(
            "producer",
            self.config.producer_threads,
            state_queue,
            StateProducer(self.fetcher, self.paths, self.urls, city_queue),
        )
        consumers = WorkerPool(
            "consumer",
            self.config.consumer_threads,
            city_queue,
            CityConsumer(self.fetcher, self.paths, self.results),
        )

        # (3) consumers first, so they are already waiting when work appears
        consumers.start()
        producers.start()

        # (4) every producer stops before any consumer is allowed to
        producers.join()
        consumers.join()

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------

    def _report(self, requested_count: int) -> bool:
        """Log the outcome of the run.

        Args:
            requested_count: how many cities the run set out to build.

        Returns:
            True if every city was built completely.
        """
        succeeded = self.results.succeeded
        failed = self.results.failed

        logger.info(
            "done -- %d of %d city/cities complete under %s",
            len(succeeded),
            requested_count,
            self.paths.cities,
        )

        for slug in failed:
            logger.error("         incomplete: %s", slug)

        return not failed
