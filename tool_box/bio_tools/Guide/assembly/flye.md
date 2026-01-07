# Flye

## Metadata
- **Version**: [latest] (Check `flye -v`)
- **Full Name**: Flye - De novo assembler for long and noisy reads
- **Docker Image**: `staphb/flye:latest`
- **Category**: assembly
- **Database Required**: No
- **Official Documentation**: https://github.com/fenderglass/Flye
- **Citation**: https://doi.org/10.1038/s41587-019-0072-8

---

## Overview

Flye is a de novo assembler for single-molecule sequencing reads, such as those produced by PacBio and Oxford Nanopore. It is designed for both genomic and metagenomic datasets and handles repetitive regions well using repeat graphs.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/flye:latest flye [options] --out-dir <dir>
```

### Common Use Cases

1. **Assemble Nanopore raw reads**
   ```bash
   docker run --rm -v /data:/data staphb/flye:latest \
     flye --nano-raw /data/reads.fq.gz --out-dir /data/assembly --threads 8
   ```

2. **Assemble Nanopore High-Quality reads (Guppy5+ SUP)**
   ```bash
   docker run --rm -v /data:/data staphb/flye:latest \
     flye --nano-hq /data/reads.fq.gz --out-dir /data/assembly --threads 8
   ```

3. **Metagenome assembly mode**
   ```bash
   docker run --rm -v /data:/data staphb/flye:latest \
     flye --nano-raw /data/meta_reads.fq.gz --out-dir /data/meta_assembly --meta --threads 16
   ```

4. **Assemble PacBio HiFi reads**
   ```bash
   docker run --rm -v /data:/data staphb/flye:latest \
     flye --pacbio-hifi /data/reads.fq.gz --out-dir /data/assembly --threads 8
   ```

---

## Command Reference (Common Options)

| Option | Read Type | Description |
|--------|-----------|-------------|
| `--nano-raw` | ONT | Regular ONT reads (<20% error) |
| `--nano-hq` | ONT | High-quality ONT reads (<5% error) |
| `--pacbio-raw` | PacBio | PacBio CLR reads (<20% error) |
| `--pacbio-hifi` | PacBio | PacBio HiFi reads (<1% error) |
| `--meta` | Any | Metagenome / uneven coverage mode |
| `--genome-size` | Any | Estimated genome size (e.g., `5m`, `2.6g`) |
| `--threads`, `-t` | Any | Number of parallel threads |
| `--iterations`, `-i` | Any | Number of polishing iterations (default: 1) |

---

## Important Notes

- ⚠️ **Read Type**: You MUST specify the correct read type flag (e.g., `--nano-raw` vs `--nano-hq`).
- ⚠️ **Meta Mode**: Use `--meta` for metagenomes or if you have very uneven coverage.
- ⚠️ **Polishing**: Flye includes an internal polisher. You can increase iterations with `-i`.

---

## Examples for Agent

### Example 1: Nanopore Metagenome Assembly
**User Request**: "Assemble these Nanopore metagenome reads"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/flye:latest \
  flye --nano-raw /data/reads.fq.gz --out-dir /data/flye_meta --meta -t 16
```

### Example 2: Bacterial Genome Assembly
**User Request**: "Assemble this bacterial genome (est. size 5MB) from PacBio HiFi reads"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/flye:latest \
  flye --pacbio-hifi /data/reads.fq.gz --genome-size 5m --out-dir /data/phage_assembly -t 8
```

---

## Troubleshooting

### Common Errors

1. **Error**: `No such file or directory`  
   **Solution**: Check your Docker volume mounts. The path in the `--nano-raw` (etc.) argument must be the path INSIDE the container.

2. **Error**: `Assembly produced no contigs`  
   **Solution**: This can happen if coverage is too low or reads are too short. Check your input data quality.
        
