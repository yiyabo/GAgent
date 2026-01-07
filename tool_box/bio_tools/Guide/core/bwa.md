# BWA (Burrows-Wheeler Aligner)

## Metadata
- **Version**: 0.7.17-r1188
- **Full Name**: BWA - Burrows-Wheeler Transformation Aligner
- **Docker Image**: `staphb/bwa:latest`
- **Category**: core
- **Database Required**: No (uses reference FASTA)
- **Official Documentation**: http://bio-bwa.sourceforge.net/bwa.1.shtml
- **Citation**: https://doi.org/10.1093/bioinformatics/btp324

---

## Overview

BWA is a software package for mapping low-divergent sequences against a large reference genome. It consists of three algorithms:
- **BWA-MEM**: The most recommended algorithm for modern sequencing (70bp-1Mbp). Faster and more accurate.
- **BWA-BACKTRACK**: For Illumina short reads up to 100bp.
- **BWA-SW**: For long queries with more frequent gaps.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/bwa:latest bwa [command] [options]
```

### Common Use Cases

1. **Index a reference genome (Required first step)**
   ```bash
   docker run --rm -v /data:/data staphb/bwa:latest \
     bwa index /data/ref.fa
   ```

2. **Map paired-end reads using BWA-MEM (SAM output)**
   ```bash
   docker run --rm -v /data:/data staphb/bwa:latest \
     bwa mem /data/ref.fa /data/read1.fq /data/read2.fq > /data/aln.sam
   ```

3. **Map single-end reads**
   ```bash
   docker run --rm -v /data:/data staphb/bwa:latest \
     bwa mem /data/ref.fa /data/reads.fq > /data/aln.sam
   ```

---

## Command Reference

### Main Commands
| Command | Description |
|---------|-------------|
| `index` | Index sequences in the FASTA format |
| `mem` | BWA-MEM algorithm (recommended) |
| `aln` | Gapped/ungapped alignment (old backtrack) |
| `sampe` | Generate paired-end alignment from `aln` results |
| `samse` | Generate single-end alignment from `aln` results |

---

## Important Notes

- ⚠️ **Indexing**: You MUST run `bwa index` on your reference before mapping.
- ⚠️ **Algorithms**: Always use `bwa mem` unless you have a specific reason to use the older algorithms.
- ⚠️ **Output**: Returns SAM format by default. Usually paired with `samtools` for BAM conversion and sorting.

---

## Examples for Agent

### Example 1: Map and Convert to BAM
**User Request**: "Align these reads and give me a sorted BAM file"

**Agent Command**:
```bash
# Complex pipe example (best practice)
docker run --rm -v /data/user_data:/data staphb/bwa:latest \
  bwa mem -t 4 /data/ref.fa /data/R1.fq /data/R2.fq | \
docker run --rm -i -v /data/user_data:/data staphb/samtools:1.21 \
  samtools sort -o /data/aln.sorted.bam -
```

### Example 2: Add Read Group information
**User Request**: "Align the reads and add Sample1 as the read group ID"

**Agent Command**:
```bash
docker run --rm -v /data/user_data:/data staphb/bwa:latest \
  bwa mem -R "@RG\tID:Sample1\tLB:Lib1\tSM:Sample1" /data/ref.fa /data/R1.fq /data/R2.fq > /data/aln.sam
```

---

## Troubleshooting

### Common Errors

1. **Error**: `[E::bwa_idx_load_from_disk] fail to locate the index`  
   **Solution**: Ensure you have run `bwa index` and that all index files (.amb, .ann, .bwt, .pac, .sa) are in the same directory as the FASTA file.

2. **Error**: `Segmentation fault`  
   **Solution**: Often caused by insufficient memory during indexing large genomes or corrupted input files.
        
