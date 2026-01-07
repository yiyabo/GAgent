# Bakta

## Metadata
- **Version**: [latest] (Check `bakta -v`)
- **Full Name**: Bakta - Rapid & standardized annotation of bacterial genomes, MAGs & plasmids
- **Docker Image**: `staphb/bakta:latest` (or `oschwengers/bakta:latest`)
- **Category**: annotation
- **Database Required**: Yes - `bakta_db` (~30-50GB)
- **Official Documentation**: https://github.com/oschwengers/bakta
- **Citation**: https://doi.org/10.1099/mgen.0.000685

---

## Overview

Bakta is a tool for the rapid & standardized annotation of bacterial genomes and plasmids. It provides:
- **Comprehensive annotation**: Protein-coding genes (CDS), tRNA, tmRNA, rRNA, ncRNA, CRISPR arrays, etc.
- **Functional assignment**: GO terms, EC numbers, Pfam, TIGRFAM, etc.
- **Compliance**: Generates GenBank/ENA/DDJB compliant results.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/bakta:latest bakta [options] <genome.fasta>
```

### Common Use Cases

1. **Standard bacterial annotation**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/bakta/db:/db \
     staphb/bakta:latest \
     bakta --db /db /data/genome.fasta --output /data/bakta_output --prefix Sample1
   ```

2. **Annotation in Metagenome mode (for MAGs)**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/bakta/db:/db \
     staphb/bakta:latest \
     bakta --db /db --meta /data/mag.fasta --output /data/mag_annotation
   ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `--db`, `-d` | Path to Bakta database | - |
| `--output`, `-o` | Output directory | current |
| `--prefix`, `-p` | Prefix for output files | - |
| `--meta` | Metagenome mode (for MAGs/fragments) | off |
| `--complete` | All sequences are complete replicons | off |
| `--threads` | Number of parallel threads | 1 |
| `--genus` | Genus name | - |
| `--species` | Species name | - |

---

## Important Notes

- ⚠️ **Database**: Bakta REQUIRES a large database. Ensure it is downloaded and correctly mounted.
- ⚠️ **CPU**: Bakta is CPU-intensive. Use `--threads` to speed it up.
- ⚠️ **Prodigal**: Bakta uses Prodigal internally for CDS prediction.

---

## Examples for Agent

### Example 1: Bacterial Genome Annotation
**User Request**: "Annotate this bacterial genome I just assembled"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/bakta/db:/db \
  staphb/bakta:latest \
  bakta --db /db --prefix MyBacterium -o /data/annotation_results -t 16 /data/assembly.fasta
```

### Example 2: Plasmid Annotation
**User Request**: "Annotate this circular plasmid"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/bakta/db:/db \
  staphb/bakta:latest \
  bakta --db /db --complete --plasmid "pSample1" -o /data/plasmid_ann /data/plasmid.fasta
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Database not found`  
   **Solution**: Check the `--db` path and ensure the database volume is correctly mounted in the Docker command.

2. **Error**: `Out of memory`  
   **Solution**: Bakta needs significant RAM for its HMMER and BLAST searches. Increase system memory.
