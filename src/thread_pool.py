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

A bounded pool of worker threads draining one queue.

This is the mechanism both halves of the pipeline are built from.  The point of
a *bounded* pool is that the number of threads is fixed by configuration rather
than by the size of the input: a run over 300 cities creates the same handful
of workers as a run over three, and each worker simply moves on to the next
item when it finishes one.  Spawning a thread per city would put hundreds of
concurrent osmium processes on one machine and hundreds of concurrent
connections on one server, which is slower than doing it properly and rude
besides.

Shutdown uses the classic sentinel, or "poison pill", protocol.  ``join()``
puts exactly one sentinel per worker onto the queue; because a worker exits the
moment it takes one, and every worker takes exactly one, all of them stop and
none of them can leave real work behind it in the queue.
"""

import queue
import threading
from typing import Any, Callable

from .logger import get_logger

logger = get_logger(__name__)


class _Sentinel:
    """
    A dedicated class rather than None or a bare object() so that a stray
    sentinel in a log or a traceback identifies itself.
    """

    def __repr__(self) -> str:
        """Identify the sentinel in diagnostics."""
        return "<shutdown sentinel>"


# The single shared instance.  Workers compare against it by identity, so no
# real work item can ever be mistaken for it.
SENTINEL = _Sentinel()


class WorkerPool:
    """A fixed number of named threads, all pulling from the same queue.

    Attributes:
        name: prefix for the worker thread names, e.g. "producer".
        worker_count: how many threads the pool runs.
        work_queue: the queue the workers drain.
        handler: called with each item taken off the queue.
    """

    def __init__(
        self,
        name: str,
        worker_count: int,
        work_queue: "queue.Queue[Any]",
        handler: Callable[[Any], None],
    ) -> None:
        """Create the pool without starting it.

        Args:
            name: prefix for the worker thread names.
            worker_count: how many threads to run; at least one.
            work_queue: the queue the workers drain.
            handler: called with each item taken off the queue.
        """
        self.name = name
        self.worker_count = max(1, worker_count)
        self.work_queue = work_queue
        self.handler = handler
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Spawn the workers.

        They begin draining the queue immediately; an empty queue simply blocks
        them, which is exactly what lets the consumer pool be started before
        any producer has finished.
        """
        for index in range(self.worker_count):
            thread = threading.Thread(
                target=self._run,
                name=f"{self.name}-{index}",
                # not daemon: a worker holding a half-written .part file or a
                # running osmium should finish or fail on its own terms, not be
                # torn down mid-write by interpreter shutdown
                daemon=False,
            )
            thread.start()
            self._threads.append(thread)

        logger.info("started %d %s thread(s)", self.worker_count, self.name)

    def join(self) -> None:
        """Signal shutdown and wait for every worker to finish.

        One sentinel per worker is enqueued *behind* whatever real work is
        still in the queue, so this drains the backlog before it stops
        anything; it is a graceful finish, not a cancellation.
        """
        for _ in self._threads:
            self.work_queue.put(SENTINEL)

        for thread in self._threads:
            thread.join()

        logger.info("all %s thread(s) finished", self.name)

    def _run(self) -> None:
        """The worker loop: take an item, handle it, repeat until poisoned."""
        while True:
            item = self.work_queue.get()

            try:
                if item is SENTINEL:
                    return

                self.handler(item)
            except Exception:
                # One bad item must not silently shrink the pool.  Logging the
                # traceback and carrying on keeps the remaining work moving,
                # and the city is still counted as failed because the pipeline
                # reconciles the recorded outcomes against what it expected.
                logger.exception("unhandled error while processing %r", item)
            finally:
                # paired with get() so a caller could join() the queue itself;
                # in the finally block so an exception cannot leave the count
                # permanently short
                self.work_queue.task_done()
