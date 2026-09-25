# Bio-Tools Documentation Guide

> ⚠️ **STALENESS WARNING — 恢复投入前必读（2026-09-25 对账结果）**
>
> 本目录是 bio_tools **暂缓期间保留下来的参考材料**（模块当前未在本平台启用，但并非永久弃用）。它与配置真源 `tool_box/bio_tools/tools_config.json` 已出现漂移，按现状直接使用会踩坑：
>
> - **15/30** 个工具页写的 Docker image 与 `tools_config.json` 不一致（prodigal / megahit / concoct / das_tool / maxbin2 / metabat2 / minimap2 / nextflow / ngmlr / samtools / sniffles2 / trim_galore / iphop / vibrant / virsorter2）
> - `dorado`、`hmmer` 在配置里存在但**无文档**；`htstream` 有文档但**未配置**
> - 下方 Tool Status 计数与上表正文不一致（正文 25 行 ✅），且曾有一处指向不存在页面的死链
>
> **以 `tools_config.json` 为唯一真源**；重新启用本模块时，请先按它逐页对账镜像/参数，再信任本目录。`core/`、`phage/`、`binning/`、`assembly/`、`annotation/`、`taxonomy/` 各页的**命令示例**仍有参考价值。

## 📚 Overview

This directory contains documentation for all bio-informatics tools available in the GAgent platform. Each tool is documented with:
- Docker usage instructions
- Common use cases
- Full command reference
- Examples tailored for Agent execution

---

## 🗂️ Tool Categories

### Core Tools
General-purpose sequence manipulation and analysis

| Tool | Version | Purpose | Guide | Status |
|------|---------|---------|-------|--------|
| **seqkit** | 2.8.0 | FASTA/Q file manipulation | [Guide](core/seqkit.md) | ✅ Documented |
| **seqtk** | 1.4 | Fast sequence processing | [Guide](core/seqtk.md) | ✅ Documented |
| **samtools** | 1.21 | SAM/BAM file processing | [Guide](core/samtools.md) | ✅ Documented |
| **minimap2** | 2.26 | Long-read alignment | [Guide](core/minimap2.md) | ✅ Documented |
| **bwa** | 0.7.17 | Short-read alignment | [Guide](core/bwa.md) | ✅ Documented |
| **bowtie2** | 2.5.4 | Fast read alignment | [Guide](core/bowtie2.md) | ✅ Documented |
| **blast** | 2.2.31 | Sequence similarity search | [Guide](core/blast.md) | ✅ Documented |
| **mmseqs2** | 14.7 | Ultra-fast sequence search | [Guide](core/mmseqs2.md) | ✅ Documented |
| **trim_galore** | 0.6.10 | Read trimming & QC | [Guide](core/trim_galore.md) | ✅ Documented |
| **nanoplot** | 1.4 | Long-read QC plotting | [Guide](core/nanoplot.md) | ✅ Documented |
| **ngmlr** | 0.2.7 | SV-aware long-read alignment | [Guide](core/ngmlr.md) | ✅ Documented |
| **sniffles2** | 2.2 | Structural variant caller | [Guide](core/sniffles2.md) | ✅ Documented |
| **nextflow** | - | Workflow orchestration | [Guide](core/nextflow.md) | ✅ Documented |
| **snakemake** | - | Workflow orchestration | [Guide](core/snakemake.md) | ✅ Documented |

### Phage Analysis
Bacteriophage prediction, annotation, and analysis

| Tool | Version | Purpose | Guide | Status |
|------|---------|---------|-------|--------|
| **geNomad** | 1.7.6 | Phage/plasmid prediction (ML-based) | [Guide](phage/genomad.md) | ⚠️ High resources |
| **CheckV** | 1.0.1 | Phage quality assessment | [Guide](phage/checkv.md) | ✅ Documented |
| **VirSorter2** | 2.2.4 | Phage prediction (ML-based) | [Guide](phage/virsorter2.md) | ✅ Documented |
| **pharokka** | 1.7.3 | Phage genome annotation | _page not written (not a configured tool)_ | TODO |
| **VIBRANT** | 1.2.1 | Phage prediction (HMM-based) | [Guide](phage/vibrant.md) | TODO |

