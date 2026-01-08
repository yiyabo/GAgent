# MetaBAT2

## Metadata
- **Version**: 2.15 (via BioContainers)
- **Docker Image**: `quay.io/biocontainers/metabat2:2.15--h988d1d8_2`
- **Category**: binning
- **Database Required**: No
- **Official Documentation**: https://bitbucket.org/berkeleylab/metabat/src/master/
- **Citation**: https://doi.org/10.7717/peerj-preprints.27522v1

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data quay.io/biocontainers/metabat2:2.15--h988d1d8_2 metabat2 [options]
```

### Common Use Cases

1. **Binning Contigs**
   ```bash
   docker run --rm -v /data:/data quay.io/biocontainers/metabat2:2.15--h988d1d8_2 metabat2 -i /data/contigs.fasta -a /data/depth.txt -o /data/bins/bin
   ```

2. **Generate Depth File (using `jgi_summarize_bam_contig_depths`)**
   ```bash
   docker run --rm -v /data:/data quay.io/biocontainers/metabat2:2.15--h988d1d8_2 jgi_summarize_bam_contig_depths --outputDepth /data/depth.txt /data/sorted.bam
   ```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `metabat2` | Run MetaBAT2 binning |
| `jgi_summarize_bam_contig_depths` | Calculate contig depth from BAM files |

---

## Examples for Agent

### Example 1: Basic Binning
**User Request**: "对这些 contigs 进行分箱"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  quay.io/biocontainers/metabat2:2.15--h988d1d8_2 \
  metabat2 -i /data/assembly.fasta -a /data/depth.txt -o /data/bins/bin -t 4
```

### Example 2: Calculate Depth
**User Request**: "计算 BAM 文件的深度信息"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  quay.io/biocontainers/metabat2:2.15--h988d1d8_2 \
  jgi_summarize_bam_contig_depths --outputDepth /data/depth.txt /data/mapping.bam
```
