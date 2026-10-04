#!/bin/bash

#SBATCH --job-name=cityscape-modelgen
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --mail-user=stojansz@miamioh.edu
#SBATCH --mail-type=END,FAIL
#SBATCH -A PMIU0110

# Run modelgen on one mined city.
#
#   ./runCity.sh <city-dir>    one city, by directory
#   sbatch --array=... runCity.sh
#                              one task per line of $CITY_LIST
#
# runAllCities.sh exports MODEL_DIR and CITY_LIST; sbatch passes them through.

export OMP_NUM_THREADS=16

MODELGEN="${MODELGEN:-/fs/ess/PMIU0110/cityscape/model_gen/modelgen}"
MODEL_DIR="${MODEL_DIR:-.}"
CITY_LIST="${CITY_LIST:-citylist.txt}"
SCALE="${SCALE:-16384000}"

CITY_DIR="${1:-$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$CITY_LIST")}"
CITY="$(basename "$CITY_DIR")"

mkdir -p "$MODEL_DIR"

# CityscapeDataMiner does not produce a population raster, as 
# modelgen.args notes in every city directory. Models generated 
# without pop-gis will not include population rings.
"$MODELGEN" \
    --shape "$CITY_DIR/boundary.shp" \
    --dbf "$CITY_DIR/boundary.dbf" \
    --osm-xml "$CITY_DIR/city.osm" \
    --xfig "$MODEL_DIR/$CITY.fig" \
    --scale "$SCALE" \
    --out-model "$MODEL_DIR/${CITY}_model.txt" \
    --pums-h "$CITY_DIR/pums_h.csv" \
    --pums-p "$CITY_DIR/pums_p.csv" \
    --puma-shp "$CITY_DIR/puma.shp" \
    --puma-dbf "$CITY_DIR/puma.dbf" \
    --puma-id-col-dbf PUMACE20 \
    --puma-id-col-pums PUMA \
    --pums-h-cols SERIALNO \
    --pop-gis /fs/ess/PMIU0110/CityscapeDataMiner/cityscape_data/lspop2011/w001001.adf \
    > "$MODEL_DIR/${CITY}_bld_info.txt"
