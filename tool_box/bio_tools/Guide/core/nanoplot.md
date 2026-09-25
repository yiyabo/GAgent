# NanoPlot

## Metadata
- **Version**: [latest] (Check `NanoPlot -v`)
- **Full Name**: NanoPlot - Plotting tool for long-read sequencing data
- **Docker Image**: `staphb/nanoplot:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://github.com/wdecoster/NanoPlot
- **Citation**: https://doi.org/10.1093/bioinformatics/bty149

---

## Overview

NanoPlot is a tool for visualizing and quality control of long-read sequencing data (Oxford Nanopore and PacBio). It generates:
- **Statistical reports**: Summaries of read lengths, qualities, and depth
- **Visualizations**: Histograms, scatter plots, and heatmaps
- **HTML reports**: Interactive dashboards for easy exploration

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/nanoplot:latest NanoPlot [options]
```

### Common Use Cases

1. **QC on FASTQ files**
   ```bash
   docker run --rm -v /data:/data staphb/nanoplot:latest \
     NanoPlot --fastq /data/reads.fastq.gz -o /data/nanoplot_results
   ```

2. **QC on BAM files (aligned reads)**
   ```bash
   docker run --rm -v /data:/data staphb/nanoplot:latest \
     NanoPlot --bam /data/alignment.bam -o /data/nanoplot_results
   ```

3. **Comparison of multiple runs**
   ```bash
   docker run --rm -v /data:/data staphb/nanoplot:latest \
     NanoPlot --summary /data/run1/sequencing_summary.txt /data/run2/sequencing_summary.txt -o /data/comparison
   ```

---

## Command Reference (Common Options)

| Option | Description |
|--------|-------------|
| `--fastq` | Input data is in FASTQ format |
| `--fasta` | Input data is in FASTA format |
| `--bam` | Input data is in BAM format |
| `--summary` | Input data is a sequencing summary file (Guppy/Albacore) |
| `-o, --outdir` | Output directory name |
| `-t, --threads` | Number of threads to use |
| `--maxlength` | Filter out reads longer than N |
| `--minlength` | Filter out reads shorter than N |
| `--minqual` | Filter out reads with quality lower than N |

---

## Important Notes

- ⚠️ **Interactive**: The primary output is `NanoPlot-report.html`, which should be viewed in a web browser.
- ⚠️ **Summary Files**: For Nanopore data, using the `sequencing_summary.txt` file is much faster than parsing FASTQ files.
- ⚠️ **Plots**: You can specify plot types with `--plots` (kde, hex, dot).

---

## Examples for Agent

### Example 1: Full Long-Read QC
**User Request**: "Generate a quality report for my Nanopore reads"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/nanoplot:latest \
  NanoPlot --fastq /data/reads.fq.gz --loglength -t 4 -o /data/nanoplot_qc
```

### Example 2: Length and Quality Filtering
**User Request**: "Show me the distribution of reads longer than 5kb and with quality > 10"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/nanoplot:latest \
  NanoPlot --fastq /data/reads.fq.gz --minlength 5000 --minqual 10 -o /data/filtered_qc
```

---

## Troubleshooting

### Common Errors

1. **Error**: `File not found`  
   **Solution**: Check your Docker volume mounts. The path INSIDE the container must match where the file is located.

2. **Error**: `MemoryError`  
   **Solution**: Large FASTQ files can consume a lot of RAM. Use `--summary` if possible or increase system RAM.
