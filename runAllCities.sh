#!/bin/bash

# Submit one modelgen job per city in <outdir>/cities, as a throttled job array.
#
#   ./runAllCities.sh [outdir]      default: ./cityscape_data
#
# Models land in <outdir>/models_<hash>, where <hash> is the short commit of the
# cityscape checkout modelgen was built from -- so a set of models says which
# modelgen made it.

OUT_DIR="$(readlink -f "${1:-./cityscape_data}")"
CITYSCAPE_DIR="${CITYSCAPE_DIR:-/fs/ess/PMIU0110/cityscape}"

HASH="$(git -C "$CITYSCAPE_DIR" rev-parse --short HEAD)"

# Exported so the array tasks inherit them: sbatch defaults to --export=ALL.
export MODEL_DIR="$OUT_DIR/models_$HASH"
export CITY_LIST="$MODEL_DIR/citylist.txt"

mkdir -p "$MODEL_DIR"
ls -d "$OUT_DIR"/cities/*/ > "$CITY_LIST"
N=$(wc -l < "$CITY_LIST")

# %4 caps concurrency: each task reads ~1.5 GB off shared storage.
sbatch --array=0-$((N - 1))%4 \
       --output="$MODEL_DIR/%x-%A_%a.out" \
       "$(dirname "$0")/runCity.sh"
