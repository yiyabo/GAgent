# geNomad

## Metadata
- **Version**: 1.7.6
- **Full Name**: geNomad - Identification of mobile genetic elements
- **Docker Image**: `quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0`
- **Category**: phage
- **Database Required**: Yes - `genomad_db` (~5GB)
- **Official Documentation**: https://portal.nersc.gov/genomad/
- **Citation**: https://doi.org/10.1038/s41587-023-01953-y

---

## ⚠️ Hardware Requirements

> **WARNING**: This tool requires significant computational resources.  
> **Zhao server performance is insufficient to run this tool.**  
> Recommend using a machine with at least 32GB RAM and GPU support for optimal performance.

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| RAM | 16GB | 32GB+ |
| CPU | 4 cores | 16+ cores |
| GPU | Optional | Recommended for nn-classification |
| Storage | 10GB | 50GB+ (for database + outputs) |

---

## Overview

geNomad is a state-of-the-art tool for identifying mobile genetic elements (viruses and plasmids) in sequencing data. It combines:
- **Marker-based classification**: Using curated protein markers
- **Neural network classification**: Deep learning for sequence classification
- **Provirus detection**: Finding integrated viruses in host genomes

---

## Quick Start

### Basic Usage
```bash
docker run --rm \
  -v /path/to/data:/data \
  -v /path/to/database:/database \
  quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0 \
  genomad [command] [args]
```

### Common Use Cases

1. **End-to-end analysis (recommended for most users)**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/genomad:/database \
     quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0 \
     genomad end-to-end /data/input.fasta /data/output /database/genomad_db
   ```

2. **Download database**
   ```bash
   docker run --rm \
     -v /data/databases/bio_tools/genomad:/database \
     quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0 \
     genomad download-database /database
   ```

---

## Full Help Output

```
 Usage: genomad [OPTIONS] COMMAND [ARGS]...                                     
                                                                                
 geNomad: Identification of mobile genetic elements                             
 Read the documentation at: https://portal.nersc.gov/genomad/                   
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│  --version        Show the version and exit.                                 │
│  --help      -h   Show this message and exit.                                │
╰──────────────────────────────────────────────────────────────────────────────╯

╭─ Database download ──────────────────────────────────────────────────────────╮
│   download-database   Download the latest version of geNomad's database      │
│                       and save it in the DESTINATION directory.              │
╰──────────────────────────────────────────────────────────────────────────────╯

╭─ End-to-end execution ───────────────────────────────────────────────────────╮
│   end-to-end   Takes an INPUT file (FASTA format) and executes all modules   │
│                of the geNomad pipeline for plasmid and virus                 │
│                identification. Output files are written in the OUTPUT        │
│                directory. A local copy of geNomad's database (DATABASE       │
│                directory) is required.                                       │
╰──────────────────────────────────────────────────────────────────────────────╯

╭─ Modules ────────────────────────────────────────────────────────────────────╮
│   annotate                    Predict genes and annotate using markers       │
│   find-proviruses             Find integrated viruses in sequences           │
│   marker-classification       Classify based on marker presence              │
│   nn-classification           Classify using neural network                  │
│   aggregated-classification   Aggregate marker and nn classification         │
│   score-calibration           Calibrate scores using batch correction        │
│   summary                     Apply filters and generate reports             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

---

## Module Reference

| Module | Description | Dependencies |
|--------|-------------|--------------|
| `annotate` | Gene prediction and marker annotation | None |
| `find-proviruses` | Detect integrated viruses | annotate |
| `marker-classification` | Classify by marker presence | annotate |
| `nn-classification` | Neural network classification | None |
| `aggregated-classification` | Combine marker + nn results | marker-classification, nn-classification |
| `score-calibration` | Batch correction for scores | Any classification module |
| `summary` | Generate final reports | Any classification module |

---

## Important Notes

- **Memory**: High memory usage, especially for nn-classification
- **Runtime**: Depends on input size; large metagenomes can take hours
- **Input**: FASTA format (.fasta, .fna, .fa)
- **Output**: Multiple files including virus/plasmid predictions, gene annotations
- **Database**: Must download database first using `download-database`

---

## Examples for Agent

### Example 1: Full Phage/Plasmid Prediction Pipeline
**User Request**: "Identify phages and plasmids in my metagenome contigs"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/genomad:/database \
  quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0 \
  genomad end-to-end \
    /data/contigs.fasta \
    /data/genomad_output \
    /database/genomad_db
```

**Expected Output**:
```
genomad_output/
├── contigs_annotate/           # Gene annotations
├── contigs_find_proviruses/    # Provirus regions
├── contigs_marker_classification/
├── contigs_nn_classification/
├── contigs_aggregated_classification/
└── contigs_summary/
    ├── contigs_virus.fna       # Predicted virus sequences
    ├── contigs_plasmid.fna     # Predicted plasmid sequences
    └── contigs_virus_summary.tsv
```

### Example 2: Find Proviruses Only
**User Request**: "Find integrated prophages in bacterial genomes"

**Agent Command**:
```bash
# Step 1: Annotate
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/genomad:/database \
  quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0 \
  genomad annotate /data/genome.fasta /data/output /database/genomad_db

# Step 2: Find proviruses
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/genomad:/database \
  quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0 \
  genomad find-proviruses /data/genome.fasta /data/output /database/genomad_db
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Out of memory`  
   **Solution**: Use a machine with more RAM or reduce input file size

2. **Error**: `Database not found`  
   **Solution**: Run `download-database` first or check database path

3. **Error**: `CUDA out of memory`  
   **Solution**: Disable GPU with `--disable-nn-classification` or use CPU-only mode
