# bio_tools 生物信息学工具总览

> 基于 `tool_box/bio_tools/tools_config.json` 汇总。共 **32 种**工具，约 **62 个**操作。

---

## 完整工具列表

| 序号 | 工具名 | 类别 | 描述 | 操作 (operations) |
|:---:|:---|:---|:---|:---|
| 1 | **seqkit** | sequence_processing | SeqKit - 跨平台 FASTA/Q 序列处理工具 | stats, grep, seq, head |
| 2 | **blast** | sequence_alignment | BLAST+ - 序列相似性搜索 | blastn, blastp, makeblastdb |
| 3 | **prodigal** | gene_prediction | Prodigal - 原核生物基因预测 | predict, meta |
| 4 | **hmmer** | sequence_analysis | HMMER - 基于 HMM 的序列分析 | hmmscan, hmmsearch, hmmpress, hmmbuild |
| 5 | **dorado** | long_read_processing | Dorado - Oxford Nanopore basecalling 与 demux | basecall, demux |
| 6 | **nanoplot** | long_read_processing | NanoPlot - 长读长测序数据质控可视化 | basic |
| 7 | **minimap2** | sequence_alignment | Minimap2 - 短读长/长读长快速比对 | map, filter |
| 8 | **samtools** | sequence_alignment | SAMtools - 处理 SAM/BAM/CRAM | view_filter_unmapped, view, sort, index, stats |
| 9 | **flye** | assembly | Flye - 长读长组装（支持 meta 宏基因组模式） | meta, ont, hifi |
| 10 | **seqtk** | sequence_processing | Seqtk - FASTA/Q 工具 | sample, size |
| 11 | **megahit** | assembly | MEGAHIT - 短读长宏基因组组装 | assemble |
| 12 | **bakta** | annotation | Bakta - 细菌基因组快速标准化注释 | annotate |
| 13 | **metabat2** | binning | MetaBAT2 - 宏基因组分箱（基于丰度） | bin, depth |
| 14 | **concoct** | binning | CONCOCT - 基于组成与丰度的宏基因组分箱 | bin |
| 15 | **maxbin2** | binning | MaxBin2 - 宏基因组分箱 | bin |
| 16 | **das_tool** | binning | DAS Tool - 整合多个分箱结果 | integrate |
| 17 | **checkm** | bin_assessment | CheckM - 宏基因组 bins 质量评估 | lineage_wf, qa |
| 18 | **gtdbtk** | taxonomy | GTDB-Tk - 基于系统发育的物种分类 | classify_wf |
| 19 | **genomad** | phage | geNomad - 移动遗传元素识别（噬菌体/质粒预测） | end_to_end, annotate, find_proviruses |
| 20 | **checkv** | phage | CheckV - 病毒基因组质量评估 | end_to_end, completeness, complete_genomes |
| 21 | **virsorter2** | phage | VirSorter2 - 多分类器病毒序列识别 | run |
| 22 | **bwa** 🆕 | sequence_alignment | BWA - Burrows-Wheeler Alignment Tool | index, mem |
| 23 | **bowtie2** 🆕 | sequence_alignment | Bowtie 2 - 快速敏感序列比对 | build, align |
| 24 | **mmseqs2** 🆕 | sequence_analysis | MMseqs2 - 超快速蛋白序列搜索 | easy_search, easy_linclust |
| 25 | **trim_galore** 🆕 | sequence_processing | Trim Galore! - 质量和接头修剪 | trim, trim_paired |
| 26 | **ngmlr** 🆕 | sequence_alignment | NGMLR - 结构变异感知比对 | map |
| 27 | **sniffles2** 🆕 | sequence_analysis | Sniffles2 - 结构变异检测 | call |
| 28 | **fastani** 🆕 | taxonomy | FastANI - 快速全基因组ANI计算 | compare |
| 29 | **vibrant** 🆕 | phage | VIBRANT - 病毒识别和分类 | run |
| 30 | **iphop** 🆕 | phage | iPHoP - 噬菌体宿主预测 | predict |
| 31 | **nextflow** 🆕 | workflow | Nextflow - 数据驱动计算流程 | run, clean |
| 32 | **snakemake** 🆕 | workflow | Snakemake - 工作流管理系统 | run, dry_run |

---

## 按类别统计

| 类别 | 工具数 | 工具 |
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

## 数据库配置

以下工具需要预先安装数据库：

| 工具 | 数据库路径 | 大小 | 状态 |
|:---|:---|:---:|:---|
| bakta | `/home/zczhao/GAgent/data/databases/bio_tools/bakta/db` | ~71 GB | ✅ 已安装 |
| checkm | `/home/zczhao/GAgent/data/databases/bio_tools/checkm_data` | ~1.7 GB | ✅ 已安装 |
| checkv | `/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5` | ~6.4 GB | ✅ 已安装 |
| genomad | `/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db` | ~2.7 GB | ✅ 已安装 |
| gtdbtk | `/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data` | ~105 GB | ✅ 已安装 |
| virsorter2 | `/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db` | ~12 GB | ✅ 已安装 |
| iphop 🆕 | `/home/zczhao/GAgent/data/databases/bio_tools/iphop` | ~20 GB | ⚠️ 需确认 |

---

## 已完成的运行任务

用户已使用部分工具处理 Nature 论文实验A的数据：

| 工具 | 已处理样本数 | 结果目录 |
|:---|:---:|:---|
| genomad | 19 | `data/experiment_nature/experiment_A/genomad_results_fixed` |
| virsorter2 | 18 | `data/experiment_nature/experiment_A/virsorter2_results` |

---

## 调用方式

### 列出所有工具
```python
from tool_box import execute_tool

result = await execute_tool('bio_tools', tool_name='list')
```

### 获取工具帮助
```python
result = await execute_tool('bio_tools', 
    tool_name='bwa',
    operation='help'
)
```

### 执行工具
```python
result = await execute_tool('bio_tools',
    tool_name='bwa',
    operation='mem',
    input_file='/path/to/reads.fastq',
    params={'reference': '/path/to/ref.fa', 'output': 'aln.sam', 'threads': '8'}
)
```

---

## 测试

运行完整测试：
```bash
cd /home/zczhao/GAgent
PYTHONPATH=/home/zczhao/GAgent:$PYTHONPATH \
  python tool_box/bio_tools/test_bio_tools_complete.py
```

---

## 更新记录

- **2026-02-15**: 新增 11 个工具 (bwa, bowtie2, mmseqs2, trim_galore, ngmlr, sniffles2, fastani, vibrant, iphop, nextflow, snakemake)，工具总数从 21 增加到 32