### Metagenome Binning
Recovery of MAGs from metagenomes

| Tool | Version | Purpose | Guide | Status |
|------|---------|---------|-------|--------|
| **CONCOCT** | 1.1 | Metagenome binning | [Guide](binning/concoct.md) | ✅ Documented |
| **CheckM** | 1.2 | MAG quality assessment | [Guide](binning/checkm.md) | ✅ Documented |
| **DAS Tool** | 1.1 | Binning refinement | [Guide](binning/das_tool.md) | ✅ Documented |

### Assembly
Genome and metagenome assembly

| Tool | Version | Purpose | Guide | Status |
|------|---------|---------|-------|--------|
| **MegaHIT** | 1.2.9 | Fast metagenome assembly | [Guide](assembly/megahit.md) | ✅ Documented |
| **Flye** | 2.9.2 | Long-read assembly | [Guide](assembly/flye.md) | ✅ Documented |

### Annotation
Genome annotation tools

| Tool | Version | Purpose | Guide | Status |
|------|---------|---------|-------|--------|
| **Prodigal** | 2.6.3 | Prokaryotic gene prediction | [Guide](annotation/prodigal.md) | ✅ Documented |
| **Bakta** | 1.8.2 | Prokaryotic genome annotation | [Guide](annotation/bakta.md) | ✅ Documented |

### Taxonomy
Taxonomic classification

| Tool | Version | Purpose | Guide | Status |
|------|---------|---------|-------|--------|
| **FastANI** | 1.34 | Average nucleotide identity | [Guide](taxonomy/fastani.md) | ✅ Documented |
| **GTDB-Tk** | 2.3.0 | Genome taxonomy via GTDB | [Guide](taxonomy/gtdbtk.md) | TODO |


---

## 🚀 Quick Start for Agents

### General Docker Pattern
```bash
docker run --rm \
  -v /data/input:/input \
  -v /data/output:/output \
  [docker_image:tag] \
  [command] [args]
```

### Database Locations
Database paths for tools requiring them:
```bash
CHECKV_DB=/data/databases/bio_tools/checkv/checkv-db-v1.5
GENOMAD_DB=/data/databases/bio_tools/genomad/genomad_db
VIRSORTER2_DB=/data/databases/bio_tools/virsorter2/db
PHAROKKA_DB=/data/databases/bio_tools/pharokka/pharokka_db
IPHOP_DB=/data/databases/bio_tools/iphop/Sept_2021_pub
GTDBTK_DATA_PATH=/data/databases/bio_tools/gtdbtk/release214
```

---

## 📖 Documentation Standards

All tool documentation follows the [TEMPLATE.md](TEMPLATE.md) format:
1. **Metadata**: Version, Docker image, category, databases
2. **Quick Start**: Basic usage and common cases
3. **Full Help**: Complete command reference
4. **Important Notes**: Memory, runtime, I/O formats
5. **Agent Examples**: Specific use cases with commands
6. **Troubleshooting**: Common errors and solutions

---

## 🔧 Contributing

When adding a new tool:
1. Copy `TEMPLATE.md` to the appropriate category folder
2. Fill in all sections based on `docker run [image] --help`
3. Add entry to this README.md
4. Include at least 2 agent-specific examples

---

## 📊 Tool Status

> 下表计数已过期（与上方分类表不一致），以分类表为准，见文件头的 STALENESS WARNING。

| Status | Count | Description |
|--------|-------|-------------|
| ✅ Documented | see category tables above | 31 tool pages |
| 🚧 In Progress | 0 | - |
| 📝 TODO | see `tools_config.json` | `dorado`、`hmmer` 有配置无文档 |
