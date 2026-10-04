#!/bin/bash

#SBATCH --job-name=data-miner
#SBATCH --time=02:00:00
#SBATCH --mem=16G
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mail-user=stojansz@miamioh.edu
#SBATCH --mail-type=END,FAIL
#SBATCH -A PMIU0110

# !add --config flag if not using default configuration
python3 main.py --cities ./cities.txt>data_collection.txt