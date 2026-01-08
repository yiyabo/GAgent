# VIBRANT

## Metadata
- **Version**: 1.2.1 (via BioContainers)
- **Docker Image**: `quay.io/biocontainers/vibrant:1.2.1--pyhdfd78af_0`
- **Category**: phage
- **Database Required**: Yes (VIBRANT databases)
- **Official Documentation**: https://github.com/AnantharamanLab/VIBRANT
- **Citation**: https://doi.org/10.1186/s40168-020-00867-0

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data -v /path/to/db:/db quay.io/biocontainers/vibrant:1.2.1--pyhdfd78af_0 VIBRANT_run.py [args]
```

### Common Use Cases

1. **Identify Viruses in Scaffolds**
   ```bash
   docker run --rm -v /data:/data -v /database/vibrant_db:/db -e VIBRANT_DATA_PATH=/db quay.io/biocontainers/vibrant:1.2.1--pyhdfd78af_0 VIBRANT_run.py -i /data/scaffolds.fasta -fnuapS -t 4
   ```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `VIBRANT_run.py` | Main VIBRANT pipeline script |

---

## Important Notes
- **Databases**: Requires VIBRANT databases to be downloaded and mounted.
- **Environment**: May need to set path to databases or mount them into the expected location within the container.

## Examples for Agent

### Example 1: Run VIBRANT
**User Request**: "运行 VIBRANT 识别病毒"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/database/vibrant:/db \
  -e VIBRANT_DATA_PATH=/db \
  quay.io/biocontainers/vibrant:1.2.1--pyhdfd78af_0 \
  VIBRANT_run.py -i /data/assembly.fasta -d /db/databases -o /data/vibrant_out -t 4
```
