#!/bin/bash -l
# Install one reference database for the native bio_tools runner (620b).
#   usage: mkdbs.sh <genomad|checkv|virsorter2|checkm|bakta|vibrant|gtdbtk|iphop>
#   gtdbtk needs about 200 GB free while it runs (110 GB kept); iphop about 500 GB (about 350 GB kept).
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

# fetch_parallel URL DIR PARTS: ranged download into DIR/part.NN, resuming a part from what is already there.
fetch_parallel() {
  local url=$1 dir=$2 parts=$3 size per i start end have
  size=$(curl -sIL "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v}')
  per=$(( (size + parts - 1) / parts ))
  for i in $(seq 0 $((parts - 1))); do
    ( start=$((i * per)); end=$((start + per - 1)); [ "$end" -ge "$size" ] && end=$((size - 1))
      f=$(printf "%s/part.%02d" "$dir" "$i")
      for attempt in $(seq 1 30); do
        have=$(stat -c %s "$f" 2>/dev/null || echo 0)
        [ $((start + have)) -gt "$end" ] && exit 0
        curl -sfL -r "$((start + have))-$end" "$url" >> "$f" || sleep 20
      done
      echo "part $i incomplete" >&2; exit 1 ) &
  done
  wait $(jobs -p)
  [ "$(cat "$dir"/part.* | wc -c)" = "$size" ] || { echo "size mismatch after download" >&2; exit 1; }
}

# fetch_chunk MD5 NAME: one iPHoP chunk, retried until its md5 matches.
fetch_chunk() {
  local sum=$1 name=$2 attempt
  [ -f "$STAGE/chunks/$name" ] && [ "$(md5sum "$STAGE/chunks/$name" | cut -d' ' -f1)" = "$sum" ] && { echo "$name already here"; return 0; }
  for attempt in 1 2 3; do
    curl -fL --retry 20 --retry-delay 30 -C - -s -o "$STAGE/chunks/$name" "$BASE/$name"
    [ "$(md5sum "$STAGE/chunks/$name" | cut -d' ' -f1)" = "$sum" ] && { echo "[$(date +%T)] $name ok"; return 0; }
    rm -f "$STAGE/chunks/$name"
  done
  echo "md5 mismatch for $name" >&2; return 1
}

if [ -n "$(ls -A "$FINAL" 2>/dev/null)" ]; then echo "[$NAME] already installed at $FINAL"; exit 0; fi
# KEEP_STAGE=1 resumes an interrupted run (iphop keeps its checked chunks) instead of starting over.
[ -n "${KEEP_STAGE:-}" ] || rm -rf "$STAGE"
mkdir -p "$STAGE"
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
  gtdbtk)
    # GTDB-Tk 2.3.2 is built for release 214. The server gives about 5 MB/s per connection, so fetch in parallel parts.
    URL=https://data.gtdb.ecogenomic.org/releases/release214/214.0/auxillary_files/gtdbtk_r214_data.tar.gz
    mkdir -p "$STAGE/parts"; rm -rf "$STAGE/data"; mkdir "$STAGE/data"
    fetch_parallel "$URL" "$STAGE/parts" 12
    cat "$STAGE"/parts/part.* | tar -xzf - -C "$STAGE/data" --strip-components=1
    rm -rf "$STAGE/parts"
    PAYLOAD=$STAGE/data ;;
  iphop)
    # iPHoP 1.3.x goes with the Aug23 database (the "latest" one needs iPHoP 1.4). 10 GB chunks, each checked.
    BASE=https://portal.nersc.gov/cfs/m342/iphop/db; V=iPHoP_db_Aug23_rw
    curl -fL --retry 5 -o "$STAGE/$V.md5" "$BASE/$V.tar.gz.md5"
    mkdir -p "$STAGE/chunks"; rm -rf "$STAGE/data"; mkdir "$STAGE/data"
    export BASE STAGE; export -f fetch_chunk
    grep -E "tar\.gz\.[0-9]+$" "$STAGE/$V.md5" | xargs -P 6 -L 1 bash -c 'fetch_chunk "$0" "$1"'
    cat "$STAGE"/chunks/"$V".tar.gz.[0-9]* | tar -xzf - -C "$STAGE/data"
    rm -rf "$STAGE/chunks"
    PAYLOAD=$(dirname "$(find "$STAGE/data" -maxdepth 3 -type d -name db_infos | head -n 1)") ;;
  *) echo "unknown database: $NAME" >&2; exit 2 ;;
esac

test -n "$(ls -A "$PAYLOAD")"
mv "$PAYLOAD" "$FINAL"
rm -rf "$STAGE"
chmod -R a+rX "$FINAL"
echo "[$(date +%T)] $NAME: installed at $FINAL ($(du -sh "$FINAL" | cut -f1))"
