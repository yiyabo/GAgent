#!/bin/bash -l
# Install one reference database for the native bio_tools runner (620b).
#   usage: mkdbs.sh <genomad|checkv|virsorter2|checkm|bakta|vibrant>
# Downloads into <root>/.staging/<name> and renames to <root>/<name> only when everything succeeded,
# because the runner treats a non-empty <root>/<name> as "installed".
set -euo pipefail
ROOT=${GAGENT_DB_ROOT:-/data1/gagent/databases}
NAME=$1
STAGE=$ROOT/.staging/$NAME
FINAL=$ROOT/$NAME
export CONDA_ENVS_PATH=${CONDA_ENVS_PATH:-/data1/gagent/conda/envs}
export CONDA_PKGS_DIRS=${CONDA_PKGS_DIRS:-/data1/gagent/conda/pkgs}
unset PYTHONPATH PYTHONHOME PERL5LIB
source "${CONDA_BASE:-/home/wangyichun/miniconda3}/etc/profile.d/conda.sh"

if [ -n "$(ls -A "$FINAL" 2>/dev/null)" ]; then echo "[$NAME] already installed at $FINAL"; exit 0; fi
rm -rf "$STAGE"; mkdir -p "$STAGE"
echo "[$(date +%T)] $NAME: downloading into $STAGE"

case $NAME in
  genomad)    conda activate genomad; genomad download-database "$STAGE"; PAYLOAD=$STAGE/genomad_db ;;
  checkv)     conda activate checkv; checkv download_database "$STAGE"; PAYLOAD=$(ls -d "$STAGE"/checkv-db-v* | head -n 1) ;;
  virsorter2) conda activate virsorter2; virsorter setup -d "$STAGE" -j 4 --skip-deps-install; PAYLOAD=$STAGE ;;
  bakta)      conda activate bakta; bakta_db download --output "$STAGE" --type "${BAKTA_DB_TYPE:-light}"; PAYLOAD=$(ls -d "$STAGE"/db* | head -n 1) ;;
  checkm)
    curl -fL --retry 5 -o "$STAGE/checkm_data.tar.gz" https://data.ace.uq.edu.au/public/CheckM_databases/checkm_data_2015_01_16.tar.gz
    mkdir "$STAGE/data"; tar -xzf "$STAGE/checkm_data.tar.gz" -C "$STAGE/data"; rm "$STAGE/checkm_data.tar.gz"; PAYLOAD=$STAGE/data ;;
  vibrant)
    # VIBRANT_setup.py fetches Pfam and KEGG over plain FTP, which fails here; it still gets the VOG part.
    # So: let it run, then fetch the two missing profile sets ourselves (Pfam over https) and press them.
    conda activate vibrant
    download-db.sh "$STAGE" || true
    cd "$STAGE/databases"
    curl -fL --retry 5 -o Pfam-A.hmm.gz https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam32.0/Pfam-A.hmm.gz
    curl -fL --retry 5 -o profiles.tar.gz ftp://ftp.genome.jp/pub/db/kofam/archives/2019-08-10/profiles.tar.gz
    gunzip Pfam-A.hmm.gz && mv Pfam-A.hmm Pfam-A_v32.HMM
    mkdir kegg && tar -xzf profiles.tar.gz -C kegg
    find kegg/profiles -name 'K*.hmm' -print0 | xargs -0 cat > kegg_temp.HMM
    rm -f KEGG_profiles_prokaryotes.HMM*
    hmmfetch -o KEGG_profiles_prokaryotes.HMM -f kegg_temp.HMM profile_names/VIBRANT_kegg_profiles.txt
    hmmpress KEGG_profiles_prokaryotes.HMM; hmmpress Pfam-A_v32.HMM
    rm -rf kegg kegg_temp.HMM profiles.tar.gz
    test -s VOGDB94_phage.HMM.h3f
    cd "$ROOT"; PAYLOAD=$STAGE ;;
  *) echo "unknown database: $NAME" >&2; exit 2 ;;
esac

test -n "$(ls -A "$PAYLOAD")"
mv "$PAYLOAD" "$FINAL"
rm -rf "$STAGE"
chmod -R a+rX "$FINAL"
echo "[$(date +%T)] $NAME: installed at $FINAL ($(du -sh "$FINAL" | cut -f1))"
