
---

## 1) 流程编排与测序数据预处理（QC / 去宿主）

* **Nextflow** (v22.10.5)：论文的 in-house 工作流引擎/管线框架（短读长读流程都围绕它组织）。
* **HTStream SuperDeduper** (v1.3.3)：短读去重复（deduplicate）。
* **TrimGalore** (v0.6.7)：短读低质量碱基/接头修剪。
* **bwa** (v0.7.17)：短读比对到人基因组 hg38，用于 **宿主污染去除** （把比对上的人 reads 丢弃）。
* **Dorado** (v0.5.3；“super-high accuracy” model v3.4.0)：ONT 的 POD5 **basecalling + demultiplex** 生成 FASTQ。
* **NanoPlot** (v1.41.6)：长读（以及下采样后）读长/质量分布评估。
* **minimap2** (v2.26-r1175)：长读比对到人基因组 v38 做 **宿主去除** 。
* **Seqtk subseq** (v1.4-r130)：对 FASTQ 做下采样（让长读深度匹配短读用于公平比较）。

---

## 2) 组装、基因预测、分箱（MAGs）

* **MetaHIT（文中写法；用于短读组装）** (v1.2.9)：短读宏基因组组装。
* **metaFlye** (v2.9.2-b1786)：长读宏基因组组装（使用 nano-hq，只用 Q20+ reads 做初始组装）。
* **myloasm** (v0.1.0)：作为 Flye 的替代组装器，用于“正交证据”验证某些噬菌体跨宿主家族整合现象。
* **Bakta** (v1.8.2)：对组装结果做 **基因预测/注释** （产出 gff 等，后续也被用于可视化）。
* **MetaBAT** (v2.5) / **CONCOCT** (v1.1.0) / **MaxBin** (v2.2.7)：将 contigs **分箱**成 draft genomes。
* **DAS Tool** (v1.1.6)：整合多个 binning 方法的结果（bin consolidation）。
* **CheckM** (v1.2.2)：评估 bin 质量（完整度/污染度）。
* **GTDB-Tk** (v2.3.0；GTDB r214)：对 bins 做 **分类学注释** 。

---

## 3) 噬菌体预测、质量控制与短读/长读对比

* **geNomad** (v1.7.6) / **VIBRANT** (v1.2.1) / **VirSorter2** (v2.2.4) / **Cenote-Taker3** (v3.4.0)：对组装结果进行 **噬菌体区域/前噬菌体预测** （四种工具并用）。
* **CheckV** (v1.0.1) +  **CheckV companion scripts** ：评估噬菌体基因组质量、并基于 host-trimming 调整边界；以及在聚类/比较时计算 identity & coverage。
* **Snakemake** (v5.26.0)：因为 VirSorter2 依赖 Snakemake，所以 VirSorter2 被单独运行。
* **blast+** (v2.2.31)：短读 contigs ↔ 长读组装 的比对；以及 T1/T2 噬菌体互比对用于聚类/对齐覆盖判断。
* **Bowtie 2** (v2.5.4)：短读 reads 比对到长读组装以计算覆盖度。
* **SAMtools** (v1.21)：基于比对结果计算 per-base coverage（覆盖度统计）。

---

## 4) 进化/变异与宿主预测相关分析

* **FastANI** (v1.34)：计算 T1/T2 配对 MAG 的 ANI，用于“strain replacement”分析。
* **NGMLR** (v0.2.7)：跨时间点 reads ↔ 对方时间点组装的比对，用于找结构变异（SV）。
* **SAMtools** (v1.9)：SV 流程里对 BAM 排序。
* **Sniffles2** (v2.2)：基于长读比对结果进行 **结构变异检测** 。
* **iPHoP** (v1.3.3；db iPHoP_db_Aug23_rw)：对噬菌体做 **宿主预测** 。
* **MMseqs2** (v14.7e284；mmseqs taxonomy module，基于 GTDB v214.1)：对整合型噬菌体所在 contig 的基因做 **基因级分类注释** ，进而推断宿主分类。

---

## 5) 基因内容/共线性可视化 + 移动元件相关扫描

* **AliTV** ：对 bins/序列之间的比对做可视化（用于展示某些噬菌体在不同宿主家族中的情况）。
* **pharokka** (v1.7.3；database v1.4.0)：对整合噬菌体做 **基因内容注释** （带 --meta 等参数）。
* **LoVis4u** (v0.1.4.1)：噬菌体基因内容与共线性（synteny）可视化；输入来自 pharokka 的 gff，宿主上下文也会用 Bakta 的 gff 做转换。
* **HMMER** (v3.4；hmmsearch)：用 Pfam HMM profiles 扫描预测蛋白，识别潜在整合酶/重组酶家族。
* **ISEScan** (v1.7.2.3)：在公共病毒基因组与细菌基因组里扫描 IS 元件（文中用来找 IS30 相关的 “IScream phages”）。
* **NCBI Datasets tool** ：批量下载 Clostridia 完整/染色体水平基因组用于后续筛查。

---

## 6) 最后补充一下，之前漏掉了一些

* **Phanta v1.0** ：作者用它去分析两个公开数据集（其中包含 **VLP-enriched** 数据）来做 IScream phage 的物种层面 profile / 丰度与流行度分析。
* **DamageProfiler v1.1** ：用于古粪便（paleofeces）样本里、对候选 IS30/phage contig 做 **ancient DNA damage pattern** 评估并据此过滤。
