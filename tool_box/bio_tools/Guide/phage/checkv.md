# CheckV

## Metadata
- **Version**: 1.0.1
- **Full Name**: CheckV - Assessing the quality of metagenome-assembled viral genomes
- **Docker Image**: `quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0`
- **Category**: phage
- **Database Required**: Yes - `checkv-db-v1.5` (~2GB)
- **Official Documentation**: https://bitbucket.org/berkeleylab/checkv
- **Citation**: https://doi.org/10.1038/s41587-020-00774-7

---

## Overview

CheckV is a fully automated pipeline for assessing the quality of single-contig viral genomes:
- **Completeness estimation**: Estimate how complete your viral genome is
- **Contamination detection**: Identify and remove host contamination from proviruses
- **Complete genome identification**: Detect closed/complete genomes based on terminal repeats
- **Quality summary**: Generate comprehensive quality reports

---

## Quick Start

### Basic Usage
```bash
docker run --rm \
  -v /path/to/data:/data \
  -v /path/to/database:/database \
  quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
  checkv <program> [options]
```

### Common Use Cases

1. **Full quality assessment pipeline (recommended)**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/checkv/checkv-db-v1.5:/database \
     quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
     checkv end_to_end /data/viruses.fasta /data/checkv_output -d /database -t 8
   ```

2. **Completeness estimation only**
   ```bash
   docker run --rm \
     -v /data:/data \
     -v /data/databases/bio_tools/checkv/checkv-db-v1.5:/database \
     quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
     checkv completeness /data/viruses.fasta /data/checkv_output -d /database -t 8
   ```

3. **Identify complete genomes**
   ```bash
   docker run --rm \
     -v /data:/data \
     quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
     checkv complete_genomes /data/viruses.fasta /data/checkv_output
   ```

---

## Full Help Output

### Main Program
```
CheckV v1.0.1: assessing the quality of metagenome-assembled viral genomes
https://bitbucket.org/berkeleylab/checkv

usage: checkv <program> [options]

programs:
    end_to_end          run full pipeline to estimate completeness, contamination, and identify closed genomes
    contamination       identify and remove host contamination on integrated proviruses
    completeness        estimate completeness for genome fragments
    complete_genomes    identify complete genomes based on terminal repeats and flanking host regions
    quality_summary     summarize results across modules
    download_database   download the latest version of CheckV's database
    update_database     update CheckV's database with your own complete genomes

options:
  -h, --help  show this help message and exit
```

---

## Command Reference

### end_to_end
Run full pipeline to estimate completeness, contamination, and identify closed genomes.

```
usage: checkv end_to_end <input> <output> [options]

positional arguments:
  input         Input nucleotide sequences in FASTA format (.gz, .bz2 and .xz files are supported)
  output        Output directory

options:
  -h, --help    show this help message and exit
  -d PATH       Reference database path. By default the CHECKVDB environment variable is used
  --remove_tmp  Delete intermediate files from the output directory
  -t INT        Number of threads to use for Prodigal and DIAMOND
  --restart     Overwrite existing intermediate files. By default CheckV continues where program left off
  --quiet       Suppress logging messages
```

| Option | Description | Default |
|--------|-------------|---------|
| `-d PATH` | Reference database path | `$CHECKVDB` |
| `-t INT` | Number of threads | 1 |
| `--remove_tmp` | Delete intermediate files | off |
| `--restart` | Overwrite existing files | off |
| `--quiet` | Suppress logging | off |

---

### completeness
Estimate completeness for genome fragments.

```
usage: checkv completeness <input> <output> [options]

positional arguments:
  input       Input nucleotide sequences in FASTA format (.gz, .bz2 and .xz files are supported)
  output      Output directory

options:
  -h, --help  show this help message and exit
  -d PATH     Reference database path. By default the CHECKVDB environment variable is used
  -t INT      Number of threads to use for Prodigal and DIAMOND
  --restart   Overwrite existing intermediate files. By default CheckV continues where program left off
  --quiet     Suppress logging messages
```

| Option | Description | Default |
|--------|-------------|---------|
| `-d PATH` | Reference database path | `$CHECKVDB` |
| `-t INT` | Number of threads | 1 |
| `--restart` | Overwrite existing files | off |
| `--quiet` | Suppress logging | off |

---

### complete_genomes
Identify complete genomes based on terminal repeats and flanking host regions.

```
usage: checkv complete_genomes <input> <output> [options]

positional arguments:
  input                 Input nucleotide sequences in FASTA format (.gz, .bz2 and .xz files are supported)
  output                Output directory

options:
  -h, --help            show this help message and exit
  --tr_min_len INT      Min length of TR (20)
  --tr_max_count INT    Max occurences of TR per contig (8)
  --tr_max_ambig FLOAT  Max fraction of TR composed of Ns (0.20)
  --tr_max_basefreq FLOAT
                        Max fraction of TR composed of single nucleotide (0.75)
  --kmer_max_freq FLOAT
                        Max kmer frequency (1.5). Computed by splitting genome into kmers, counting 
                        occurence of each kmer, and taking the average count. Expected value of 1.0 
                        for no duplicated regions; 2.0 for the same genome repeated back-to-back
  --quiet               Suppress logging messages
