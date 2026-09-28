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

CityscapeAutomation: mine the Census and OpenStreetMap sources that modelgen
needs, for a list of US cities.

The package is arranged so that each module owns one responsibility:

=================  ============================================================
Module             Responsibility
=================  ============================================================
arg_parser         the two command-line flags
config             defaults, and the file that overrides them
logger             the single thread-aware log handler
text               string helpers used during city resolution
models             the immutable values passed between pipeline stages
states             FIPS -> Geofabrik region translation
paths              where every file lives, locally (DataPaths) and remotely
                   (SourceUrls)
fetch_utility      resumable HTTP downloads, unzipping, external tools
city_file          reading the user's list of requested cities
gazetteer          downloading, parsing and resolving against the Gazetteer
state_producer     the producer half: one state's bulk sources
city_consumer      the consumer half: one city's deliverables
thread_pool        a bounded pool of workers draining a queue
results            the thread-safe tally of what was built
pipeline           wires the queues and the two pools together
=================  ============================================================
"""

__version__ = "1.0.0"
