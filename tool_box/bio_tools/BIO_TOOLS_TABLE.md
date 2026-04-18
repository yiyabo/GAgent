# bio_tools 

>  `tool_box/bio_tools/tools_config.json`  **32 **， **62 **

---

## 

|  |  |  |  |  (operations) |
|:---:|:---|:---|:---|:---|
| 1 | **seqkit** | sequence_processing | SeqKit -  FASTA/Q  | stats, grep, seq, head |
| 2 | **blast** | sequence_alignment | BLAST+ -  | blastn, blastp, makeblastdb |
| 3 | **prodigal** | gene_prediction | Prodigal -  | predict, meta |
| 4 | **hmmer** | sequence_analysis | HMMER -  HMM  | hmmscan, hmmsearch, hmmpress, hmmbuild |
| 5 | **dorado** | long_read_processing | Dorado - Oxford Nanopore basecalling  demux | basecall, demux |
| 6 | **nanoplot** | long_read_processing | NanoPlot -  | basic |
| 7 | **minimap2** | sequence_alignment | Minimap2 - / | map, filter |
| 8 | **samtools** | sequence_alignment | SAMtools -  SAM/BAM/CRAM | view_filter_unmapped, view, sort, index, stats |
| 9 | **flye** | assembly | Flye - （ meta ） | meta, ont, hifi |
| 10 | **seqtk** | sequence_processing | Seqtk - FASTA/Q  | sample, size |
| 11 | **megahit** | assembly | MEGAHIT -  | assemble |
| 12 | **bakta** | annotation | Bakta -  | annotate |
| 13 | **metabat2** | binning | MetaBAT2 - （） | bin, depth |
| 14 | **concoct** | binning | CONCOCT -  | bin |
| 15 | **maxbin2** | binning | MaxBin2 -  | bin |
| 16 | **das_tool** | binning | DAS Tool -  | integrate |
| 17 | **checkm** | bin_assessment | CheckM -  bins  | lineage_wf, qa |
| 18 | **gtdbtk** | taxonomy | GTDB-Tk -  | classify_wf |
| 19 | **genomad** | phage | geNomad - （/） | end_to_end, annotate, find_proviruses |
| 20 | **checkv** | phage | CheckV -  | end_to_end, completeness, complete_genomes |
| 21 | **virsorter2** | phage | VirSorter2 -  | run |
| 22 | **bwa** 🆕 | sequence_alignment | BWA - Burrows-Wheeler Alignment Tool | index, mem |
| 23 | **bowtie2** 🆕 | sequence_alignment | Bowtie 2 -  | build, align |
| 24 | **mmseqs2** 🆕 | sequence_analysis | MMseqs2 -  | easy_search, easy_linclust |
| 25 | **trim_galore** 🆕 | sequence_processing | Trim Galore! -  | trim, trim_paired |
| 26 | **ngmlr** 🆕 | sequence_alignment | NGMLR -  | map |
| 27 | **sniffles2** 🆕 | sequence_analysis | Sniffles2 -  | call |
| 28 | **fastani** 🆕 | taxonomy | FastANI - ANI | compare |
| 29 | **vibrant** 🆕 | phage | VIBRANT -  | run |
| 30 | **iphop** 🆕 | phage | iPHoP -  | predict |
| 31 | **nextflow** 🆕 | workflow | Nextflow -  | run, clean |
| 32 | **snakemake** 🆕 | workflow | Snakemake -  | run, dry_run |

---

## 

|  |  |  |
|:---|:---:|:---|
| sequence_processing | 3 | seqkit, seqtk, trim_galore |
| sequence_alignment | 6 | blast, minimap2, samtools, bwa, bowtie2, ngmlr |
| gene_prediction | 1 | prodigal |
| sequence_analysis | 3 | hmmer, mmseqs2, sniffles2 |
| long_read_processing | 2 | dorado, nanoplot |
| assembly | 2 | flye, megahit |
| annotation | 1 | bakta |
| binning | 4 | metabat2, concoct, maxbin2, das_tool |
| bin_assessment | 1 | checkm |
| taxonomy | 2 | gtdbtk, fastani |
| phage | 5 | genomad, checkv, virsorter2, vibrant, iphop |
| workflow | 2 | nextflow, snakemake |

---

## 

：

|  |  |  |  |
|:---|:---|:---:|:---|
| bakta | `/home/zczhao/GAgent/data/databases/bio_tools/bakta/db` | ~71 GB | ✅  |
| checkm | `/home/zczhao/GAgent/data/databases/bio_tools/checkm_data` | ~1.7 GB | ✅  |
| checkv | `/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5` | ~6.4 GB | ✅  |
| genomad | `/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db` | ~2.7 GB | ✅  |
| gtdbtk | `/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data` | ~105 GB | ✅  |
| virsorter2 | `/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db` | ~12 GB | ✅  |
| iphop 🆕 | `/home/zczhao/GAgent/data/databases/bio_tools/iphop` | ~20 GB | ⚠️  |

---

## 

 Nature A：

|  |  |  |
|:---|:---:|:---|
| genomad | 19 | `data/experiment_nature/experiment_A/genomad_results_fixed` |
| virsorter2 | 18 | `data/experiment_nature/experiment_A/virsorter2_results` |

---

## 

### 
```python
from tool_box import execute_tool

result = await execute_tool('bio_tools', tool_name='list')
```

### 
```python
result = await execute_tool('bio_tools', 
    tool_name='bwa',
    operation='help'
)
```

### 
```python
result = await execute_tool('bio_tools',
    tool_name='bwa',
    operation='mem',
    input_file='/path/to/reads.fastq',
    params={'reference': '/path/to/ref.fa', 'output': 'aln.sam', 'threads': '8'}
)
```

---

## 

：
```bash
cd /home/zczhao/GAgent
PYTHONPATH=/home/zczhao/GAgent:$PYTHONPATH \
  python tool_box/bio_tools/run_bio_tools_complete.py
```

---

## 

- **2026-02-15**:  11  (bwa, bowtie2, mmseqs2, trim_galore, ngmlr, sniffles2, fastani, vibrant, iphop, nextflow, snakemake)， 21  32
