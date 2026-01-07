# Samtools

## Metadata
- **Version**: 1.23 (or compatible 1.x)
- **Full Name**: Samtools - Utilities for the Sequence Alignment/Map (SAM) format
- **Docker Image**: `staphb/samtools:1.21` (or `latest`)
- **Category**: core
- **Database Required**: No
- **Official Documentation**: http://www.htslib.org/doc/samtools.html
- **Citation**: https://doi.org/10.1093/bioinformatics/btp352

---

## Overview

Samtools is the industry-standard suite of programs for interacting with high-throughput sequencing data. It primarily handles:
- **Conversion**: SAM ↔ BAM ↔ CRAM
- **Sorting & Indexing**: Essential for downstream analysis and visualization
- **Statistics**: Calculating coverage, depth, and mapping quality
- **Manipulation**: Merging, splitting, and filtering alignments

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/samtools:1.21 samtools [command] [options]
```

### Common Use Cases

1. **Convert SAM to BAM and Sort**
   ```bash
   docker run --rm -v /data:/data staphb/samtools:1.21 \
     samtools sort /data/aln.sam -o /data/aln.sorted.bam
   ```

2. **Index a BAM file (required for IGV/viewing)**
   ```bash
   docker run --rm -v /data:/data staphb/samtools:1.21 \
     samtools index /data/aln.sorted.bam
   ```

3. **Calculate Mapping Statistics**
   ```bash
   docker run --rm -v /data:/data staphb/samtools:1.21 \
     samtools flagstat /data/aln.sorted.bam
   ```

4. **Calculate Coverage per Base**
   ```bash
   docker run --rm -v /data:/data staphb/samtools:1.21 \
     samtools depth /data/aln.sorted.bam > /data/depth.txt
   ```

---

## Full Help Output

```
Usage:   samtools <command> [options]

Commands:
  -- Indexing
     dict           create a sequence dictionary file
     faidx          index/extract FASTA
     fqidx          index/extract FASTQ
     index          index alignment

  -- Editing
     calmd          recalculate MD/NM tags and '=' bases
     fixmate        fix mate information
     reheader       replace BAM header
     targetcut      cut fosmid regions (for fosmid pool only)
     addreplacerg   adds or replaces RG tags
     markdup        mark duplicates
     ampliconclip   clip oligos from the end of reads

  -- File operations
     collate        shuffle and group alignments by name
     cat            concatenate BAMs
     consensus      produce a consensus Pileup/FASTA/FASTQ
     merge          merge sorted alignments
     mpileup        multi-way pileup
     sort           sort alignment file
     split          splits a file by read group
     quickcheck     quickly check if SAM/BAM/CRAM file appears intact
     fastq          converts a BAM to a FASTQ
     fasta          converts a BAM to a FASTA
     import         Converts FASTA or FASTQ files to SAM/BAM/CRAM
     reference      Generates a reference from aligned data
     reset          Reverts aligner changes in reads

  -- Statistics
     bedcov         read depth per BED region
     coverage       alignment depth and percent coverage
     depth          compute the depth
     flagstat       simple stats
     idxstats       BAM index stats
     cram-size      list CRAM Content-ID and Data-Series sizes
     phase          phase heterozygotes
     stats          generate stats (former bamcheck)
     ampliconstats  generate amplicon specific stats
     checksum       produce order-agnostic checksums of sequence content

  -- Viewing
     flags          explain BAM flags
     head           header viewer
     tview          text alignment viewer
     view           SAM<->BAM<->CRAM conversion
     depad          convert padded BAM to unpadded BAM
     samples        list the samples in a set of SAM/BAM/CRAM files

  -- Misc
     help [cmd]     display this help message or help for [cmd]
     version        detailed version information
```

---

## Important Notes

- ⚠️ **Sorting**: Most tools and genome browsers (like IGV) require BAM files to be **coordinate-sorted** and **indexed**. Always run `sort` and `index` after alignment.
- ⚠️ **Pipe Support**: Samtools works excellently with pipes. You can pipe from `minimap2` directly into `samtools sort`.
- ⚠️ **Threads**: Use `-@` (or `-t` in some versions/commands) to specify multiple threads for sorting and compression.
- ⚠️ **BAM vs SAM**: BAM is the compressed binary version of SAM. Always use BAM for storage and analysis to save space.

---

## Examples for Agent

### Example 1: Full Post-Alignment Processing
**User Request**: "I just finished a minimap2 alignment. Now help me sort it, index it, and give me the mapping stats."

**Agent Command**:
```bash
# 1. Sort SAM to BAM
docker run --rm -v /data/user_data:/data staphb/samtools:1.21 \
  samtools sort -@ 4 /data/aln.sam -o /data/aln.sorted.bam

# 2. Index the BAM
docker run --rm -v /data/user_data:/data staphb/samtools:1.21 \
  samtools index /data/aln.sorted.bam

# 3. Get stats
docker run --rm -v /data/user_data:/data staphb/samtools:1.21 \
  samtools flagstat /data/aln.sorted.bam > /data/flagstat.txt
```

### Example 2: Check Coverage of a Specific Sample
**User Request**: "Show me the average coverage and depth for this BAM file"

**Agent Command**:
```bash
docker run --rm -v /data/user_data:/data staphb/samtools:1.21 \
  samtools coverage /data/sample.sorted.bam
```

### Example 3: Extract FastQ from BAM
**User Request**: "Convert this BAM file back to FastQ format"

**Agent Command**:
```bash
docker run --rm -v /data/user_data:/data staphb/samtools:1.21 \
  samtools fastq /data/sample.bam > /data/sample.fastq
```

---

## Troubleshooting

### Common Errors

1. **Error**: `[E::idx_find_and_load] could not find index file`  
   **Solution**: Run `samtools index file.bam` before running the command.

2. **Error**: `[main_samview] fail to read the header from "file.bam"`  
   **Solution**: The file might be corrupted or is not a valid BAM file. Run `samtools quickcheck` to verify.

3. **Error**: `[bam_sort_core] fail to open temporary file`  
   **Solution**: Ensure the container has write permissions to the output directory and there is enough disk space.
