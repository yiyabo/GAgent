# Trim Galore!

## Metadata
- **Version**: [0.6.10 or compatible]
- **Full Name**: Trim Galore! - Quality and adapter trimming for Next-Generation Sequencing
- **Docker Image**: `staphb/trim-galore:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://github.com/FelixKrueger/TrimGalore/blob/master/Docs/Trim_Galore_User_Guide.md
- **Citation**: https://github.com/FelixKrueger/TrimGalore

---

## Overview

Trim Galore! is a wrapper script to automate quality and adapter trimming as well as quality control, with an extra functionality for RRBS data. It uses:
- **Cutadapt**: For adapter and quality trimming
- **FastQC**: For quality control (optional)

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/trim-galore:latest trim_galore [options] <filename(s)>
```

### Common Use Cases

1. **Single-end trimming with auto-detection**
   ```bash
   docker run --rm -v /data:/data staphb/trim-galore:latest \
     trim_galore /data/reads.fastq.gz --o /data/trimmed/
   ```

2. **Paired-end trimming**
   ```bash
   docker run --rm -v /data:/data staphb/trim-galore:latest \
     trim_galore --paired /data/reads_1.fastq.gz /data/reads_2.fastq.gz -o /data/trimmed/
   ```

3. **Trimming with FastQC report**
   ```bash
   docker run --rm -v /data:/data staphb/trim-galore:latest \
     trim_galore --fastqc /data/reads.fastq.gz
   ```

---

## Command Reference (Common Options)

| Option | Description |
|--------|-------------|
| `--paired` | Process paired-end reads |
| `--quality <INT>` | Phred score threshold for quality trimming (default: 20) |
| `--length <INT>` | Minimum read length to keep (default: 20) |
| `--gzip` | Compress output files with GZIP |
| `--fastqc` | Run FastQC after trimming |
| `-o /path/` | Output directory |
| `--cores <INT>` | Number of cores for Cutadapt (parallel processing) |

---

## Important Notes

- ⚠️ **Auto-detection**: Trim Galore! can automatically detect Illumina, Nextera, and Small RNA adapters.
- ⚠️ **Paired-end**: Both reads must pass the length threshold for the pair to be kept (unless using `--retain_unpaired`).
- ⚠️ **RRBS**: Use `--rrbs` for Reduced Representation Bisulfite Sequencing data.

---

## Examples for Agent

### Example 1: Standard QC and Trimming
**User Request**: "Clean my paired-end reads and run FastQC"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/trim-galore:latest \
  trim_galore --paired --fastqc --gzip -o /data/cleaned_reads/ /data/R1.fq.gz /data/R2.fq.gz
```

### Example 2: Stringent Trimming
**User Request**: "Trim reads with high stringency (min quality 30, min length 50)"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/trim-galore:latest \
  trim_galore --quality 30 --length 50 /data/raw.fastq.gz
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Cutadapt not in PATH`  
   **Solution**: Usually handled by the Docker image. If running locally, install `cutadapt`.

2. **Error**: `Paired-end file names must be specified as a pair`  
   **Solution**: Ensure you provide both R1 and R2 files and include the `--paired` flag.
