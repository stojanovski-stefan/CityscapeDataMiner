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

Entry point.

Parse the only two flags, load the configuration, run the
pipeline, and let the exit status say whether every city came out complete. A
partial run will still leave the cities it did finish on disk.

Usage::

    # --config optional, loads default configuration if one is not given.
    python3 main.py --cities cities.txt --config config.txt 

"""

import sys

from src.arg_parser import ArgParser
from src.config import Config, load_config_file
from src.logger import configure_logging, get_logger
from src.pipeline import Pipeline

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Run the program.

    Args:
        argv: command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 if every resolved city was built completely, 1 otherwise.
    """
    # before anything that might log, so no record is written to a logger that
    # has not been given its handler yet
    configure_logging()

    # Record command line arguments
    arguments = ArgParser().parse(argv)

    # load config file if given, use default if otherwise
    config = (
        load_config_file(arguments.config_file_path)
        if arguments.config_file_path
        else Config()
    )

    try:
        completed = Pipeline(arguments.city_file_path, config).run()
    except KeyboardInterrupt:
        # Worth catching explicitly, a partial download survives as a .part
        # file, so the next run resumes rather than starting over, and the user
        # should be told that rather than left to assume the data was lost.
        logger.warning("interrupted -- partial downloads are kept and will resume")
        return 130

    return 0 if completed else 1


if __name__ == "__main__":
    sys.exit(main())
