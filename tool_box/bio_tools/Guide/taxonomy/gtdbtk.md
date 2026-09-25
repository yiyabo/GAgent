# GTDB-Tk

## Metadata
- **Version**: 2.3.2 (via BioContainers)
- **Docker Image**: `quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0`
- **Category**: taxonomy
- **Database Required**: Yes (GTDB-Tk reference data)
- **Official Documentation**: https://ecogenomics.github.io/GTDBTk/
- **Citation**: https://doi.org/10.1093/bioinformatics/btz848

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data -v /path/to/db:/refdata quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0 gtdbtk [command] [args]
```

### Common Use Cases

1. **Classify Genomes (classify_wf)**
   ```bash
   docker run --rm -v /data:/data -v /database/gtdb_release214:/refdata quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0 gtdbtk classify_wf --genome_dir /data/genomes --out_dir /data/output --cpus 8
   ```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `classify_wf` | Complete classification workflow (identify, align, classify) |
| `identify` | Identify marker genes |
| `align` | Align marker genes |
| `classify` | Determine taxonomy based on placement |

---

## Important Notes
- **Database**: Requires the GTDB-Tk reference data extracted to a folder.
- **Environment Variable**: Set `GTDBTK_DATA_PATH` if not using default paths (usually handled by mounting to specific location or setting env var).
  - Recommended: `-e GTDBTK_DATA_PATH=/refdata`

## Examples for Agent

### Example 1: Classify Genomes
**User Request**: ""

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/database/gtdb:/refdata \
  -e GTDBTK_DATA_PATH=/refdata \
  quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0 \
  gtdbtk classify_wf --genome_dir /data/input_genomes --out_dir /data/gtdbtk_output --extension fa --cpus 8
```
