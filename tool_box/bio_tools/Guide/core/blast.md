# BLAST+

## Metadata
- **Version**: 2.2.31+
- **Full Name**: BLAST+ (Basic Local Alignment Search Tool)
- **Docker Image**: `biocontainers/blast:2.2.31`
- **Category**: core
- **Database Required**: Yes (BLAST formatted database)
- **Official Documentation**: https://www.ncbi.nlm.nih.gov/books/NBK279690/
- **Citation**: https://doi.org/10.1186/1471-2105-10-421

---

## Overview

NCBI BLAST+ is a suite of tools for comparing primary biological sequence information, such as the amino-acid sequences of proteins or the nucleotides of DNA sequences.
- **blastp**: Protein-protein BLAST
- **blastn**: Nucleotide-nucleotide BLAST
- **blastx**: Translated nucleotide-protein BLAST
- **tblastn**: Protein-translated nucleotide BLAST
- **tblastx**: Translated nucleotide-translated nucleotide BLAST

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data biocontainers/blast:2.2.31 [tool] [options]
```

### Common Use Cases

1. **Search protein query against protein database (blastp)**
   ```bash
   docker run --rm -v /data:/data biocontainers/blast:2.2.31 \
     blastp -query /data/query.faa -db /data/db/protein_db -out /data/results.txt -evalue 1e-5 -outfmt 6
   ```

2. **Make a BLAST database**
   ```bash
   docker run --rm -v /data:/data biocontainers/blast:2.2.31 \
     makeblastdb -in /data/sequences.fasta -dbtype prot -out /data/db/protein_db
   ```

3. **Search nucleotide query against nucleotide database (blastn)**
   ```bash
   docker run --rm -v /data:/data biocontainers/blast:2.2.31 \
     blastn -query /data/query.fna -db /data/db/nucl_db -out /data/results.txt -outfmt 6
   ```

---

## Full Help Output (blastp)

```
USAGE
  blastp [-h] [-help] [-import_search_strategy filename]
    [-export_search_strategy filename] [-task task_name] [-db database_name]
    [-dbsize num_letters] [-gilist filename] [-seqidlist filename]
    [-negative_gilist filename] [-entrez_query entrez_query]
    [-db_soft_mask filtering_algorithm] [-db_hard_mask filtering_algorithm]
    [-subject subject_input_file] [-subject_loc range] [-query input_file]
    [-out output_file] [-evalue evalue] [-word_size int_value]
    [-gapopen open_penalty] [-gapextend extend_penalty]
    [-qcov_hsp_perc float_value] [-max_hsps int_value]
    [-xdrop_ungap float_value] [-xdrop_gap float_value]
    [-xdrop_gap_final float_value] [-searchsp int_value]
    [-sum_stats bool_value] [-seg SEG_options] [-soft_masking soft_masking]
    [-matrix matrix_name] [-threshold float_value] [-culling_limit int_value]
    [-best_hit_overhang float_value] [-best_hit_score_edge float_value]
    [-window_size int_value] [-lcase_masking] [-query_loc range]
    [-parse_deflines] [-outfmt format] [-show_gis]
    [-num_descriptions int_value] [-num_alignments int_value]
    [-line_length line_length] [-html] [-max_target_seqs num_sequences]
    [-num_threads int_value] [-ungapped] [-remote] [-comp_based_stats compo]
    [-use_sw_tback] [-version]
```

---

## Important Notes

- ⚠️ **Database**: You MUST run `makeblastdb` on your reference sequences before you can search against them.
- ⚠️ **Threads**: Use `-num_threads` to speed up searches.
- ⚠️ **Output Formats**: `-outfmt 6` (Tabular) and `-outfmt 5` (XML) are the most common formats for programmatically parsing results.
- ⚠️ **E-value**: Increasing stringency (smaller evalue, e.g., `1e-10`) reduces false positives.

---

## Examples for Agent

### Example 1: Local Protein Search
**User Request**: "Compare my protein sequences against this local database"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  biocontainers/blast:2.2.31 \
  blastp -query /data/query.faa -db /data/db/target_db -out /data/results.tsv -outfmt 6 -num_threads 4
```

### Example 2: Remote Search
**User Request**: "Search this protein sequence against the remote NCBI nr database"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  biocontainers/blast:2.2.31 \
  blastp -query /data/seq.faa -db nr -remote -out /data/remote_results.txt
```

---

## Troubleshooting

### Common Errors

1. **Error**: `BLAST Database error: No alias or index file found`  
   **Solution**: Ensure you have run `makeblastdb` and the path to the database prefix is correct.

2. **Error**: `Out of memory`  
   **Solution**: BLAST can be memory intensive for very large databases or many queries. Try splitting the query file.
