# DAS Tool

## Metadata
- **Version**: [latest] (Check `DAS_Tool -v`)
- **Full Name**: DAS Tool - Recovery of high-quality metagenome-assembled genomes
- **Docker Image**: `staphb/das_tool:latest`
- **Category**: binning
- **Database Required**: Yes (Single copy gene database)
- **Official Documentation**: https://github.com/cmbi/DAS_Tool
- **Citation**: https://doi.org/10.1038/s41564-018-0171-1

---

## Overview

DAS Tool is an automated binning refinement method that integrates the results of multiple binning algorithms implemented in a single tool. It calculates an optimized set of bins (MAGs) by selecting the best bins from multiple input sets based on single-copy gene (SCG) completeness and redundancy.

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/das_tool:latest DAS_Tool [options]
```

### Common Use Cases

1. **Refine bins from multiple tools (e.g., MetaBAT2, CONCOCT)**
   ```bash
   docker run --rm -v /data:/data staphb/das_tool:latest \
     DAS_Tool -i /data/binning/metabat2.csv,/data/binning/concoct.csv \
     -c /data/assembly/contigs.fa -o /data/binning/das_tool_out \
     -l metabat2,concoct --write_bins
   ```

---

## Command Reference (Common Options)

| Option | Description | Default |
|--------|-------------|---------|
| `-i, --bins` | Comma separated list of contig-to-bin tables (TSV) | - |
| `-c, --contigs` | Contigs in FASTA format | - |
| `-o, --outputbasename` | Basename of output files | - |
| `-l, --labels` | Comma separated list of labels for each binning set | - |
| `--search_engine` | Engine for SCG identification (diamond/blastp/usearch) | diamond |
| `-t, --threads` | Number of threads to use | 1 |
| `--write_bins` | Export refined bins as FASTA files | off |

---

## Important Notes

- ⚠️ **Input Format**: Input tables must be tab-separated with two columns: `contig_id` and `bin_id`.
- ⚠️ **Labels**: The number of labels provided with `-l` must match the number of input files provided with `-i`.
- ⚠️ **Gold Standard**: DAS Tool is often used as the "gold standard" final step in binning pipelines to get the best possible MAGs.

---

## Examples for Agent

### Example 1: Multi-Tool Refinement
**User Request**: "I ran MetaBAT2 and MaxBin2. Now use DAS Tool to pick the best bins from both."

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/das_tool:latest \
  DAS_Tool -i /data/bins/metabat.tsv,/data/bins/maxbin.tsv \
  -c /data/assembly/final.contigs.fa \
  -l metabat,maxbin \
  -o /data/bins/refined_results \
  --write_bins -t 8
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Diamond database not found`  
   **Solution**: Ensure the database directory is correctly pointed to using `--dbDirectory` if it's not in the default location within the container.

2. **Error**: `No bins selected`  
   **Solution**: This can happen if the input bins are of very low quality (low SCG completeness). Check the component binning results first.
