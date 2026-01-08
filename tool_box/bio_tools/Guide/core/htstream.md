# HTStream

## Metadata
- **Version**: latest (via BioContainers)
- **Docker Image**: `quay.io/biocontainers/htstream:latest`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: https://s4hts.github.io/HTStream/
- **Citation**: https://doi.org/10.1101/2020.06.03.132183

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data quay.io/biocontainers/htstream:latest [command] [args]
```

### Common Use Cases

1. **Preprocess Reads with `hts_SuperDeduper`**
   ```bash
   docker run --rm -v /data:/data quay.io/biocontainers/htstream:latest hts_SuperDeduper -1 /data/input_R1.fastq -2 /data/input_R2.fastq -f /data/dedup
   ```

2. **Quality Trimming with `hts_QWindowTrim`**
   ```bash
   docker run --rm -v /data:/data quay.io/biocontainers/htstream:latest hts_QWindowTrim -1 /data/input_R1.fastq -2 /data/input_R2.fastq -f /data/trimmed
   ```

3. **Adapter Removal with `hts_AdapterTrimmer`**
   ```bash
   docker run --rm -v /data:/data quay.io/biocontainers/htstream:latest hts_AdapterTrimmer -1 /data/input_R1.fastq -2 /data/input_R2.fastq -f /data/adapter_removed
   ```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `hts_SuperDeduper` | Remove PCR duplicates |
| `hts_AdapterTrimmer` | Remove adapter sequences |
| `hts_QWindowTrim` | Quality trimming using a sliding window |
| `hts_Stats` | Compute statistics for FastQ files |
| `hts_PolyATTrim` | Remove PolyA/T tails |
| `hts_NTrimmer` | Remove N characters |
| `hts_SeqScreener` | Screen for contaminants |
| `hts_Overlapper` | Overlap paired-end reads |

---

## Examples for Agent

### Example 1: Basic Stats Calculation
**User Request**: "计算这个 FASTQ 文件的统计信息"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  quay.io/biocontainers/htstream:latest \
  hts_Stats -1 /data/input.fastq -L /data/stats.json -f /data/stats_out
```

### Example 2: Remove Duplicates
**User Request**: "去除 PCR 重复"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  quay.io/biocontainers/htstream:latest \
  hts_SuperDeduper -1 /data/reads_R1.fastq -2 /data/reads_R2.fastq -f /data/dedup
```
