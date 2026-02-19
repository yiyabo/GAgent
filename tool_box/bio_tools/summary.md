# Bio Tools Docker Images Summary

>  NC_TOOLS.MD  Docker 

---

## 1) （QC / ）

|  |  | Docker |  |
|------|------|-----------|------|
| Nextflow | v22.10.5 | `nextflow/nextflow:22.10.5` | ✅ |
| HTStream SuperDeduper | v1.3.3 | `quay.io/biocontainers/htstream:1.3.3--hf5e1c6e_5` | ✅ |
| TrimGalore | v0.6.7 | `quay.io/biocontainers/trim-galore:0.6.7--hdfd78af_0` | ✅ |
| bwa | v0.7.17 | `staphb/bwa:0.7.17` / `staphb/bwa:latest` | ✅ |
| Dorado | v0.5.3 | `genomicpariscentre/dorado:0.5.3` | ✅ |
| NanoPlot | v1.41.6 | `staphb/nanoplot:latest` | ✅ |
| minimap2 | v2.26-r1175 | `staphb/minimap2:2.26` / `staphb/minimap2:latest` | ✅ |
| Seqtk subseq | v1.4-r130 | `staphb/seqtk:1.4` / `staphb/seqtk:latest` | ✅ |

---

## 2) （MAGs）

|  |  | Docker |  |
|------|------|-----------|------|
| MEGAHIT | v1.2.9 | `quay.io/biocontainers/megahit:1.2.9--haf24da9_8` | ✅ |
| metaFlye | v2.9.2-b1786 | `staphb/flye:2.9.2` / `staphb/flye:latest` | ✅ |
| myloasm | v0.1.0 | ❌ Docker ( `pip install myloasm`) | ⏳ pip |
| Bakta | v1.8.2 | `staphb/bakta:latest` | ✅ |
| MetaBAT | v2.5 / v2.15 | `quay.io/biocontainers/metabat2:2.15--h986a166_1` | ✅ |
| CONCOCT | v1.1.0 | `quay.io/biocontainers/concoct:1.1.0--py312h71dcd68_8` | ✅ |
| MaxBin | v2.2.7 | `quay.io/biocontainers/maxbin2:2.2.7--he1b5a44_2` | ✅ |
| DAS Tool | v1.1.6 | `quay.io/biocontainers/das_tool:1.1.6--r42hdfd78af_0` | ✅ |
| CheckM | v1.2.2 | `quay.io/biocontainers/checkm-genome:1.2.2--pyhdfd78af_1` | ✅ |
| GTDB-Tk | v2.3.0 / v2.3.2 | `quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0` | ✅ |

---

## 3) /

|  |  | Docker |  |
|------|------|-----------|------|
| geNomad | v1.7.6 | `quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0` | ✅ |
| VIBRANT | v1.2.1 | `quay.io/biocontainers/vibrant:1.2.1--hdfd78af_4` | ✅ |
| VirSorter2 | v2.2.4 | `quay.io/biocontainers/virsorter:2.2.4--pyhdfd78af_2` | ✅ |
| Cenote-Taker3 | v3.4.0 | `quay.io/biocontainers/cenote-taker3:3.4.0--pyhdfd78af_0` | ✅ |
| CheckV | v1.0.1 | `quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0` | ✅ |
| Snakemake | v5.26.0 | `quay.io/biocontainers/snakemake:5.26.0--0` / `snakemake/snakemake:latest` | ✅ |
| blast+ | v2.2.31 | `biocontainers/blast:2.2.31` | ✅ |
| Bowtie 2 | v2.5.4 | `staphb/bowtie2:2.5.4` / `staphb/bowtie2:latest` | ✅ |
| SAMtools | v1.21 / v1.9 | `staphb/samtools:1.21` / `staphb/samtools:latest` | ✅ |

---

## 4) /

|  |  | Docker |  |
|------|------|-----------|------|
| FastANI | v1.34 | `staphb/fastani:latest` | ✅ |
| NGMLR | v0.2.7 | `quay.io/biocontainers/ngmlr:0.2.7--h077b44d_10` | ✅ |
| Sniffles2 | v2.2 | `quay.io/biocontainers/sniffles:2.2--pyhdfd78af_0` | ✅ |
| iPHoP | v1.3.3 | `quay.io/biocontainers/iphop:1.3.3--pyhdfd78af_0` | ✅ |
| MMseqs2 | v14.7e284 | `staphb/mmseqs2:latest` / `quay.io/biocontainers/mmseqs2:14.7e284--pl5321hf1761c0_1` | ✅ |

---

## 5) / + 

|  |  |  |  |
|------|------|-----------|------|
| AliTV | - | Web / `pip install alitv-python` | ⏳ pip |
| pharokka | v1.6.1 | `bjhall/pharokka:1.6.1` | ✅ |
| LoVis4u | v0.1.4.1 | `quay.io/biocontainers/lovis4u:0.1.4.1--pyh7e72e81_0` | ✅ |
| HMMER | v3.4 | `staphb/hmmer:latest` | ✅  |
| ISEScan | v1.7.2.3 | ❌ Docker (`pip install isescan`) | ⏳ pip |
| NCBI Datasets tool | - | Docker (`ncbi/edirect`  `pip install ncbi-datasets-cli`) | ⏳ pip |

---

## 6) 

|  |  |  |  |
|------|------|-----------|------|
| Phanta | v1.0 | ❌ Docker (`pip install phanta`) | ⏳ pip |
| DamageProfiler | v1.1 | ❌ Docker () | ⏳  |
| Prodigal | - | `docker.byoryn.cn/biocontainers/prodigal:v1-2.6.3-4-deb_cv1` | ✅ |
| SeqKit | - | `staphb/seqkit:2.8.0` / `quay.io/biocontainers/seqkit:2.8.1--h9ee0642_0` | ✅ |

---

## 

|  | Docker | pip/ |  |  |
|------|------------|--------------|--------|------|
| QC | 8 | 0 | 0 | 8 |
| //MAGs | 9 | 1 (myloasm) | 0 | 10 |
|  | 8 | 0 | 0 | 8 |
| / | 5 | 0 | 0 | 5 |
| / | 3 | 2 (AliTV, ISEScan) | 1 (NCBI Datasets*) | 6 |
|  | 2 | 1 (Phanta) | 1 (DamageProfiler) | 4 |
| **** | **35** | **4** | **2** | **41** |

> *NCBI Datasets  `ncbi/edirect` Docker `pip install ncbi-datasets-cli` 

---

## 

|  |  |
|------|------|
| ✅ | Docker |
| ⏳ pip |  pip  |
| ⏳  |  |
| ⏳  | （Docker） |

---

## 

```bash
# pip 
pip install myloasm                    # myloasm
pip install alitv-python               # AliTV
pip install isescan                    # ISEScan
pip install ncbi-datasets-cli          # NCBI Datasets CLI
pip install phanta                     # Phanta

# Docker 
docker pull staphb/hmmer               # HMMER ()
docker pull ncbi/edirect               # NCBI EDirect (datasets)
```
