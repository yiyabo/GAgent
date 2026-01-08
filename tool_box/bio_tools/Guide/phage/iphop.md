# iPHoP

## Metadata
- **Version**: latest (via BioContainers)
- **Docker Image**: `quay.io/biocontainers/iphop:latest`
- **Category**: phage
- **Database Required**: Yes (iPHoP database)
- **Official Documentation**: https://bitbucket.org/srouxjgi/iphop/src/main/
- **Citation**: https://doi.org/10.1371/journal.pbio.3002083

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data -v /path/to/db:/db quay.io/biocontainers/iphop:latest iphop [command] [args]
```

### Common Use Cases

1. **Predict Host (Full Pipeline)**
   ```bash
   docker run --rm -v /data:/data -v /database/iphop_db:/db quay.io/biocontainers/iphop:latest iphop predict --fa_file /data/viruses.fasta --db_dir /db --out_dir /data/iphop_output
   ```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `iphop predict` | Predict host taxonomy for input phages |
| `iphop split` | Split input file for parallel processing |
| `iphop download` | Download necessary databases |

---

## Important Notes
- **Databases**: Requires the iPHoP database (~20GB+) to be downloaded and mounted.

## Examples for Agent

### Example 1: Predict Hosts
**User Request**: "预测这些噬菌体的宿主"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/database/iphop:/db \
  quay.io/biocontainers/iphop:latest \
  iphop predict --fa_file /data/phage_sequences.fasta --db_dir /db --out_dir /data/host_predictions --num_threads 8
```
