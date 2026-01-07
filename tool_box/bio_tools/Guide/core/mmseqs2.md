# MMseqs2

## Metadata
- **Version**: 14.7e284
- **Full Name**: MMseqs2 - Many-against-Many sequence searching
- **Docker Image**: `staphb/mmseqs2:latest` (or `soedinglab/mmseqs2:latest`)
- **Category**: core
- **Database Required**: No (but can build/download many)
- **Official Documentation**: https://mmseqs.com/latest/userguide.pdf
- **Citation**: https://doi.org/10.1038/nbt.3988

---

## Overview

MMseqs2 is a software suite for very fast protein sequence searching and clustering. It is designed to handle huge datasets with millions of sequences. It is:
- **Ultra-fast**: 100x to 1000x faster than BLAST.
- **Sensitive**: Maintains high sensitivity for homology search.
- **Versatile**: Supports searching, clustering, and taxonomic assignment.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/mmseqs2:latest mmseqs <command> [arguments]
```

### Common Workflows (Easy interface)

1. **Clustering (Linear time)**
   ```bash
   docker run --rm -v /data:/data staphb/mmseqs2:latest \
     mmseqs easy-linclust /data/proteins.fasta /data/cluster_out /data/tmp --min-seq-id 0.9
   ```

2. **Homology Search**
   ```bash
   docker run --rm -v /data:/data staphb/mmseqs2:latest \
     mmseqs easy-search /data/query.fasta /data/target.fasta /data/aln.m8 /data/tmp
   ```

3. **Taxonomic Classification**
   ```bash
   docker run --rm -v /data:/data staphb/mmseqs2:latest \
     mmseqs easy-taxonomy /data/query.fasta /data/db/nr /data/taxonomy_out /data/tmp
   ```

---

## Command Categories

| Category | Commands |
|----------|----------|
| **Easy Workflows** | `easy-search`, `easy-linsearch`, `easy-cluster`, `easy-linclust`, `easy-taxonomy` |
| **Main Workflows** | `search`, `linsearch`, `map`, `cluster`, `linclust`, `taxonomy` |
| **DB Handling** | `createdb`, `databases`, `createindex`, `rmdb`, `touchdb` |
| **Result Processing** | `convertalis`, `createtsv`, `convert2fasta`, `taxonomyreport` |

---

## Important Notes

- ⚠️ **Temporary Directory**: Always specify a temporary directory (e.g., `/data/tmp`) as MMseqs2 generates many intermediate files.
- ⚠️ **Linear Clustering**: `easy-linclust` is recommended for huge datasets where standard `cluster` is too slow.
- ⚠️ **Databases**: You can download pre-built databases like Uniref90 or NR using `mmseqs databases`.

---

## Examples for Agent

### Example 1: Protein sequence clustering
**User Request**: "Cluster my protein sequences at 90% identity to remove duplicates"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/mmseqs2:latest \
  mmseqs easy-linclust /data/all_proteins.faa /data/clustered_90 /data/tmp --min-seq-id 0.9 --cov-mode 1
```

### Example 2: Fast protein search
**User Request**: "Search these proteins against the target database (m8 tabular output)"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/mmseqs2:latest \
  mmseqs easy-search /data/query.faa /data/target.faa /data/results.m8 /data/tmp -s 7.5 --threads 16
```

---

## Troubleshooting

### Common Errors

1. **Error**: `No such file or directory (tmp)`  
   **Solution**: Ensure you have created the temporary directory or provided a valid path that is mounted in Docker.

2. **Error**: `Out of memory`  
   **Solution**: MMseqs2 uses significant RAM for indexing. Use `--split` for very large database searches to process them in chunks.
