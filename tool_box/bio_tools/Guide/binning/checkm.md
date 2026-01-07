# CheckM

## Metadata
- **Version**: [latest] (Check `checkm -v`)
- **Full Name**: CheckM - Assess the quality of metagenome-assembled genomes
- **Docker Image**: `staphb/checkm:latest`
- **Category**: binning
- **Database Required**: Yes - `checkm_data` (~50GB)
- **Official Documentation**: https://github.com/Ecogenomics/CheckM
- **Citation**: https://doi.org/10.1101/gr.186072.114

---

## Overview

CheckM provides a set of tools for assessing the quality of genomes recovered from isolates, single cells, or metagenomes. It estimates:
- **Completeness**: Presence of lineage-specific marker genes
- **Contamination**: Duplication of lineage-specific marker genes
- **Strain Heterogeneity**: Genetic diversity within a bin

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/checkm:latest checkm [command] [options]
```

### Common Use Cases

1. **Full lineage-specific workflow (lineage_wf)**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/checkm/db:/checkm_data \
     staphb/checkm:latest \
     checkm lineage_wf /data/bins/ /data/checkm_out/ -x fna -t 8
   ```

2. **Calculate stats for a single bin**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/checkm/db:/checkm_data \
     staphb/checkm:latest \
     checkm qa /data/checkm_out/lineage.ms /data/checkm_out/ -o 2 --tab_table
   ```

---

## Command Reference (Common Options for lineage_wf)

| Option | Description | Default |
|--------|-------------|---------|
| `bin_input` | Directory containing bins in FASTA format | - |
| `output_dir` | Directory to write results | - |
| `-x, --extension` | Extension of bin files | `fna` |
| `-t, --threads` | Number of threads | 1 |
| `-r, --reduced_tree` | Use a reduced tree (requires less memory, <16GB) | off |
| `--tab_table` | Output results in a tab-separated table | off |

---

## Important Notes

- ⚠️ **Database**: CheckM requires a large reference database (`checkm_data`). You must set the path using `checkm data setroot` or mount it correctly.
- ⚠️ **Memory**: The standard lineage workflow requires >16GB of RAM. Use `--reduced_tree` if memory is limited.
- ⚠️ **Markers**: It uses HMMER to search for lineage-specific single-copy marker genes.

---

## Examples for Agent

### Example 1: Assess MAG Quality
**User Request**: "Check the completeness and contamination of the bins I just generated"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/checkm/db:/checkm_data \
  staphb/checkm:latest \
  checkm lineage_wf /data/bins/ /data/checkm_results -x fa -t 16 --tab_table -f /data/checkm_summary.tsv
```

---

## Troubleshooting

### Common Errors

1. **Error**: `[Error] LookUpError: CheckM data is not yet set.`  
   **Solution**: Ensure the database is mounted and the internal path is set. In many Docker images, this is pre-configured, but you may need to check the mount point.

2. **Error**: `pplacer crashed`  
   **Solution**: This is often due to memory exhaustion. Try using `--reduced_tree` and reducing `--pplacer_threads`.
