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

The value types that travel between the stages of the pipeline.

All of them are frozen dataclasses.  That is deliberate: these objects are
handed across thread boundaries through the work queues, and an immutable
payload cannot be mutated by one worker while another is reading it, so none of
them needs a lock.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestedCity:
    """One line of the user's cities file, before it has been resolved.

    Attributes:
        name: the city name as typed, or a bare 7-digit GEOID.
        state: the USPS state abbreviation, empty when ``name`` is a GEOID.
    """

    name: str
    state: str


@dataclass(frozen=True)
class GazetteerEntry:
    """One row of the national place Gazetteer.

    Reduced to the three columns this program actually resolves against; the
    full file is roughly 32,000 of these.

    Attributes:
        usps: two-letter state code, e.g. "OH".
        geoid: 7-digit place id, e.g. "3959234".
        namelsad: name with its legal designator, e.g. "Oxford city".
    """

    usps: str
    geoid: str
    namelsad: str


@dataclass(frozen=True)
class ResolvedCity:
    """A user request after it has matched exactly one Gazetteer row.

    Everything downstream -- URLs, directory names, modelgen arguments -- is
    derived from these five fields.

    Attributes:
        geoid: 7-digit place id, e.g. "3959234".
        namelsad: "Oxford city".
        usps: "OH".
        fips: first two digits of the GEOID, e.g. "39".  Kept as a string
            because TIGER file names embed the zero-padded form ("tl_2025_06_").
        slug: "oxford-city-oh-3959234", the per-city output directory name.
    """

    geoid: str
    namelsad: str
    usps: str
    fips: str
    slug: str

    @property
    def lower_usps(self) -> str:
        """The USPS code in lower case, as the PUMS URLs and paths spell it."""
        return self.usps.lower()

    def __str__(self) -> str:
        """Human-readable identification used throughout the log."""
        return f"{self.namelsad}, {self.usps} ({self.geoid})"


@dataclass(frozen=True)
class StateWork:
    """One unit of producer work: a state, plus every city requested from it.

    The cities are carried along so that the producer that downloads a state's
    bulk sources is also the thing that releases that state's cities onto the
    consumer queue.  That coupling is what guarantees a consumer never sees a
    city whose caches are not yet on disk.

    Attributes:
        fips: two-digit state FIPS code, e.g. "39".
        usps: two-letter state code, e.g. "OH".
        geofabrik_slug: the OSM extract region name, e.g. "ohio".
        cities: every resolved city belonging to this state.
    """

    fips: str
    usps: str
    geofabrik_slug: str
    cities: tuple[ResolvedCity, ...]

    @property
    def lower_usps(self) -> str:
        """The USPS code in lower case, as the PUMS URLs and paths spell it."""
        return self.usps.lower()
