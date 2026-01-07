# SeqKit

## Metadata
- **Version**: 2.8.0
- **Docker Image**: `staphb/seqkit:2.8.0`
- **Category**: core
- **Database Required**: No
- **Official Documentation**: http://bioinf.shenwei.me/seqkit
- **Citation**: https://doi.org/10.1371/journal.pone.0163962

---

## Quick Start

### Basic Usage
```bash
docker run --rm -v /path/to/data:/data staphb/seqkit:2.8.0 seqkit [command] [args]
```

### Common Use Cases

1. **Get FASTA/Q file statistics**
   ```bash
   docker run --rm -v /data:/data staphb/seqkit:2.8.0 seqkit stats /data/input.fasta
   ```

2. **Convert FASTQ to FASTA**
   ```bash
   docker run --rm -v /data:/data staphb/seqkit:2.8.0 seqkit fq2fa /data/input.fastq -o /data/output.fasta
   ```

3. **Filter sequences by length**
   ```bash
   docker run --rm -v /data:/data staphb/seqkit:2.8.0 seqkit seq -m 1000 /data/input.fasta -o /data/filtered.fasta
   ```

4. **Extract subsequences by region**
   ```bash
   docker run --rm -v /data:/data staphb/seqkit:2.8.0 seqkit subseq -r 1:500 /data/input.fasta -o /data/subseq.fasta
   ```

5. **Search sequences by ID/pattern**
   ```bash
   docker run --rm -v /data:/data staphb/seqkit:2.8.0 seqkit grep -p "pattern" /data/input.fasta -o /data/matched.fasta
   ```

---

## Command Reference

### Basic Operations
| Command | Description |
|---------|-------------|
| `stats` | Simple statistics of FASTA/Q files |
| `seq` | Transform sequences (filter, reverse complement, etc.) |
| `subseq` | Get subsequences by region/gtf/bed |
| `sliding` | Extract subsequences in sliding windows |
| `translate` | Translate DNA/RNA to protein |

### Format Conversion
| Command | Description |
|---------|-------------|
| `fq2fa` | Convert FASTQ to FASTA |
| `fa2fq` | Retrieve FASTQ from FASTA |
| `fx2tab` | Convert to tabular format |
| `tab2fx` | Convert from tabular format |

### Searching
| Command | Description |
|---------|-------------|
| `grep` | Search by ID/name/sequence (mismatch allowed) |
| `locate` | Locate subsequences/motifs |
| `amplicon` | Extract amplicon via primers |

### Set Operations
| Command | Description |
|---------|-------------|
| `sample` | Sample sequences by number or proportion |
| `head` | Print first N records |
| `rmdup` | Remove duplicated sequences |
| `split` | Split sequences into files |
| `common` | Find common sequences |

### Editing
| Command | Description |
|---------|-------------|
| `replace` | Replace name/sequence by regex |
| `rename` | Rename duplicated IDs |
| `mutate` | Edit sequence (mutation, insertion, deletion) |

---

## Full Help Output

