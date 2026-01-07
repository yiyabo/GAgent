# Bowtie 2

## Metadata
- **Version**: 2.5.4
- **Full Name**: Bowtie 2 - Fast and sensitive read alignment
- **Docker Image**: `staphb/bowtie2:latest`
- **Category**: core
- **Database Required**: No (uses reference index)
- **Official Documentation**: http://bowtie-bio.sourceforge.net/bowtie2/manual.shtml
- **Citation**: https://doi.org/10.1038/nmeth.1923

---

## Overview

Bowtie 2 is an ultrafast and memory-efficient tool for aligning sequencing reads to long reference sequences. It is particularly good at aligning reads of about 50 up to 100s or 1,000s of characters, and at aligning to relatively long (e.g. mammalian) genomes.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/bowtie2:latest bowtie2 [options]
```

### Common Use Cases

1. **Build a Bowtie 2 index**
   ```bash
   docker run --rm -v /data:/data staphb/bowtie2:latest \
     bowtie2-build /data/ref.fa /data/ref_index
   ```

2. **Map paired-end reads (SAM output)**
   ```bash
   docker run --rm -v /data:/data staphb/bowtie2:latest \
     bowtie2 -x /data/ref_index -1 /data/R1.fq -2 /data/R2.fq -S /data/aln.sam
   ```

3. **Map using a preset (e.g., --very-sensitive)**
   ```bash
   docker run --rm -v /data:/data staphb/bowtie2:latest \
     bowtie2 --very-sensitive -x /data/ref_index -U /data/reads.fq -S /data/aln.sam
   ```

---

## Command Reference (Common Options)

| Option | Description |
|--------|-------------|
| `-x <idx>` | Index filename prefix |
| `-1`, `-2` | Files with #1 and #2 mates |
| `-U` | Files with unpaired reads |
| `-S` | File for SAM output (default: stdout) |
| `--very-fast` | Fast but less sensitive preset |
| `--sensitive` | Default preset |
| `--very-sensitive` | Slow but most sensitive preset |
| `--local` | Local alignment (ends can be soft-clipped) |
| `-p/--threads` | Number of alignment threads |

---

## Important Notes

- ⚠️ **Indexing**: You MUST run `bowtie2-build` before alignment. Bowtie 1 and Bowtie 2 indexes are NOT compatible.
- ⚠️ **End-to-end vs Local**: Default is end-to-end (entire read must align). Use `--local` if you expect adapters or low-quality ends to be clipped.
- ⚠️ **SAM output**: Like BWA, usually piped to `samtools` for BAM conversion.

---

## Examples for Agent

### Example 1: Map and Filter Unmapped
**User Request**: "Align these reads and only keep the ones that mapped"

**Agent Command**:
```bash
docker run --rm -v /data/user_data:/data staphb/bowtie2:latest \
  bowtie2 -x /data/ref_index -1 /data/R1.fq -2 /data/R2.fq | \
docker run --rm -i -v /data/user_data:/data staphb/samtools:1.21 \
  samtools view -b -F 4 -o /data/mapped_only.bam -
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Could not locate a Bowtie index corresponding to basename`  
   **Solution**: Ensure the index prefix is correct and all 6 index files (.bt2) are present in the directory.

2. **Error**: `Out of memory`  
   **Solution**: Bowtie 2 is generally memory-efficient but indexing very large genomes requires sufficient RAM.
