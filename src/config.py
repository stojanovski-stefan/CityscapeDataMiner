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

Program configuration: the defaults, and the ``key = value`` file that
overrides them.

Keeping the tunables in a file is what lets the command line stay at two
flags.  Every field has a working default, so a config file only needs to name
the handful of settings that actually differ from them.
"""

from dataclasses import dataclass, replace

from .logger import get_logger

logger = get_logger(__name__)

# The only two spans the Census publishes PUMS for.
VALID_PUMS_SPANS = ("5-Year", "1-Year")


@dataclass(frozen=True)
class Config:
    """Every tunable the program has, with the default it falls back to.

    Frozen because the configuration is read once on the main thread and then
    shared, unsynchronised, with every producer and consumer.  Immutability is
    what makes that safe.

    Attributes:
        output_dir: root of the generated file tree; holds cache/ and cities/.
        user_agent: sent on every HTTP request.  census.gov and Geofabrik both
            throttle anonymous bulk traffic, so identifying the program (and a
            contact address) is the polite thing to do.
        census: base URL for census.gov; overridable mostly so a mirror or a
            local test server can be substituted.
        geofabrik: base URL of the directory holding the per-state OSM
            extracts.
        pums_span: "5-Year" or "1-Year".
        tiger_year: TIGER/Line vintage, e.g. 2025.
        gaz_year: Gazetteer vintage, e.g. 2024.
        pums_year: ACS PUMS vintage, e.g. 2023.
        producer_threads: size of the state-download pool.
        consumer_threads: size of the city-build pool.
        max_retry: download attempts before a URL is given up on.
        retry_delay_seconds: pause between those attempts.
    """

    output_dir: str = "./cityscape_data"
    user_agent: str = "cityscape-automation/1.0 (stojansz@miamioh.edu)"
    census: str = "https://www2.census.gov"
    geofabrik: str = "https://download.geofabrik.de/north-america/us"
    pums_span: str = "5-Year"
    tiger_year: int = 2025
    gaz_year: int = 2024
    pums_year: int = 2023

    # --- concurrency -------------------------------------------------------
    # Four and four is a deliberately modest default.  Producers are bound by
    # the remote server, not by this machine, and census.gov and Geofabrik both
    # respond badly to a wide fan-out from one address; four parallel state
    # downloads already hides essentially all of the latency.  Consumers run
    # osmium and ogr2ogr, which are CPU- and disk-heavy, so more of them than
    # the machine has cores only makes them contend.
    producer_threads: int = 4
    consumer_threads: int = 4

    # --- download retry ----------------------------------------------------
    max_retry: int = 5
    retry_delay_seconds: int = 5


class ConfigLoader:
    """Reads a ``key = value`` configuration file into a Config.

    The file format is the same one the C++ version accepted:

    * blank lines and lines whose first non-space character is '#' are ignored;
    * every other line is ``key = value``, with surrounding space trimmed off
      both halves;
    * a key that is absent keeps its default, and says so, because a user who
      meant to set it wants to know it did not take.

    A line with no '=' is malformed.  As in the C++ version, that discards the
    *whole* file and falls back to defaults throughout rather than applying a
    half-read configuration -- a run with silently mixed settings is much
    harder to explain afterwards than one that obviously ignored the file.
    """

    def __init__(self, file_path: str) -> None:
        """Remember which file to read.

        Args:
            file_path: path to the configuration file.
        """
        self.file_path = file_path

    def load(self) -> Config:
        """Parse the file and apply it on top of the defaults.

        Returns:
            A Config; the all-defaults one if the file is missing, unreadable
            or malformed.
        """
        settings = self._read_key_values()
        if settings is None:
            return Config()

        defaults = Config()
        overrides: dict[str, object] = {}

        for key in ("output_dir", "user_agent", "census", "geofabrik"):
            value = self._get_str(settings, key)
            if value is not None:
                overrides[key] = value

        # years are bounded to what the source archives plausibly cover; a
        # typo like 20255 would otherwise become a 404 several seconds later
        for key in ("tiger_year", "gaz_year", "pums_year"):
            value = self._get_int(settings, key, minimum=1990, maximum=2100)
            if value is not None:
                overrides[key] = value

        # a pool of zero would deadlock the pipeline outright: nothing would
        # ever drain the queue it is responsible for
        for key in ("producer_threads", "consumer_threads"):
            value = self._get_int(settings, key, minimum=1, maximum=64)
            if value is not None:
                overrides[key] = value

        value = self._get_int(settings, "max_retry", minimum=1, maximum=100)
        if value is not None:
            overrides["max_retry"] = value

        value = self._get_int(settings, "retry_delay_seconds", minimum=0, maximum=3600)
        if value is not None:
            overrides["retry_delay_seconds"] = value

        span = self._get_pums_span(settings)
        if span is not None:
            overrides["pums_span"] = span

        return replace(defaults, **overrides)  # type: ignore[arg-type]

    def _read_key_values(self) -> dict[str, str] | None:
        """Parse the file into a flat key/value mapping.

        Does not touch Config: this only validates the file's *shape*, leaving
        the meaning of each key to load().

        Returns:
            The key/value pairs, or None if the file could not be read or held
            a malformed line.
        """
        settings: dict[str, str] = {}

        try:
            with open(self.file_path, "r", encoding="utf-8") as config_file:
                lines = config_file.readlines()
        except OSError as error:
            logger.warning(
                "could not open %s (%s), using default values", self.file_path, error
            )
            return None

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()

            # blank line, or a comment
            if not line or line.startswith("#"):
                continue

            key, separator, value = line.partition("=")
            if not separator:
                logger.warning(
                    "malformed line %d in %s: %s -- using default values throughout",
                    line_number,
                    self.file_path,
                    line,
                )
                return None

            settings[key.strip()] = value.strip()

        return settings

    def _get_str(self, settings: dict[str, str], key: str) -> str | None:
        """Fetch a string setting.

        Args:
            settings: the parsed key/value pairs.
            key: the configuration key to look for.

        Returns:
            The value, or None when the key is absent or empty.
        """
        if key not in settings:
            # the user may have only wanted to change a subset of the fields
            logger.warning("no value provided for %s, keeping default", key)
            return None

        value = settings[key]
        if not value:
            logger.warning("empty value for %s, keeping default", key)
            return None

        return value

    def _get_int(
        self, settings: dict[str, str], key: str, minimum: int, maximum: int
    ) -> int | None:
        """Fetch an integer setting, range-checked.

        Args:
            settings: the parsed key/value pairs.
            key: the configuration key to look for.
            minimum: smallest accepted value, inclusive.
            maximum: largest accepted value, inclusive.

        Returns:
            The value, or None when the key is absent, unparsable or out of
            range.
        """
        if key not in settings:
            logger.warning("no value provided for %s, keeping default", key)
            return None

        raw = settings[key]
        try:
            # int() rejects trailing junk such as "2025x" on its own, which is
            # what the C++ had to track parsed-character counts to achieve
            value = int(raw)
        except ValueError:
            logger.warning("invalid integer for %s: %s. Keeping default.", key, raw)
            return None

        if not minimum <= value <= maximum:
            logger.warning(
                "out of range integer for %s: %s (expected %d-%d). Keeping default.",
                key,
                raw,
                minimum,
                maximum,
            )
            return None

        return value

    def _get_pums_span(self, settings: dict[str, str]) -> str | None:
        """Fetch and validate the ``pums_span`` setting.

        Args:
            settings: the parsed key/value pairs.

        Returns:
            "5-Year" or "1-Year", or None when the key is absent or invalid.
        """
        if "pums_span" not in settings:
            logger.warning("no value provided for pums_span, keeping default")
            return None

        span = settings["pums_span"]
        if span not in VALID_PUMS_SPANS:
            logger.warning(
                "invalid pums_span value in config: %s (expected %s). Keeping default.",
                span,
                " or ".join(VALID_PUMS_SPANS),
            )
            return None

        return span


def load_config_file(file_path: str) -> Config:
    """Convenience wrapper mirroring the C++ ``config::loadConfigFile``.

    Args:
        file_path: path to the configuration file.

    Returns:
        The resulting Config.
    """
    return ConfigLoader(file_path).load()
