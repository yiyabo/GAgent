#!/bin/bash -l
# usage: mkenvs.sh name=pkg[,pkg...] ...   (built into /data1/gagent/conda/envs)
export CONDA_ENVS_PATH=/data1/gagent/conda/envs CONDA_PKGS_DIRS=/data1/gagent/conda/pkgs
source /home/wangyichun/miniconda3/etc/profile.d/conda.sh
for spec in "$@"; do
  name=${spec%%=*}; pkgs=${spec#*=}
  if [ -d /data1/gagent/conda/envs/$name/conda-meta ]; then echo "[$(date +%T)] $name exists"; continue; fi
  echo "[$(date +%T)] building $name: $pkgs"
  conda create -y -q -n "$name" --override-channels -c conda-forge -c bioconda ${pkgs//,/ } && echo "[$(date +%T)] OK $name" || echo "[$(date +%T)] FAIL $name"
done
