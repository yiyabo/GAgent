# MaxBin2

## Metadata
- **Version**: 2.2.7 (via BioContainers)
- **Docker Image**: `nanozoo/maxbin2:2.2.7--e1577a7`
- **Category**: binning
- **Database Required**: No
- **Official Documentation**: https://sourceforge.net/projects/maxbin2/
- **Citation**: https://doi.org/10.1093/bioinformatics/btv638

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data nanozoo/maxbin2:2.2.7--e1577a7 run_MaxBin.pl [options]
```

### Common Use Cases

1. **Binning with Abundance File**
   ```bash
   docker run --rm -v /data:/data nanozoo/maxbin2:2.2.7--e1577a7 run_MaxBin.pl -contig /data/contigs.fasta -abund /data/abundance.txt -out /data/bins/bin
   ```

2. **Binning with Reads (Auto Abundance Calculation)**
   ```bash
   docker run --rm -v /data:/data nanozoo/maxbin2:2.2.7--e1577a7 run_MaxBin.pl -contig /data/contigs.fasta -reads /data/reads.fastq -out /data/bins/bin
   ```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `run_MaxBin.pl` | Run MaxBin2 binning pipeline |

---

## Examples for Agent

### Example 1: Basic Binning
**User Request**: " MaxBin2  contigs "

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  nanozoo/maxbin2:2.2.7--e1577a7 \
  run_MaxBin.pl -contig /data/assembly.fasta -abund /data/abundance.txt -out /data/output_bin -thread 4
```
