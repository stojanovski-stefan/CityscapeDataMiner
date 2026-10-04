# Cityscape Data Miner

Mines the Census and OpenStreetMap sources that `CITYSCAPE` needs, for a list of
US cities, using a **producer–consumer thread pool**.

For each requested city it produces a directory containing the city boundary,
the OSM extract clipped to that boundary, the PUMA geometry, the ACS PUMS
person and housing microdata, and a ready-to-use `modelgen.args` naming all of
them.

---

## Quick start

```shell
# for pitzer
module load miniconda3/25.11.1-py312

# external tools (GDAL and osmium are not pip-installable)
conda create -y -p $HOME/envs/osm --override-channels -c conda-forge osmium-tool gdal
conda activate $HOME/envs/osm

# python dependency
python3 -m pip install -r requirements.txt

# run
python3 main.py --cities ../cities.txt --config config.txt
```

There are only two command-line flags. Everything else lives in the config
file, so a reproducible run is a pair of files you can check in next to its
results.

| Flag | Meaning |
|---|---|
| `--cities FILE` | **required** — the cities to build, one per line |
| `--config FILE` | optional — overrides the built-in defaults; every key is optional |

Exit status is `0` only if **every** resolved city was built completely. A
partial run still leaves the cities it did finish on disk, so the status is the
only thing that tells you the difference.

### The cities file

```
# comments and blank lines are ignored
Oxford, OH        # a name plus its USPS state code
San Francisco, CA
3959234           # or a bare 7-digit Census place GEOID
```

A name on its own is not accepted: "Oxford city" exists in ten states. If a
name matches more than one place in its state, the run reports the candidate
GEOIDs and skips it rather than guessing.

---

## Architecture

The job is embarrassingly data parallel. States do not depend on each other,
and cities do not depend on each other once their state's caches are on disk.
The application encodes a single ordereing constraint (*a city needs its state 
first*) by implementing a produce/consumer multithreading strategy.

```
   [states]                                   [cities]
      |                                          |
      v                                          v
 state_queue ---> producer pool  ---------> city_queue ---> consumer pool ---> disk
                  (producer_threads)                        (consumer_threads)
                  downloads one state's                     builds one city:
                  bulk sources, then                        boundary, OSM clip,
                  enqueues its cities                       PUMA/PUMS, args
```

Both pools are **bounded**: a run over 300 cities creates the same handful of
threads as a run over three, and a worker simply takes the next item when it
finishes one.

**Why two pools instead of one?** The halves are bound by different resources.
Producers wait on the network; consumers drive `osmium` and `ogr2ogr` and wait
on a subprocess. Sizing them separately lets a machine run, say, four downloads
and eight clips at once without either side starving the other. It also means
the first state's cities start building while later states are still
downloading.

**Why it needs no locks.** The configuration, the path tables and the state
table are immutable. Producers only write to state-keyed cache paths and each
state is handled by exactly one producer, because states are deduplicated
before they are enqueued. Consumers only read those caches and only write
inside their own city's directory, and duplicate city requests are collapsed
during resolution. The one piece of shared mutable state, the `Results` tally,
carries its own lock.

---

## Resumable downloads

Every transfer streams into a `.part` file and is moved into place with a
single atomic rename. That gives two guarantees:

* a file that exists in the cache is always **complete** — there is no state in
  which a truncated download is mistaken for a cached one; and
* an interrupted transfer **resumes**. If a `.part` already holds bytes, the
  next attempt sends `Range: bytes=<size>-` and appends, the same thing
  `curl -C -` does. A 1.3 GB state extract that drops at 90% costs 130 MB to
  finish, not 1.3 GB.

Retries resume rather than restart, and a failed transfer deliberately **keeps**
its `.part` so a later run picks up where this one stopped.

---

## Output tree

```
<output_dir>/
├── cache/                shared across cities, persists between runs
│   ├── zips/             every archive downloaded, kept after unpacking
│   ├── gazetteer/        the unpacked national place Gazetteer
│   ├── tiger/            flat — all states unpack into one directory
│   ├── pums/
│   │   ├── p<st>/        lowercase USPS, e.g. pdc/
│   │   └── h<st>/
│   └── osm/              raw statewide .pbf extracts
└── cities/
    └── <slug>/           one per resolved city: name-designator-st-geoid
        ├── boundary.shp  (+ .dbf .shx .prj)
        ├── boundary.geojson
        ├── city.osm.pbf
        ├── city.osm
        ├── puma.shp      (+ .dbf .shx .prj)
        ├── pums_p.csv
        ├── pums_h.csv
        └── modelgen.args
```

---

## Configuration

See `config.txt`, which documents every key with its default. Summary:

| Key | Default | Meaning |
|---|---|---|
| `output_dir` | `./cityscape_data` | root of the generated tree |
| `user_agent` | `cityscape-automation/1.0 (…)` | sent on every request |
| `census` | `https://www2.census.gov` | base URL |
| `geofabrik` | `https://download.geofabrik.de/north-america/us` | base URL |
| `tiger_year` | `2025` | TIGER/Line vintage |
| `gaz_year` | `2024` | Gazetteer vintage |
| `pums_year` | `2023` | ACS PUMS vintage |
| `pums_span` | `5-Year` | `5-Year` or `1-Year` |
| `producer_threads` | `4` | size of the state-download pool |
| `consumer_threads` | `4` | size of the city-build pool |
| `max_retry` | `5` | download attempts before giving up |
| `retry_delay_seconds` | `5` | pause between attempts |

---

## Requirements

* Python 3.10+ 
* `requests`
* `unzip`, `ogr2ogr` (GDAL) and `osmium` (osmium-tool) on `PATH` — all three
  are checked before anything is downloaded
