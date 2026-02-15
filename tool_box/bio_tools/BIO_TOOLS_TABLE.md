# bio_tools 生物信息学工具总览

> 基于 `tool_box/bio_tools/tools_config.json` 汇总。共 **19 种**工具，约 **45 个**操作。

---

## 完整工具列表

| 序号 | 工具名             | 类别                 | 描述                                          | 操作 (operations)                              |
| :--: | ------------------ | -------------------- | --------------------------------------------- | ---------------------------------------------- |
|  1  | **seqkit**   | sequence_processing  | SeqKit - 跨平台 FASTA/Q 序列处理工具          | stats, grep, seq, head                         |
|  2  | **blast**    | sequence_alignment   | BLAST+ - 序列相似性搜索                       | blastn, blastp, makeblastdb                    |
|  3  | **prodigal** | gene_prediction      | Prodigal - 原核生物基因预测                   | predict, meta                                  |
|  4  | **hmmer**    | sequence_analysis    | HMMER - 基于 HMM 的序列分析                   | hmmscan, hmmsearch, hmmpress, hmmbuild         |
|  5  | **dorado**   | long_read_processing | Dorado - Oxford Nanopore basecalling 与 demux | basecall, demux                                |
|  6  | **nanoplot** | long_read_processing | NanoPlot - 长读长测序数据质控可视化           | basic                                          |
|  7  | **minimap2** | sequence_alignment   | Minimap2 - 短读长/长读长快速比对              | map, filter                                    |
|  8  | **samtools** | sequence_alignment   | SAMtools - 处理 SAM/BAM/CRAM                  | view_filter_unmapped, view, sort, index, stats |
|  9  | **flye**     | assembly             | Flye - 长读长组装（支持 meta 宏基因组模式）   | meta, ont, hifi                                |
|  10  | **seqtk**    | sequence_processing  | Seqtk - FASTA/Q 工具                          | sample, size                                   |
|  11  | **megahit**  | assembly             | MEGAHIT - 短读长宏基因组组装                  | assemble                                       |
|  12  | **bakta**    | annotation           | Bakta - 细菌基因组快速标准化注释              | annotate                                       |
|  13  | **metabat2** | binning              | MetaBAT2 - 宏基因组分箱（基于丰度）           | bin, depth                                     |
|  14  | **concoct**  | binning              | CONCOCT - 基于组成与丰度的宏基因组分箱        | bin                                            |
|  15  | **maxbin2**  | binning              | MaxBin2 - 宏基因组分箱                        | bin                                            |
|  16  | **das_tool** | binning              | DAS Tool - 整合多个分箱结果                   | integrate                                      |
|  17  | **checkm**   | bin_assessment       | CheckM - 宏基因组 bins 质量评估               | lineage_wf, qa                                 |
|  18  | **gtdbtk**   | taxonomy             | GTDB-Tk - 基于系统发育的物种分类              | classify_wf                                    |

---

## 按类别统计

| 类别                 | 工具数 | 工具                                 |
| -------------------- | :----: | ------------------------------------ |
| sequence_processing  |   2   | seqkit, seqtk                        |
| sequence_alignment   |   3   | blast, minimap2, samtools            |
| gene_prediction      |   1   | prodigal                             |
| sequence_analysis    |   1   | hmmer                                |
| long_read_processing |   2   | dorado, nanoplot                     |
| assembly             |   2   | flye, megahit                        |
| annotation           |   1   | bakta                                |
| binning              |   4   | metabat2, concoct, maxbin2, das_tool |
| bin_assessment       |   1   | checkm                               |
| taxonomy             |   1   | gtdbtk                               |

---

## 备注

- **数据库依赖**：bakta、checkm、gtdbtk 需预先安装对应数据库；handler 中已预留 checkv 的数据库挂载逻辑，但 checkv 尚未加入 `tools_config.json`。
- **Guide 中有文档但未注册**：geNomad、VirSorter2、CheckV、iPHoP、pharokka 等有 Guide 文档，目前未在 `tools_config.json` 中注册为可调用工具。
- **调用方式**：使用 `operation='help'` 可查看各工具的具体参数与用法。
