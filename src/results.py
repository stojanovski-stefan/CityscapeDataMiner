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

The one piece of shared mutable state in the program: the record of how each
city turned out.

Consumers run concurrently and every one of them writes here, so unlike the
configuration, the paths and the state table -- all of which are immutable and
therefore free to share -- this needs a lock.  The lock is held only for the
few instructions it takes to update a dict, so it is never a bottleneck even
with a wide consumer pool.
"""

import threading

from .logger import get_logger

logger = get_logger(__name__)


class Results:
    """Thread-safe tally of which cities were built and which were not."""

    def __init__(self) -> None:
        """Start with an empty tally."""
        self._lock = threading.Lock()
        self._outcomes: dict[str, bool] = {}

    def record(self, slug: str, ok: bool) -> None:
        """Record one city's outcome.

        Args:
            slug: the city's directory name, e.g. "oxford-city-oh-3959234".
            ok: True if every deliverable was produced.
        """
        with self._lock:
            self._outcomes[slug] = ok

    def mark_missing_as_failed(self, expected_slugs: list[str]) -> None:
        """Fail any city that was never recorded at all.

        A city reaches this state only when something went wrong outside the
        normal failure path -- a producer thread died before enqueueing it, or
        a consumer raised before recording.  Without this, such a city would
        vanish from the summary and the run would exit zero despite having
        silently skipped work.

        Args:
            expected_slugs: every city the run was supposed to build.
        """
        with self._lock:
            for slug in expected_slugs:
                if slug not in self._outcomes:
                    logger.error("%s was never built (worker did not report)", slug)
                    self._outcomes[slug] = False

    @property
    def succeeded(self) -> list[str]:
        """Slugs of the cities that were built completely, sorted."""
        with self._lock:
            return sorted(slug for slug, ok in self._outcomes.items() if ok)

    @property
    def failed(self) -> list[str]:
        """Slugs of the cities that were not built completely, sorted."""
        with self._lock:
            return sorted(slug for slug, ok in self._outcomes.items() if not ok)

    @property
    def all_succeeded(self) -> bool:
        """True when every recorded city was built completely."""
        with self._lock:
            return all(self._outcomes.values())
