# VirSorter2

## Metadata
- **Version**: 2.2.4
- **Full Name**: VirSorter2 - Multi-classifier workflow for identifying viral sequences
- **Docker Image**: `jiarong/virsorter:latest` (or `staphb/virsorter2:latest`)
- **Category**: phage
- **Database Required**: Yes - `virsorter2_db` (~10-15GB)
- **Official Documentation**: https://github.com/jiarong/VirSorter2
- **Citation**: https://doi.org/10.1186/s40168-020-00957-y

---

## Overview

VirSorter2 is a modular tool that uses a multi-classifier approach to identify viral sequences from metagenomic data. It can identify:
- **dsDNA phages**
- **ssDNA viruses**
- **RNA viruses**
- **NCLDVs** (Large DNA viruses)
- **Lavidaviridae** (Virophages)

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data jiarong/virsorter:latest virsorter [options] COMMAND [ARGS]...
```

### Common Use Cases

1. **Run viral identification workflow**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/virsorter2/db:/db \
     jiarong/virsorter:latest \
     virsorter run -i /data/assembly.fasta -w /data/virsorter_out --db-dir /db --min-score 0.5 -j 8
   ```

2. **Run only specific viral groups (e.g., dsDNA and ssDNA)**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/virsorter2/db:/db \
     jiarong/virsorter:latest \
     virsorter run -i /data/assembly.fasta -w /data/virsorter_out --db-dir /db --include-groups dsDNA,ssDNA -j 8
   ```

---

## Command Reference (Common Options for `run`)

| Option | Description | Default |
|--------|-------------|---------|
| `-i, --input` | Input FASTA file | - |
| `-w, --working-dir` | Working directory for results | - |
| `--db-dir` | Path to VirSorter2 database | - |
| `--min-score` | Minimum score threshold (0-1) | 0.5 |
| `-j, --threads` | Number of threads to use | 1 |
| `--include-groups` | Viral groups to search for (dsDNA,ssDNA,RNA,NCLDV,lavidaviridae) | all |
| `--min-length` | Minimum sequence length to consider | 1500 |

---

## Important Notes

- ⚠️ **Database**: Requires a specific database. Use `virsorter setup` if it's not already downloaded.
- ⚠️ **Score Threshold**: High scores (>0.9) are very reliable; lower scores (0.5-0.7) may contain more false positives but are useful for discovery.
- ⚠️ **Integrations**: VirSorter2 output can be further refined with tools like CheckV.

---

## Examples for Agent

### Example 1: Phage Discovery in Metagenome
**User Request**: "Find all possible viral sequences in this metagenome assembly"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/virsorter2/db:/db \
  jiarong/virsorter:latest \
  virsorter run -i /data/assembly/final.contigs.fa -w /data/phage_results --db-dir /db --all -j 16
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Database not found`  
   **Solution**: Ensure the `--db-dir` points to the correct path inside the container and that the volume is mounted.

2. **Error**: `No viral sequences found`  
   **Solution**: Your assembly might not contain recognizable viral signatures, or you may need to lower the `--min-score` (not recommended if precision is needed).
