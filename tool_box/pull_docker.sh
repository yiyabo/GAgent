#!/usr/bin/env bash
set -euo pipefail

# =========================
# Paper-aligned tool pulls
# =========================
# Requirements: docker, curl, python3 (or python)
# This script:
#   1) pulls fixed-tag images (DockerHub / StaPH-B / etc.)
#   2) resolves BioContainers (quay.io/biocontainers) tags that match a given version and pulls them
#
# Notes:
# - "metaFlye v2.9.2-b1786" is Flye in metagenome mode; we pull Flye 2.9.2.
# - VirSorter2 is usually packaged as "virsorter" in Bioconda/BioContainers; we pull version 2.2.4.
# - AliTV is only for visualization; not strictly required for the main pipeline (optional section at bottom).

DOCKER_BIN="${DOCKER_BIN:-docker}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }
}

need_cmd "${DOCKER_BIN}"
need_cmd curl

PY_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
else
  echo "ERROR: need python3 (or python) to parse Quay API JSON." >&2
  exit 1
fi

log() { echo -e "[pull] $*"; }

pull_image() {
  local img="$1"
  log "docker pull ${img}"
  "${DOCKER_BIN}" pull "${img}"
}

# Resolve a BioContainers tag on quay.io that matches a given version.
# It queries Quay API pages and picks the "best" matching tag.
resolve_quay_biocontainers_tag() {
  local repo="$1"
  local ver="$2"

  "${PY_BIN}" - "$repo" "$ver" <<'PY'
import sys, json, re, urllib.request, urllib.error

repo, ver = sys.argv[1], sys.argv[2]

def fetch(page: int):
    url = f"https://quay.io/api/v1/repository/biocontainers/{repo}/tag/?onlyActiveTags=true&limit=100&page={page}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))

def is_match(tag: str) -> bool:
    # Accept:
    #   1) "2.3.0--..."  (common biocontainers)
    #   2) "v2.3.0--..." (sometimes)
    #   3) exact "2.3.0"
    # Also tolerate suffixes like "-r1175" in the paper by matching prefix only.
    patterns = [
        rf"^v?{re.escape(ver)}(\b|--|[-_].*)",
        rf"^v?{re.escape(ver.split('-')[0])}(\b|--|[-_].*)",  # drop paper build suffix
    ]
    return any(re.match(p, tag) for p in patterns)

matches = []
page = 1
while True:
    try:
        data = fetch(page)
    except urllib.error.HTTPError as e:
        # repo doesn't exist or blocked
        sys.exit(2)
    except Exception:
        sys.exit(3)

    for t in data.get("tags", []):
        name = t.get("name", "")
        if name and is_match(name):
            matches.append(name)

    if not data.get("has_additional"):
        break
    page += 1
    if page > 50:
        break

if not matches:
    sys.exit(4)

# Prefer tags that include conda build ("--"), then lexicographically max.
def score(tag: str):
    return (1 if "--" in tag else 0, len(tag), tag)

best = sorted(matches, key=score)[-1]
print(best)
PY
}

pull_biocontainers() {
  local repo="$1"
  local ver="$2"
  local tag=""

  if tag="$(resolve_quay_biocontainers_tag "$repo" "$ver" 2>/dev/null)"; then
    pull_image "quay.io/biocontainers/${repo}:${tag}"
  else
    echo "WARN: could not resolve quay.io/biocontainers/${repo} for version ${ver}. Skipping." >&2
    return 0
  fi
}

# ---- 1) Fixed-tag images (direct pulls) ----
# Versions aligned to the paper where specified:
# Nextflow v22.10.5; Dorado v0.5.3; Bowtie2 v2.5.4; SAMtools v1.21 and v1.9; BLAST+ v2.2.31
FIXED_IMAGES=(
  "nextflow/nextflow:22.10.5"
  "genomicpariscentre/dorado:0.5.3"

  # aligners & utils
  "staphb/minimap2:2.26"
  "staphb/flye:2.9.2"          # metaFlye mode uses Flye
  "staphb/seqtk:1.4"
  "staphb/bwa:0.7.17"
  "staphb/bowtie2:2.5.4"

  # samtools both versions used in paper
  "staphb/samtools:1.21"
  "staphb/samtools:1.9"

  # BLAST+ (paper uses blast+ v2.2.31)
  "biocontainers/blast:2.2.31"
)

log "== Pulling fixed-tag images =="
for img in "${FIXED_IMAGES[@]}"; do
  pull_image "$img"
done

# ---- 2) BioContainers (quay) images with auto tag-resolve ----
# Tool versions from the paper Methods.
# If a repo name is different in BioContainers, resolution will fail and you'll see WARN.
log "== Pulling BioContainers (quay.io) images with version tag auto-resolve =="

# Core short-read pipeline
pull_biocontainers "htstream"       "1.3.3"
pull_biocontainers "trim-galore"    "0.6.7"

# Assembly & gene calling
pull_biocontainers "megahit"        "1.2.9"
pull_biocontainers "bakta"          "1.8.2"

# Binning & taxonomy
# Paper says MetaBAT (v.2.5). If that exact version is unavailable, try common MetaBAT2 versions as fallback.
if ! pull_biocontainers "metabat2" "2.5"; then true; fi
if ! "${DOCKER_BIN}" image inspect "quay.io/biocontainers/metabat2:" >/dev/null 2>&1; then
  pull_biocontainers "metabat2" "2.15" || true
  pull_biocontainers "metabat2" "2.12.1" || true
fi
pull_biocontainers "concoct"        "1.1.0"
pull_biocontainers "maxbin2"        "2.2.7"
pull_biocontainers "das_tool"       "1.1.6"
pull_biocontainers "checkm-genome"  "1.2.2"
pull_biocontainers "gtdbtk"         "2.3.0"

# Long-read QC/plots (paper uses NanoPlot v1.41.6)
pull_biocontainers "nanoplot"       "1.41.6"

# Phage prediction / QC
pull_biocontainers "genomad"        "1.7.6"
pull_biocontainers "vibrant"        "1.2.1"
# VirSorter2 is typically bioconda package "virsorter"
pull_biocontainers "virsorter"      "2.2.4"
pull_biocontainers "cenote-taker3"  "3.4.0"
pull_biocontainers "checkv"         "1.0.1"

# VirSorter2 dependency in paper
pull_biocontainers "snakemake"      "5.26.0"

# Strain replacement / ANI
pull_biocontainers "fastani"        "1.34"

# SV calling (paper: NGMLR v0.2.7 + SAMtools v1.9 (already) + Sniffles2 v2.2)
pull_biocontainers "ngmlr"          "0.2.7"
pull_biocontainers "sniffles"       "2.2"

# Host prediction / taxonomy module
pull_biocontainers "iphop"          "1.3.3"
pull_biocontainers "mmseqs2"        "14.7e284"

# Visualization mentioned in paper (optional but used)
# LoVis4u v0.1.4.1 (paper)
pull_biocontainers "lovis4u"        "0.1.4.1"

log "== Done. =="
echo
echo "IMPORTANT: images are only part of the reproduction."
echo "You still need to download databases mentioned in the paper (e.g., GTDB r214/214.1, iPHoP_db_Aug23_rw, VirSorter2 DB, CheckV DB, etc.)."
echo "If any tool shows WARN, tell me the WARN line and I’ll give you the correct BioContainers repo name / alternate image."