```
SeqKit -- a cross-platform and ultrafast toolkit for FASTA/Q file manipulation

Version: 2.8.0

Author: Wei Shen <shenwei356@gmail.com>

Documents  : http://bioinf.shenwei.me/seqkit
Source code: https://github.com/shenwei356/seqkit
Please cite: https://doi.org/10.1371/journal.pone.0163962

Usage:
  seqkit [command] 

Commands for Basic Operation:
  faidx           create the FASTA index file and extract subsequences
  scat            real time recursive concatenation and streaming of fastx files
  seq             transform sequences (extract ID, filter by length, remove gaps, reverse complement...)
  sliding         extract subsequences in sliding windows
  stats           simple statistics of FASTA/Q files
  subseq          get subsequences by region/gtf/bed, including flanking sequences
  translate       translate DNA/RNA to protein sequence (supporting ambiguous bases)
  watch           monitoring and online histograms of sequence features

Commands for Format Conversion:
  convert         convert FASTQ quality encoding between Sanger, Solexa and Illumina
  fa2fq           retrieve corresponding FASTQ records by a FASTA file
  fq2fa           convert FASTQ to FASTA
  fx2tab          convert FASTA/Q to tabular format (and length, GC content, average quality...)
  tab2fx          convert tabular format to FASTA/Q format

Commands for Searching:
  amplicon        extract amplicon (or specific region around it) via primer(s)
  fish            look for short sequences in larger sequences using local alignment
  grep            search sequences by ID/name/sequence/sequence motifs, mismatch allowed
  locate          locate subsequences/motifs, mismatch allowed

Commands for Set Operation:
  common          find common/shared sequences of multiple files by id/name/sequence
  duplicate       duplicate sequences N times
  head            print first N FASTA/Q records
  head-genome     print sequences of the first genome with common prefixes in name
  pair            match up paired-end reads from two fastq files
  range           print FASTA/Q records in a range (start:end)
  rmdup           remove duplicated sequences by ID/name/sequence
  sample          sample sequences by number or proportion
  split           split sequences into files by id/seq region/size/parts (mainly for FASTA)
  split2          split sequences into files by size/parts (FASTA, PE/SE FASTQ)

Commands for Edit:
  concat          concatenate sequences with the same ID from multiple files
  mutate          edit sequence (point mutation, insertion, deletion)
  rename          rename duplicated IDs
  replace         replace name/sequence by regular expression
  restart         reset start position for circular genome
  sana            sanitize broken single line FASTQ files

Commands for Ordering:
  shuffle         shuffle sequences
  sort            sort sequences by id/name/sequence/length

Flags:
      --alphabet-guess-seq-length int   length of sequence prefix for guessing (default 10000)
      --compress-level int              compression level for gzip, zstd, xz and bzip2 (default -1)
  -h, --help                            help for seqkit
      --id-ncbi                         FASTA head is NCBI-style
      --id-regexp string                regular expression for parsing ID (default "^(\\S+)\\s?")
  -X, --infile-list string              file of input files list (one file per line)
  -w, --line-width int                  line width for FASTA output (default 60)
  -o, --out-file string                 out file ("-" for stdout, suffix .gz for gzipped)
      --quiet                           be quiet and do not show extra information
  -t, --seq-type string                 sequence type (dna|rna|protein|unlimit|auto) (default "auto")
  -j, --threads int                     number of CPUs (default 4)

Use "seqkit [command] --help" for more information about a command.
```

---

## Important Notes

- ⚠️ **Memory**: Low memory usage, suitable for large files
- ⚠️ **Runtime**: Ultra-fast, can process millions of sequences quickly
- ⚠️ **Input**: FASTA (.fa, .fasta, .fna), FASTQ (.fq, .fastq), gzip (.gz), xz, zstd, bzip2
- ⚠️ **Output**: Same formats, auto-compressed if .gz suffix used

---

## Examples for Agent

### Example 1: Get File Statistics
**User Request**: "帮我统计这个 FASTA 文件有多少条序列"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/seqkit:2.8.0 \
  seqkit stats /data/sequences.fasta
```

**Expected Output**:
```
file           format  type  num_seqs  sum_len  min_len  avg_len  max_len
sequences.fasta  FASTA   DNA    1,234    5.6M     100     4,536    12,345
```

### Example 2: Filter Short Sequences
**User Request**: "过滤掉长度小于 1000bp 的序列"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/seqkit:2.8.0 \
  seqkit seq -m 1000 /data/input.fasta -o /data/filtered_1000bp.fasta
```

### Example 3: Extract Sequences by ID
**User Request**: "提取包含 'phage' 关键词的序列"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/seqkit:2.8.0 \
  seqkit grep -p "phage" /data/input.fasta -o /data/phage_sequences.fasta
```

### Example 4: Sample Random Sequences
**User Request**: "从文件中随机抽取 100 条序列"

**Agent Command**:
```bash
docker run --rm \
  -v /data/user_data:/data \
  staphb/seqkit:2.8.0 \
  seqkit sample -n 100 /data/input.fasta -o /data/sampled_100.fasta
```

---

## Troubleshooting

### Common Errors

1. **Error**: `[ERRO] invalid FASTQ format`  
   **Solution**: Check if file is corrupted or use `seqkit sana` to sanitize

2. **Error**: `[ERRO] empty sequence found`  
   **Solution**: Use `seqkit seq -m 1` to remove empty sequences first

3. **Error**: `permission denied`  
   **Solution**: Ensure mounted volume has correct permissions (`chmod 777`)