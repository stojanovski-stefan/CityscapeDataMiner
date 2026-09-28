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

Logging setup for the whole program.

The C++ version wrote progress to ``std::cout`` and diagnostics to
``std::cerr``.  That is fine for a single-threaded run, but once a dozen
workers are reporting at once, unattributed interleaved lines are unreadable
and two threads can even interleave *within* one line, because ``operator<<``
is not atomic.

``logging`` solves both problems: a handler serialises each record behind its
own lock, so one call produces one intact line, and ``%(threadName)s`` in the
format makes every line say which worker produced it.  Threads are named
``producer-N`` / ``consumer-N`` by WorkerPool, so the log reads as a trace of
the pipeline rather than as noise.
"""

import logging
import sys
import threading

# Everything in the program logs under this root, so one call configures the
# whole tree and a library that logs on its own (urllib3, via requests) is left
# at its own level rather than being dragged down to DEBUG with us.
#
# Derived from this module's own package rather than hard-coded, so renaming
# the package directory cannot quietly split the logger tree in two and leave
# half the program logging through a root that has no handler.
ROOT_LOGGER_NAME = __name__.rsplit(".", 1)[0]

# Thread name, then the message.  The level is abbreviated to a single
# character: with thousands of lines scrolling past, "W" reads as fast as
# "WARNING" and leaves more room for the path that actually matters.
_LOG_FORMAT = "%(asctime)s %(levelchar)s [%(threadName)-11s] %(message)s"
_TIME_FORMAT = "%H:%M:%S"


class _LevelCharFilter(logging.Filter):
    """Adds a one-character ``levelchar`` field to every record.

    A Filter rather than a custom Formatter: it is the smallest hook that can
    inject a derived field, and it leaves the formatter itself a plain format
    string that is obvious to read and change.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Populate ``record.levelchar`` and keep the record.

        Args:
            record: the record about to be formatted.

        Returns:
            Always True -- this filter annotates, it never discards.
        """
        record.levelchar = record.levelname[0]
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Install the single stderr handler the program logs through.

    Everything goes to stderr, including progress.  The program's real output
    is the file tree it writes, so keeping stdout clear means a caller can
    redirect the log without disturbing anything and can still pipe the
    program's exit status around normally.

    Calling this twice is harmless; the second call replaces the handler rather
    than adding a second one that would double every line.

    Args:
        level: the threshold for this package's logger tree, e.g. logging.INFO.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_TIME_FORMAT))
    handler.addFilter(_LevelCharFilter())

    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # our tree has its own handler, so letting records bubble up to the global
    # root would print each of them a second time
    root.propagate = False

    # the main thread is "MainThread" by default, which is both long and
    # inconsistent with the "producer-0" / "consumer-0" the pools use
    threading.current_thread().name = "main"


def get_logger(name: str) -> logging.Logger:
    """Return the logger a module should use.

    Args:
        name: the module's ``__name__``.

    Returns:
        A child of the package's root logger, so it inherits the one handler
        installed by configure_logging().
    """
    # __name__ is "<package>.foo" for every module in the package, which is
    # already a child of the root; anything else is grafted underneath
    if name == ROOT_LOGGER_NAME or name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)

    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
