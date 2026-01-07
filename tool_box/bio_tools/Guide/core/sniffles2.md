# Sniffles2

## Metadata
- **Version**: 2.2
- **Full Name**: Sniffles2 - Structural variant caller for long-read sequencing data
- **Docker Image**: `staphb/sniffles:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://github.com/fritzsedlazeck/Sniffles
- **Citation**: https://doi.org/10.1038/s41592-018-0001-7 (Sniffles1)

---

## Overview

Sniffles2 is a fast structural variant (SV) caller for long-read sequencing data (PacBio and Oxford Nanopore). It can detect:
- **Deletions**
- **Insertions**
- **Duplications**
- **Inversions**
- **Translocations**
- **Breakends (BND)**

It is highly optimized for speed and accuracy compared to the original Sniffles.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/sniffles:latest sniffles [options]
```

### Common Use Cases

1. **Call SVs for a single sample (BAM input)**
   ```bash
   docker run --rm -v /data:/data staphb/sniffles:latest \
     sniffles --input /data/sorted_aln.bam --vcf /data/results.vcf --threads 8
   ```

2. **Call SVs with reference (enables output of DEL sequences)**
   ```bash
   docker run --rm -v /data:/data staphb/sniffles:latest \
     sniffles --input /data/sorted_aln.bam --reference /data/ref.fa --vcf /data/results.vcf
   ```

3. **Multi-sample calling (2 steps)**
   - Step 1: Create SNF for each sample
     ```bash
     sniffles --input sample1.bam --snf sample1.snf
     ```
   - Step 2: Combined calling
     ```bash
     sniffles --input sample1.snf sample2.snf --vcf combined.vcf
     ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `--input`, `-i` | Sorted and indexed BAM/CRAM or .snf files | - |
| `--vcf`, `-v` | Output VCF filename | - |
| `--snf` | Output .snf filename (for multi-sample calling) | - |
| `--reference` | Reference FASTA (optional but recommended) | - |
| `--threads`, `-t` | Number of parallel threads | 4 |
| `--minsupport` | Minimum number of supporting reads | auto |
| `--minsvlen` | Minimum SV length in bp | 50 |
| `--mosaic` | Enable mosaic/somatic mode | off |

---

## Important Notes

- ⚠️ **Alignment**: Use an SV-aware aligner like **NGMLR** or **Minimap2** with specific SV flags.
- ⚠️ **Indexing**: The input BAM file MUST be coordinate-sorted and indexed (`samtools index`).
- ⚠️ **VCF output**: If the filename ends with `.gz`, Sniffles2 will automatically bgzip and index it.

---

## Examples for Agent

### Example 1: Standard SV Calling
**User Request**: "Find structural variants in my Nanopore BAM file"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/sniffles:latest \
  sniffles --input /data/mapping/sample.sorted.bam --vcf /data/sv/results.vcf.gz --reference /data/ref/genome.fa -t 16
```

### Example 2: Find rare/mosaic variants
**User Request**: "I suspect there are some low-frequency mosaic variants, find them"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/sniffles:latest \
  sniffles --input /data/mapping/sample.sorted.bam --vcf /data/sv/mosaic.vcf --mosaic --minsupport 2
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Input is not sorted by coordinates`  
   **Solution**: Run `samtools sort` on your BAM file before passing it to Sniffles2.

2. **Error**: `Missing deletion sequences`  
   **Solution**: You must provide the reference genome using `--reference` to get the actual deleted sequences in the VCF.
