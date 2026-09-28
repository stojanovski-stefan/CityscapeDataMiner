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

Federal Information Processing Standard (FIPS) number to Geofabrik slug
translation.  Needed to build the OSM extract URL, where the region is spelled
out in words (.../north-america/us/ohio-latest.osm.pbf).  This module is the
only place in the program that derives "ohio" from 39 or "OH".

Usage::

    from src.states import STATE_TABLE

    ohio = STATE_TABLE.by_code(39)          # O(1) dict lookup
    for state in STATE_TABLE:               # iterate in ascending FIPS order
        ...
"""

from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class State:
    """One US state or state-equivalent that Geofabrik publishes an extract for.

    Attributes:
        fips: two-digit numeric code, e.g. 6 for California.  Stored as an int
            because the Gazetteer GEOID's leading two characters are converted
            before lookup; the zero-padded string form lives on ResolvedCity.
        postal_abbreviation: USPS two-letter code, e.g. "CA".
        name: the Geofabrik slug, e.g. "california".
    """

    fips: int
    postal_abbreviation: str
    name: str


# Source data in ascending FIPS order.  Covers the 53 US subregions Geofabrik
# publishes: the 50 states plus DC, Puerto Rico and the US Virgin Islands.  A
# city whose state is absent here has no OSM extract and cannot be built, which
# is why resolution rejects it rather than failing later mid-download.
_LIST: tuple[State, ...] = (
    State(1, "AL", "alabama"),
    State(2, "AK", "alaska"),
    State(4, "AZ", "arizona"),
    State(5, "AR", "arkansas"),
    State(6, "CA", "california"),
    State(8, "CO", "colorado"),
    State(9, "CT", "connecticut"),
    State(10, "DE", "delaware"),
    State(11, "DC", "district-of-columbia"),
    State(12, "FL", "florida"),
    State(13, "GA", "georgia"),
    State(15, "HI", "hawaii"),
    State(16, "ID", "idaho"),
    State(17, "IL", "illinois"),
    State(18, "IN", "indiana"),
    State(19, "IA", "iowa"),
    State(20, "KS", "kansas"),
    State(21, "KY", "kentucky"),
    State(22, "LA", "louisiana"),
    State(23, "ME", "maine"),
    State(24, "MD", "maryland"),
    State(25, "MA", "massachusetts"),
    State(26, "MI", "michigan"),
    State(27, "MN", "minnesota"),
    State(28, "MS", "mississippi"),
    State(29, "MO", "missouri"),
    State(30, "MT", "montana"),
    State(31, "NE", "nebraska"),
    State(32, "NV", "nevada"),
    State(33, "NH", "new-hampshire"),
    State(34, "NJ", "new-jersey"),
    State(35, "NM", "new-mexico"),
    State(36, "NY", "new-york"),
    State(37, "NC", "north-carolina"),
    State(38, "ND", "north-dakota"),
    State(39, "OH", "ohio"),
    State(40, "OK", "oklahoma"),
    State(41, "OR", "oregon"),
    State(42, "PA", "pennsylvania"),
    State(44, "RI", "rhode-island"),
    State(45, "SC", "south-carolina"),
    State(46, "SD", "south-dakota"),
    State(47, "TN", "tennessee"),
    State(48, "TX", "texas"),
    State(49, "UT", "utah"),
    State(50, "VT", "vermont"),
    State(51, "VA", "virginia"),
    State(53, "WA", "washington"),
    State(54, "WV", "west-virginia"),
    State(55, "WI", "wisconsin"),
    State(56, "WY", "wyoming"),
    State(72, "PR", "puerto-rico"),
    State(78, "VI", "us-virgin-islands"),
)


class StateTable:
    """O(1) FIPS lookup over the state list.

    The C++ version built a sparse array indexed by FIPS, since FIPS codes are
    small and dense enough for that to be cheap.  Python has no such incentive,
    so a dict keyed by FIPS gives the same O(1) lookup without the empty slots.

    The table is immutable after construction, which is what makes it safe to
    share between every producer and consumer thread without a lock.
    """

    def __init__(self, states: tuple[State, ...]) -> None:
        """Build the lookup index.

        Args:
            states: the states to index, in ascending FIPS order.
        """
        self._states = states
        self._by_fips: dict[int, State] = {state.fips: state for state in states}

    def by_code(self, code: int) -> State | None:
        """Look up a state by its numeric FIPS code.

        Args:
            code: FIPS number, e.g. 6 for California.

        Returns:
            The matching State, or None when the code is not one of the
            subregions Geofabrik publishes.
        """
        return self._by_fips.get(code)

    def __iter__(self) -> Iterator[State]:
        """Iterate the states in ascending FIPS order."""
        return iter(self._states)

    def __len__(self) -> int:
        """Number of states in the table."""
        return len(self._states)


# The single shared instance.  Read-only, therefore thread-safe.
STATE_TABLE = StateTable(_LIST)
