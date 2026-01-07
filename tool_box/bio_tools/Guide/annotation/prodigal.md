# Prodigal

## Metadata
- **Version**: 2.6.3
- **Full Name**: Prokaryotic Dynamic Ingen Ab initio Location
- **Docker Image**: `staphb/prodigal:2.6.3`
- **Category**: annotation
- **Database Required**: No (ab initio prediction)
- **Official Documentation**: https://github.com/hyattpd/Prodigal
- **Developer**: Oak Ridge National Laboratory (ORNL) / University of Tennessee
- **Citation**: https://doi.org/10.1186/1471-2105-11-119

---

## Overview

| Property | Description |
|----------|-------------|
| Purpose  | Predict protein-coding genes from prokaryotic (bacteria, phage) DNA sequences |
| Advantage | Specifically optimized for prokaryotes, no training data required |
| Output   | Gene annotations in GFF3, GenBank, or SCO format |

### Why Use Prodigal?

| Tool     | Target Organisms | Reason |
|----------|------------------|--------|
| **Prodigal** | Bacteria, Phages | Specifically optimized for prokaryotes |
| Genscan  | Eukaryotes | Not suitable for phages |
| AUGUSTUS | Eukaryotes | Not suitable for phages |

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/prodigal:2.6.3 prodigal [options]
```

### Common Use Cases

1. **Standard gene prediction (single genome)**
   ```bash
   docker run --rm -v /data:/data staphb/prodigal:2.6.3 \
     prodigal -i /data/genome.fasta -o /data/genes.gff -f gff -a /data/proteins.faa
   ```

2. **Metagenomic mode (multiple short fragments)**
   ```bash
   docker run --rm -v /data:/data staphb/prodigal:2.6.3 \
     prodigal -i /data/contigs.fasta -o /data/genes.gff -f gff -p meta -a /data/proteins.faa
   ```

3. **Output nucleotide sequences**
   ```bash
   docker run --rm -v /data:/data staphb/prodigal:2.6.3 \
     prodigal -i /data/genome.fasta -o /data/genes.gff -f gff -d /data/genes.fna
   ```

---

## Full Help Output

```
Usage:  prodigal [-a trans_file] [-c] [-d nuc_file] [-f output_type]
                 [-g tr_table] [-h] [-i input_file] [-m] [-n] [-o output_file]
                 [-p mode] [-q] [-s start_file] [-t training_file] [-v]

         -a:  Write protein translations to the selected file.
         -c:  Closed ends.  Do not allow genes to run off edges.
         -d:  Write nucleotide sequences of genes to the selected file.
         -f:  Select output format (gbk, gff, or sco).  Default is gbk.
         -g:  Specify a translation table to use (default 11).
         -h:  Print help menu and exit.
         -i:  Specify FASTA/Genbank input file (default reads from stdin).
         -m:  Treat runs of N as masked sequence; don't build genes across them.
         -n:  Bypass Shine-Dalgarno trainer and force a full motif scan.
         -o:  Specify output file (default writes to stdout).
         -p:  Select procedure (single or meta).  Default is single.
         -q:  Run quietly (suppress normal stderr output).
         -s:  Write all potential genes (with scores) to the selected file.
         -t:  Write a training file (if none exists); otherwise, read and use
              the specified training file.
         -v:  Print version number and exit.
```

---

## Command Options Reference

| Option | Description | Default |
|--------|-------------|---------|
| `-i` | Input FASTA/GenBank file | stdin |
| `-o` | Output file | stdout |
| `-a` | Output protein sequences (.faa) | - |
| `-d` | Output gene nucleotide sequences (.fna) | - |
| `-f` | Output format (gbk/gff/sco) | gbk |
| `-p` | Running mode (single/meta) | single |
| `-g` | Translation table number | 11 |
| `-c` | Closed ends, don't allow genes across boundaries | off |
| `-m` | Treat runs of N as masked, don't predict across | off |
| `-q` | Quiet mode | off |

### Running Mode Explanation

| Mode | Use Case | Description |
|------|----------|-------------|
| `single` | Single complete genome | Uses genome-specific training |
| `meta` | Metagenome/short fragments | Uses pre-trained models, suitable for contigs < 100kb |

---

## Important Notes

- **Memory**: Low memory requirements, can process large genomes
- **Runtime**: Fast, typical bacterial genome < 1 minute
- **Input**: FASTA (.fa, .fasta, .fna) or GenBank format
- **Output**: GFF3, GenBank (gbk), or SCO format
- **Mode**: Use `-p meta` for phages/short sequences

---

## Examples for Agent

### Example 1: Phage Gene Prediction
**User Request**: "Predict genes for this phage genome"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/prodigal:2.6.3 \
  prodigal -i /data/phage.fasta -o /data/phage_genes.gff -f gff -a /data/phage_proteins.faa -p meta
```

**Expected Output**:
- `phage_genes.gff` - Gene annotations in GFF3 format
- `phage_proteins.faa` - Predicted protein sequences

### Example 2: Bacterial Genome Gene Prediction
**User Request**: "Predict genes for this bacterial genome, output both protein and nucleotide sequences"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/prodigal:2.6.3 \
  prodigal -i /data/bacteria.fasta \
           -o /data/bacteria_genes.gff -f gff \
           -a /data/bacteria_proteins.faa \
           -d /data/bacteria_genes.fna
```

### Example 3: Metagenome Gene Prediction
**User Request**: "Predict genes from metagenome contigs"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/prodigal:2.6.3 \
  prodigal -i /data/metagenome_contigs.fasta \
           -o /data/metagenome_genes.gff -f gff \
           -a /data/metagenome_proteins.faa \
           -p meta
```

---

## Integration with Other Tools

Prodigal is typically used as the first step in a pipeline, with output used for:

1. **Functional annotation**: Protein sequences → HMMER/eggNOG-mapper
2. **Phage annotation**: Protein sequences → pharokka
3. **Classification**: Protein sequences → DIAMOND/MMseqs2

---

## Troubleshooting

### Common Errors

1. **Error**: `Sequence is too short`  
   **Solution**: Use `-p meta` mode for sequences < 20kb

2. **Error**: `No complete genes found`  
   **Solution**: Check if input is a valid DNA sequence

3. **Error**: `Translation table error`  
   **Solution**: Confirm correct genetic code table (use 11 for bacteria/archaea)
