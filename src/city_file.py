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

Reading the user's list of requested cities.

The file format is unchanged from the C++ version::

    # comments and blank lines are ignored
    Oxford, OH          a name plus its USPS state code
    3959234             a bare 7-digit Census place GEOID

A name on its own is not accepted: "Oxford city" exists in ten states, so a
name without a state cannot identify a place.  That is caught here as a
malformed line only when there is no comma at all; a name with a comma but no
recognisable state is caught later, during Gazetteer resolution, where the
error message can list the candidates.
"""

from .logger import get_logger
from .models import RequestedCity
from .text import is_geoid

logger = get_logger(__name__)


class CityFileReader:
    """Parses the cities file into RequestedCity records."""

    def __init__(self, file_path: str) -> None:
        """Remember which file to read.

        Args:
            file_path: path to the cities file.
        """
        self.file_path = file_path

    def read(self) -> list[RequestedCity]:
        """Read and parse the file.

        Returns:
            One RequestedCity per usable line, in file order.  Empty if the
            file could not be opened; malformed lines are reported and skipped
            rather than aborting the run.
        """
        cities: list[RequestedCity] = []

        try:
            with open(self.file_path, "r", encoding="utf-8") as city_file:
                lines = city_file.readlines()
        except OSError as error:
            logger.error("could not open %s: %s", self.file_path, error)
            return cities

        for line_number, raw_line in enumerate(lines, start=1):
            city = self._parse_line(raw_line, line_number)
            if city is not None:
                cities.append(city)

        logger.info("read %d requested city/cities from %s", len(cities), self.file_path)
        return cities

    def _parse_line(self, raw_line: str, line_number: int) -> RequestedCity | None:
        """Turn one line into a RequestedCity.

        Args:
            raw_line: the line as read, including its terminator.
            line_number: 1-based line number, for diagnostics.

        Returns:
            The parsed city, or None for a blank line, a comment, or a line
            that could not be understood.
        """
        line = raw_line.strip()

        # blank line, or a comment
        if not line or line.startswith("#"):
            return None

        name, separator, state = line.partition(",")
        if not separator:
            # a bare GEOID identifies a place outright, so no state is required
            if is_geoid(line):
                return RequestedCity(name=line, state="")

            logger.warning(
                "skipping malformed line %d in %s: %s", line_number, self.file_path, line
            )
            return None

        # keep the spaces in the middle of a name, for cities like
        # "San Francisco"; only the ends are trimmed
        return RequestedCity(name=name.strip(), state=state.strip())
