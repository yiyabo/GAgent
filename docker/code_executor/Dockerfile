FROM python:3.10-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_PREFER_BINARY=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    NUMBA_CACHE_DIR=/tmp/numba_cache \
    NUMBA_DISABLE_JIT=0

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    git \
    curl \
    pkg-config \
    bedtools \
    bcftools \
    bowtie2 \
    bwa \
    libhdf5-dev \
    libopenblas-dev \
    liblapack-dev \
    libfreetype6-dev \
    libpng-dev \
    libglib2.0-0 \
    libsm6 \
    minimap2 \
    samtools \
    seqtk \
    tabix \
    libxext6 \
    libxrender1 \
    libbz2-dev \
    liblzma-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/code-executor

COPY docker/code_executor/requirements.txt /tmp/code-executor-requirements.txt

# Keep setuptools below 81 so the legacy `louvain` package still exposes
# `pkg_resources` during import-time compatibility checks.
RUN python -m pip install --upgrade \
        pip==25.0.1 \
        setuptools==80.9.0 \
        wheel==0.46.3 \
        -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} && \
    python -m pip install -r /tmp/code-executor-requirements.txt \
        -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} && \
    python -m pip install --no-deps scrublet==0.2.3 \
        -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} && \
    python -m pip check && \
    python - <<'PY'
import importlib

modules = [
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "seaborn",
    "sklearn",
    "statsmodels.api",
    "networkx",
    "umap",
    "h5py",
    "tables",
    "pyarrow",
    "openpyxl",
    "xlrd",
    "loompy",
    "Bio",
    "pysam",
    "pyfaidx",
    "pyBigWig",
    "pybedtools",
    "pyranges",
    "biom",
    "pybiomart",
    "HTSeq",
    "vcfpy",
    "dna_features_viewer",
    "mygene",
    "goatools",
    "gseapy",
    "pydeseq2",
    "cutadapt",
    "cooler",
    "bioframe",
    "biotite",
    "pycirclize",
    "primer3",
    "pyfastx",
    "biopandas",
    "pysradb",
    "multiqc",
    "anndata",
    "scanpy",
    "harmonypy",
    "bbknn",
    "igraph",
    "leidenalg",
    "louvain",
    "skmisc.loess",
    "scanorama",
    "dask.array",
    "celltypist",
    "mudata",
    "muon",
    "scrublet",
]
failed = []
for module in modules:
    try:
        importlib.import_module(module)
    except Exception as exc:
        failed.append(f"{module}: {exc}")

from scanpy.experimental.pp import _highly_variable_genes  # noqa: F401
from scanpy.external.pp import harmony_integrate  # noqa: F401

if failed:
    raise RuntimeError("runtime import validation failed: " + " | ".join(failed))
print("code executor runtime imports ok")
PY
RUN command -v samtools >/dev/null && \
    command -v bcftools >/dev/null && \
    command -v bedtools >/dev/null && \
    command -v tabix >/dev/null && \
    command -v seqtk >/dev/null && \
    command -v bwa >/dev/null && \
    command -v bowtie2 >/dev/null && \
    command -v minimap2 >/dev/null

WORKDIR /workspace
