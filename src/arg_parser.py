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

Command-line parsing.

The surface is deliberately two flags wide.  Everything else the program can be
told lives in the configuration file, which keeps an invocation short enough to
type from memory and keeps a *reproducible* run down to a pair of files that
can be checked into a repository alongside its results.
"""

import argparse
from dataclasses import dataclass

PROGRAM_NAME = "CityscapeAutomation"

DESCRIPTION = """\
Mine the Census and OpenStreetMap sources modelgen needs, for every city in a
list, using a producer-consumer thread pool: producers fetch each state's bulk
data once, consumers carve the individual cities out of it.
"""

EPILOG = """\
examples:
  python3 main.py --cities cities.txt
  python3 main.py --cities cities.txt --config config.txt
"""


@dataclass(frozen=True)
class Arguments:
    """The parsed command line.

    Attributes:
        city_file_path: path to the file listing the requested cities.
        config_file_path: path to the configuration file; empty when the user
            did not pass one, in which case every setting keeps its default.
    """

    city_file_path: str
    config_file_path: str


class ArgParser:
    """Thin wrapper around argparse

    A class rather than a bare function so the parser can be built once and
    reused, and so ``--help`` text lives next to the fields it describes.
    """

    def __init__(self) -> None:
        """Build the parser."""
        self.parser = argparse.ArgumentParser(
            prog=PROGRAM_NAME,
            description=DESCRIPTION,
            epilog=EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        self.parser.add_argument(
            "--cities",
            dest="city_file_path",
            required=True,
            metavar="FILE",
            help=(
                "read cities from FILE, one per line: 'Name, ST' or a bare "
                "7-digit GEOID ('#' starts a comment)"
            ),
        )

        self.parser.add_argument(
            "--config",
            dest="config_file_path",
            default="",
            metavar="FILE",
            help=(
                "configuration file of 'key = value' lines that overrides the "
                "built-in defaults; every key is optional"
            ),
        )

    def parse(self, argv: list[str] | None = None) -> Arguments:
        """Parse the command line.

        Args:
            argv: the arguments to parse, or None to read ``sys.argv``.

        Returns:
            The parsed arguments.

        Raises:
            SystemExit: if the arguments are invalid or --help was given;
                argparse has already printed the reason at that point.
        """
        namespace = self.parser.parse_args(argv)

        return Arguments(
            city_file_path=namespace.city_file_path,
            config_file_path=namespace.config_file_path,
        )