```

| Option | Description | Default |
|--------|-------------|---------|
| `--tr_min_len INT` | Minimum length of terminal repeat | 20 |
| `--tr_max_count INT` | Maximum occurrences of TR per contig | 8 |
| `--tr_max_ambig FLOAT` | Maximum fraction of TR composed of Ns | 0.20 |
| `--tr_max_basefreq FLOAT` | Maximum fraction of TR composed of single nucleotide | 0.75 |
| `--kmer_max_freq FLOAT` | Maximum kmer frequency | 1.5 |
| `--quiet` | Suppress logging | off |

---

### quality_summary
Summarize results across modules.

```
usage: checkv quality_summary <input> <output> [options]

positional arguments:
  input         Input viral sequences in FASTA format
  output        Output directory

options:
  -h, --help    show this help message and exit
  --remove_tmp  Delete intermediate files from the output directory
  --quiet       Suppress logging messages
```

---

### contamination
Identify and remove host contamination on integrated proviruses.

```
usage: checkv contamination <input> <output> [options]

positional arguments:
  input       Input nucleotide sequences in FASTA format
  output      Output directory

options:
  -h, --help  show this help message and exit
  -d PATH     Reference database path
  -t INT      Number of threads
  --restart   Overwrite existing intermediate files
  --quiet     Suppress logging messages
```

---

### download_database
Download the latest version of CheckV's database.

```
usage: checkv download_database <destination>

positional arguments:
  destination   Directory to save the database
```

---

### update_database
Update CheckV's database with your own complete genomes.

```
usage: checkv update_database <input> <source_db> <dest_db> [options]

positional arguments:
  input       Input complete viral genomes in FASTA format
  source_db   Path to source CheckV database
  dest_db     Path to output updated database
```

---

## Module Summary

| Module | Purpose | Requires Database |
|--------|---------|-------------------|
| `end_to_end` | Full QC pipeline | Yes |
| `contamination` | Remove host contamination | Yes |
| `completeness` | Estimate genome completeness | Yes |
| `complete_genomes` | Find complete genomes via terminal repeats | No |
| `quality_summary` | Generate final summary | No |
| `download_database` | Download reference database | N/A |
| `update_database` | Add custom genomes to database | Yes |

---

## Important Notes

- **Memory**: Moderate (~4-8GB for typical datasets)
- **Runtime**: Minutes to hours depending on input size
- **Input**: FASTA format (.gz, .bz2, .xz supported)
- **Output**: Multiple TSV files with quality metrics
- **Database**: Required for most modules; download with `download_database`

---

## Output Files

After running `end_to_end`, the output directory contains:

| File | Description |
|------|-------------|
| `quality_summary.tsv` | Main quality report with completeness and contamination |
| `completeness.tsv` | Detailed completeness estimates |
| `contamination.tsv` | Host contamination regions |
| `complete_genomes.tsv` | List of complete genomes |
| `proviruses.fna` | Extracted provirus sequences |
| `viruses.fna` | Cleaned viral sequences (contamination removed) |

### Quality Categories

| Category | Completeness | Contamination |
|----------|--------------|---------------|
| Complete | 100% | 0% |
| High-quality | >90% | <5% |
| Medium-quality | 50-90% | <10% |
| Low-quality | <50% | - |
| Not-determined | Unknown | - |

---

## Examples for Agent

### Example 1: Full Quality Assessment
**User Request**: "Assess the quality of these predicted viral contigs"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/checkv/checkv-db-v1.5:/database \
  quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
  checkv end_to_end \
    /data/viral_contigs.fasta \
    /data/checkv_results \
    -d /database \
    -t 8 \
    --remove_tmp
```

**Expected Output**:
```
checkv_results/
├── quality_summary.tsv      # Main quality report
├── completeness.tsv         # Completeness estimates
├── contamination.tsv        # Contamination details
├── complete_genomes.tsv     # Complete genome list
├── proviruses.fna           # Provirus sequences
└── viruses.fna              # Cleaned viral sequences
```

### Example 2: Check if Genomes are Complete
**User Request**: "Find which viral genomes are complete (circular or with terminal repeats)"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
  checkv complete_genomes \
    /data/viral_contigs.fasta \
    /data/complete_check
```

### Example 3: Estimate Completeness Only
**User Request**: "Estimate how complete these viral genome fragments are"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/checkv/checkv-db-v1.5:/database \
  quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
  checkv completeness \
    /data/viral_fragments.fasta \
    /data/completeness_results \
    -d /database \
    -t 8
```

### Example 4: Remove Host Contamination from Proviruses
**User Request**: "Clean host contamination from these provirus sequences"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  -v /data/databases/bio_tools/checkv/checkv-db-v1.5:/database \
  quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0 \
  checkv contamination \
    /data/proviruses.fasta \
    /data/cleaned_proviruses \
    -d /database \
    -t 8
```

---

## Troubleshooting

### Common Errors

1. **Error**: `Database not found`  
   **Solution**: Specify database path with `-d` or set `CHECKVDB` environment variable

2. **Error**: `No viral sequences detected`  
   **Solution**: Ensure input contains viral sequences; run a prediction tool (geNomad, VirSorter2) first

3. **Error**: `DIAMOND database error`  
   **Solution**: Re-download the database; it may be corrupted

4. **Error**: `Out of memory`  
   **Solution**: Reduce the number of threads (`-t`) or split input into smaller files

---

## Integration with Other Tools

CheckV is typically used **after** viral prediction tools:

```
Metagenome → geNomad/VirSorter2 → CheckV → High-quality viruses
```

Workflow example:
1. Predict viruses with geNomad or VirSorter2
2. Assess quality with CheckV
3. Filter for high-quality genomes (>50% complete, <10% contamination)
4. Annotate with pharokka
