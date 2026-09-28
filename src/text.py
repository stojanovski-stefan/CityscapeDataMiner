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

Small text helpers used while resolving a requested city against the Census
Gazetteer.  These are the Python counterpart of the anonymous namespace at the
top of the C++ ``CityDataMiner.cpp``: pure functions, no state, no I/O.
"""

import re

# The trailing legal/statistical designator the Gazetteer appends to every NAME
# ("Oxford city", "Juneau city and borough").  A user types "Oxford", so the
# designator is stripped before comparing.  Longest match wins: "city and
# borough" must not be shortened to "borough" by a careless scan.
DESIGNATORS: tuple[str, ...] = (
    "city and borough",
    "consolidated government",
    "metro government",
    "metropolitan government",
    "unified government",
    "urban county",
    "zona urbana",
    "municipality",
    "township",
    "plantation",
    "comunidad",
    "village",
    "borough",
    "city",
    "town",
    "cdp",
)

# Runs of anything that is not a lowercase letter or digit collapse to a single
# '-' when slugifying.  Pre-compiled because slugify() runs once per resolved
# city and the pattern never changes.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# A Census place GEOID is exactly seven digits: two of state FIPS followed by
# five of place FIPS.  Leading zeros are significant, so this is a string test
# and never an int conversion.
_GEOID = re.compile(r"^[0-9]{7}$")


def to_lower(text: str) -> str:
    """ASCII lower-casing.

    The Gazetteer is plain ASCII, so a byte-wise fold is enough; ``str.lower()``
    is used directly rather than any locale-aware folding.

    Args:
        text: the string to fold.

    Returns:
        A lower-cased copy of ``text``.
    """
    return text.lower()


def slugify(text: str) -> str:
    """Turn a display name into a filesystem-safe slug.

    Lower case, every run of non-alphanumeric characters collapsed to a single
    '-', no leading or trailing '-'.  "Oxford city" becomes "oxford-city".

    Args:
        text: the name to slugify.

    Returns:
        The slug, which may be empty if ``text`` held no alphanumerics.
    """
    return _NON_ALNUM.sub("-", text.lower()).strip("-")


def strip_designator(lowered_name: str) -> str:
    """Drop the trailing legal/statistical designator from a Gazetteer NAME.

    This is what lets a user's "oxford" match the Gazetteer's "oxford city".
    The longest designator that fits is the one removed, so "juneau city and
    borough" loses the whole three-word designator rather than just "borough".

    Args:
        lowered_name: an already lower-cased NAME.

    Returns:
        The name without its designator; unchanged if it has none.
    """
    longest = ""

    for designator in DESIGNATORS:
        # the designator is always its own word, hence the leading space
        suffix = " " + designator
        if len(lowered_name) > len(suffix) and lowered_name.endswith(suffix):
            if len(designator) > len(longest):
                longest = designator

    if not longest:
        return lowered_name

    # +1 also removes the space that separated the name from the designator
    return lowered_name[: -(len(longest) + 1)]


def is_geoid(text: str) -> bool:
    """True when a string is exactly seven digits, the shape of a place GEOID.

    Such an entry is unambiguous on its own and needs no state to resolve.

    Args:
        text: candidate string.

    Returns:
        True if ``text`` is a 7-digit GEOID.
    """
    return _GEOID.match(text) is not None


def split_tabs(line: str) -> list[str]:
    """Split one tab-delimited Gazetteer line into trimmed fields.

    The Census pads its columns with spaces to a fixed width, so every field
    needs trimming before it can be compared against anything.

    Args:
        line: one line of the Gazetteer, without its line terminator.

    Returns:
        The line's fields, in order.
    """
    return [field.strip() for field in line.split("\t")]

