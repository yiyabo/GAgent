# CONCOCT

## Metadata
- **Version**: [latest] (Check `concoct -v`)
- **Full Name**: CONCOCT - Clustering contigs on coverage and composition
- **Docker Image**: `staphb/concoct:latest` (or `binning/concoct`)
- **Category**: binning
- **Database Required**: No
- **Official Documentation**: https://concoct.readthedocs.io/en/latest/
- **Citation**: https://doi.org/10.1038/nmeth.3103

---

## Overview

CONCOCT is a program for binning metagenomic contigs by using co-assembly and differential coverage across multiple samples. it combines:
- **Sequence composition**: K-mer frequencies (genomic signature)
- **Coverage profile**: Average coverage across one or more samples

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/concoct:latest concoct [options]
```

### Common Use Cases

1. **Standard binning**
   ```bash
   docker run --rm -v /data:/data staphb/concoct:latest \
     concoct --composition_file /data/contigs.fa --coverage_file /data/coverage.tsv -b /data/concoct_output/
   ```

2. **Binning with specific kmer length and clusters**
   ```bash
   docker run --rm -v /data:/data staphb/concoct:latest \
     concoct -k 4 -c 500 --composition_file /data/contigs.fa --coverage_file /data/coverage.tsv -b /data/concoct_run2/
   ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `--composition_file` | Contigs in FASTA format | - |
| `--coverage_file` | Table of average coverage per contig per sample | - |
| `-b, --basename` | Output directory or basename | current |
| `-c, --clusters` | Maximal number of clusters for VGMM | 400 |
| `-k, --kmer_length` | Kmer length | 4 |
| `-t, --threads` | Number of threads to use | 1 |
| `-l, --length_threshold` | Skip contigs shorter than this value | 1000 |

---

## Important Notes

- ⚠️ **Coverage File**: The coverage file must be a tab-separated table where rows are contigs and columns are samples.
- ⚠️ **Log Transformation**: CONCOCT automatically log-transforms and normalizes coverage data by default.
- ⚠️ **Contig Length**: It is highly recommended to filter out short contigs (e.g., <1000bp or <2500bp) to improve binning quality.

---

## Examples for Agent

### Example 1: Metagenome Binning Pipeline
**User Request**: "Run CONCOCT on my assembled contigs using the coverage table I generated"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/concoct:latest \
  concoct --composition_file /data/assembly/final.contigs.fa --coverage_file /data/mapping/coverage_table.tsv -b /data/binning/concoct/
```

---

## Troubleshooting

### Common Errors

1. **Error**: `ValueError: No clusters found`  
   **Solution**: This can happen if the coverage data is too sparse or contigs are too short. Ensure you have high-quality mapping data and have filtered out very short contigs.

2. **Error**: `MemoryError`  
   **Solution**: CONCOCT can be memory intensive for large numbers of contigs or high kmer lengths. Increase system RAM or use a higher length threshold.
