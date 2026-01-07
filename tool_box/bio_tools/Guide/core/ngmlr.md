# NGMLR

## Metadata
- **Version**: 0.2.7
- **Full Name**: NGMLR - Co-linear aligner for long reads (Oxford Nanopore and PacBio)
- **Docker Image**: `staphb/ngmlr:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://github.com/philres/ngmlr
- **Citation**: https://doi.org/10.1038/s41592-018-0001-7

---

## Overview

NGMLR is a long-read aligner designed to correctly align reads across large structural variations (SVs). It is particularly effective for:
- **Structural Variant Detection**: Pairs perfectly with Sniffles.
- **Heterogeneous Error Profiles**: Handles the high error rates and specific error patterns of ONT and PacBio reads.
- **Split-reads**: Accurately maps reads that bridge structural variants.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/ngmlr:latest ngmlr [options] -r <ref> -q <query>
```

### Common Use Cases

1. **Map Nanopore reads**
   ```bash
   docker run --rm -v /data:/data staphb/ngmlr:latest \
     ngmlr -r /data/ref.fa -q /data/reads.fq.gz -o /data/aln.sam -x ont -t 8
   ```

2. **Map PacBio reads**
   ```bash
   docker run --rm -v /data:/data staphb/ngmlr:latest \
     ngmlr -r /data/ref.fa -q /data/reads.fq.gz -o /data/aln.sam -x pacbio -t 8
   ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `-r, --reference` | Reference genome FASTA | - |
| `-q, --query` | Query reads FASTA/Q | - |
| `-o, --output` | Output SAM file | stdout |
| `-t, --threads` | Number of threads | 1 |
| `-x, --presets` | Parameter presets (`ont`, `pacbio`) | pacbio |
| `--min-identity` | Minimum identity threshold | 0.65 |
| `--rg-id` | Add Read Group ID to SAM | - |

---

## Important Notes

- ⚠️ **SVs**: NGMLR is the companion aligner for the Sniffles structural variant caller.
- ⚠️ **Presets**: Always use `-x ont` for Nanopore data to ensure correct alignment parameters.
- ⚠️ **BAM Conversion**: Outputs SAM. Use `samtools` to convert to sorted and indexed BAM for Sniffles.

---

## Examples for Agent

### Example 1: Preparation for SV Calling
**User Request**: "Map my Nanopore reads to the reference so I can later find structural variants"

**Agent Command**:
```bash
docker run --rm -v /data/user_data:/data staphb/ngmlr:latest \
  ngmlr -r /data/ref.fa -q /data/reads.fq.gz -x ont -t 16 | \
docker run --rm -i -v /data/user_data:/data staphb/samtools:1.21 \
  samtools sort -o /data/aln.sorted.bam -
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Cannot open reference file`  
   **Solution**: Check your Docker volume mounts. Ensure the path to the reference is correct within the container.

2. **Error**: `Low mapping rate`  
   **Solution**: Ensure you are using the correct preset (`-x ont` or `-x pacbio`). If reads are very low quality, you may need to lower `--min-identity`.
        
