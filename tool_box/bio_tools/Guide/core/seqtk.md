# Seqtk

## Metadata
- **Version**: 1.4-r122
- **Full Name**: Seqtk - A fast and lightweight tool for processing sequences in FASTA/Q formats
- **Docker Image**: `staphb/seqtk:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://github.com/lh3/seqtk
- **Citation**: https://github.com/lh3/seqtk

---

## Overview

Seqtk is a fast and lightweight tool for processing biological sequences in FASTA and FASTQ formats. It seamlessly parses both IDBA and FASTA/Q files, which can also be optionally compressed by gzip.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/seqtk:latest seqtk <command> [arguments]
```

### Common Use Cases

1. **Convert FASTQ to FASTA**
   ```bash
   docker run --rm -v /data:/data staphb/seqtk:latest \
     seqtk seq -a /data/reads.fastq > /data/reads.fasta
   ```

2. **Sample 10,000 sequences (subsampling)**
   ```bash
   docker run --rm -v /data:/data staphb/seqtk:latest \
     seqtk sample -s100 /data/reads.fastq 10000 > /data/sampled.fastq
   ```

3. **Reverse complement a sequence**
   ```bash
   docker run --rm -v /data:/data staphb/seqtk:latest \
     seqtk seq -r /data/input.fasta > /data/rev_comp.fasta
   ```

4. **Extract sequences by a list of names**
   ```bash
   docker run --rm -v /data:/data staphb/seqtk:latest \
     seqtk subseq /data/input.fasta /data/name_list.txt > /data/subset.fasta
   ```

---

## Command Reference

### Main Commands
| Command | Description |
|---------|-------------|
| `seq` | Common transformation of FASTA/Q (reverse complement, mask, etc.) |
| `sample` | Subsample sequences |
| `subseq` | Extract subsequences from FASTA/Q |
| `fqchk` | FASTQ QC (base/quality summary) |
| `mergepe` | Interleave two PE FASTA/Q files |
| `trimfq` | Trim FASTQ using the Phred algorithm |
| `mutfa` | Point mutate FASTA at specified positions |

---

## Important Notes

- ⚠️ **Fast**: Seqtk is extremely fast as it is written in C.
- ⚠️ **Pipes**: Most commands output to `stdout`, making it easy to pipe to other tools.
- ⚠️ **Gzip**: Automatically handles gzipped input files.

---

## Examples for Agent

### Example 1: Subsample for testing
**User Request**: "I want to run a quick test. Give me 1% of my reads."

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/seqtk:latest \
  seqtk sample -s100 /data/reads.fq.gz 0.01 > /data/test_subset.fq
```

### Example 2: Trim low quality ends
**User Request**: "Trim the low quality ends of these reads"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/seqtk:latest \
  seqtk trimfq /data/reads.fq.gz > /data/trimmed.fq
```

---

## Troubleshooting

### Common Errors

1. **Error**: `[E::main] failed to open file`  
   **Solution**: Check your Docker volume mounts. Ensure the path is correct within the container.

2. **Error**: Incorrect output format  
   **Solution**: Many seqtk commands require specific flags (like `-a` for fasta output in `seq`) to get the desired format.
