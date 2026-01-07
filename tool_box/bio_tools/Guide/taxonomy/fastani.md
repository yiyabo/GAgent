# FastANI

## Metadata
- **Version**: [latest] (Check `fastANI -v`)
- **Full Name**: FastANI - Fast whole-genome Average Nucleotide Identity
- **Docker Image**: `staphb/fastani:latest`
- **Category**: taxonomy
- **Database Required**: No
- **Official Documentation**: https://github.com/ParBLiSS/FastANI
- **Citation**: https://doi.org/10.1038/s41467-018-07641-9

---

## Overview

FastANI is a fast alignment-free implementation for computing whole-genome Average Nucleotide Identity (ANI). ANI is the standard for defining bacterial species (>95% ANI usually indicates the same species). It handles thousands of genomes efficiently.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/fastani:latest fastANI [options]
```

### Common Use Cases

1. **Compare two genomes**
   ```bash
   docker run --rm -v /data:/data staphb/fastani:latest \
     fastANI -q /data/genome1.fa -r /data/genome2.fa -o /data/ani_out.txt
   ```

2. **Compare one query against many references**
   ```bash
   docker run --rm -v /data:/data staphb/fastani:latest \
     fastANI -q /data/query.fa --rl /data/ref_list.txt -o /data/results.txt
   ```

3. **All-vs-all comparison (Matrix output)**
   ```bash
   docker run --rm -v /data:/data staphb/fastani:latest \
     fastANI --ql /data/genome_list.txt --rl /data/genome_list.txt -o /data/matrix.txt --matrix
   ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `-q, --query` | Query genome (FASTA) | - |
| `--ql, --queryList` | File containing list of query genomes | - |
| `-r, --ref` | Reference genome (FASTA) | - |
| `--rl, --refList` | File containing list of reference genomes | - |
| `-o, --output` | Output file name | - |
| `-t, --threads` | Number of threads | 1 |
| `--matrix` | Output ANI as a lower triangular matrix | off |
| `-k, --kmer` | Kmer size (<= 16) | 16 |

---

## Important Notes

- ⚠️ **Species Definition**: ANI values >= 95% often indicate that the two genomes belong to the same species.
- ⚠️ **Fraction**: Look at the "fraction of fragments matched" in the output. If it's very low (<0.2), the ANI value might not be reliable.
- ⚠️ **Fast**: FastANI is much faster than BLAST-based ANI methods (like ANIb).

---

## Examples for Agent

### Example 1: Verify Species Identity
**User Request**: "Compare my new assembly against the reference genome for E. coli"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/fastani:latest \
  fastANI -q /data/my_assembly.fa -r /data/references/E_coli_K12.fa -o /data/ani_results.txt
```

---

## Troubleshooting

### Common Errors

1. **Error**: `File not found`  
   **Solution**: Ensure all genome files listed in `--ql` or `--rl` are accessible within the container's volume mounts and use absolute paths relative to the mount point.

2. **Error**: `ANI value is 0`  
   **Solution**: This typically means the genomes are too divergent for FastANI to find enough matches. This is expected for genomes from different genera or far-related families.
        
