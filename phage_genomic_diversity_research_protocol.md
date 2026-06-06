# 基于PhageScope数据的噬菌体群体基因组多样性分析研究方案

## 1. 研究背景与目标

### 1.1 研究背景
噬菌体是地球上最丰富的生物实体，其基因组多样性与宿主范围密切相关。PhageScope数据库整合了873,718条高质量噬菌体序列，覆盖14个主要来源数据库（GOV2、MGV、IMG_VR、GPD等），为系统性的群体基因组多样性分析提供了前所未有的数据基础。

### 1.2 研究目标
本研究旨在：
1. 基于严格的质控标准筛选高质量噬菌体基因组
2. 提取并比较不同宿主属群体间的基因组特征差异
3. 量化α多样性和β多样性，揭示群体内部和群体间的多样性模式
4. 构建系统发育关系，追溯进化历史
5. 识别宿主特异性的功能基因特征

---

## 2. 数据基础与质控策略

### 2.1 PhageScope已有数据（直接使用，无需重复生成）

| 数据类型 | PhageScope提供内容 | 文件路径/说明 |
|---------|-------------------|--------------|
| **元数据** | curated_metadata.tsv (873,718行) | 包含phage_id, host_genus, genome_length, gc_content, completeness, source_database等字段 |
| **功能注释** | 7类功能基因目录 | virulent_factor/, trna_tmrna/, crispr_array/, antimicrobial_resistance_gene/, anticrispr_protein/, transmembrane_protein/, transcription_terminator/ |
| **序列数据** | phage_fasta/ | 所有噬菌体的FASTA序列文件 |
| **质量评估** | CheckV质量标签 | High-quality, Medium-quality, Low-quality, Not-determined |

### 2.2 质控流程（需执行）

**工具**: Python (pandas) + CheckV质量过滤

**步骤**:
```python
# 质控标准
- 完整性(Completeness): ≥90%
- 污染率(Contamination): <5%
- CheckV质量等级: High-quality 或 Complete
- 基因组长度: 10kb - 500kb (排除异常值)
```

**预期输出**:
- `filtered_metadata.csv`: 质控后的元数据表
- `qc_report.tsv`: 质控统计报告（排除数量、保留数量、各质量等级分布）

**工具选择理由**: 使用Python pandas进行数据过滤，因为PhageScope已提供CheckV质量评估结果，无需重新运行CheckV。

---

## 3. 分析流程与工具选择

### 3.1 任务一：数据子集化与基因组特征提取

#### 3.1.1 宿主属筛选
**工具**: Python (pandas)
**方法**: 
- 统计质控后每个host_genus的序列数量
- 选择序列数量最多的前15个宿主属
- 记录每个属的序列数、占比

**输出**: `top15_genera_list.tsv`

#### 3.1.2 基因组特征统计
**工具**: Python (pandas, numpy, scipy.stats)
**特征提取**:

| 特征类型 | 计算方法 | 工具/函数 |
|---------|---------|----------|
| 基因组长度分布 | mean, median, std, min, max, IQR | pandas.describe(), scipy.stats |
| GC含量分布 | 均值、标准差、分布直方图 | pandas, matplotlib |
| 完整性分布 | 各质量等级计数 | pandas.value_counts() |
| 来源数据库分布 | 各数据库序列计数 | pandas.groupby() |

#### 3.1.3 功能基因交叉引用
**工具**: Python (pandas) + 文件系统操作
**方法**:
- 遍历7个功能注释目录
- 对每个phage_id统计各类功能基因数量
- 按host_genus聚合统计

**输出**: `functional_gene_counts.csv` (phage_id × 功能基因类别矩阵)

#### 3.1.4 综合特征表生成
**工具**: Python (pandas)
**方法**: 合并基础元数据、基因组特征、功能基因计数
**输出**: `comprehensive_feature_table.csv`

---

### 3.2 任务二：多样性指数计算

#### 3.2.1 稀释标准化（Rarefaction）
**工具**: R (vegan包) 或 Python (scikit-bio)
**方法**:
- 对每个host_genus进行稀释标准化，统一抽样深度
- 选择最小样本量的90%作为稀释深度（避免丢失小群体）

**R代码示例**:
```R
library(vegan)
rarefied_data <- rarefy(species_matrix, sample = min_sample_size)
```

**Python替代**:
```python
from skbio.diversity import alpha_diversity, beta_diversity
from skbio.stats import subsample_counts
```

**选择理由**: vegan是生态学多样性分析的经典R包，提供完整的稀释和多样性计算功能；scikit-bio是其Python等价物。

#### 3.2.2 α多样性计算
**工具**: R (vegan) 或 Python (scikit-bio)
**指标**:

| 指标 | 公式含义 | 应用场景 |
|-----|---------|---------|
| Shannon熵 (H') | -Σ(pi × ln(pi)) | 综合考虑丰富度和均匀度 |
| Simpson指数 (D) | 1 - Σ(pi²) | 强调优势种的影响 |
| Pielou均匀度 (J') | H' / ln(S) | 衡量群落均匀程度 |

**计算维度**:
- 基因组长度分箱多样性
- GC含量分箱多样性
- 功能基因家族多样性
- 来源数据库组成多样性

**输出**: `alpha_diversity_metrics.csv`

#### 3.2.3 β多样性计算
**工具**: R (vegan) 或 Python (scikit-bio)
**距离度量**:
- **Bray-Curtis相异度**: 基于丰度的群落差异
- **Jaccard距离**: 基于存在/缺失的差异

**R代码示例**:
```R
library(vegan)
bray_dist <- vegdist(species_matrix, method = "bray")
jaccard_dist <- vegdist(species_matrix, method = "jaccard")
```

**统计检验**:
- **PERMANOVA** (vegan::adonis2): 检验群体间差异显著性
- **FDR校正**: Benjamini-Hochberg方法，阈值p < 0.05

**输出**: 
- `bray_curtis_matrix.csv`
- `permanova_results.csv`

#### 3.2.4 可视化
**工具**: R (ggplot2, pheatmap) 或 Python (matplotlib, seaborn)

| 图表类型 | 内容 | 工具 |
|---------|-----|------|
| 柱状图 | 各属Shannon熵比较 | ggplot2 / matplotlib |
| 热图 | Bray-Curtis相异度矩阵 | pheatmap / seaborn |
| 箱线图 | 各属基因组长度分布 | ggplot2 / matplotlib |

**输出**: `results/task2/figures/` 目录下的PNG文件

---

### 3.3 任务三：系统发育分析

#### 3.3.1 代表性序列选择
**工具**: Python (pandas, random)
**方法**:
- 从每个top 15宿主属中随机抽取20条序列
- 总计约300条代表性序列
- 记录phage_id和对应host_genus

**输出**: `selected_phage_ids.csv`

#### 3.3.2 FASTA序列提取
**工具**: seqkit (conda安装)
**命令**:
```bash
# 从PhageScope phage_fasta目录提取序列
seqkit grep -f selected_ids.txt phage_fasta/ -o representative_sequences.fasta
```

**选择理由**: seqkit是高性能的FASTA/Q处理工具，支持按ID快速提取序列。

#### 3.3.3 序列统计
**工具**: seqkit
**命令**:
```bash
seqkit stats representative_sequences.fasta -o sequence_stats.tsv
```

**输出**: `sequence_stats.tsv` (序列数、总长度、平均长度、N50等)

#### 3.3.4 多序列比对
**工具**: MAFFT (conda安装)
**命令**:
```bash
mafft --auto representative_sequences.fasta > aligned_sequences.fasta
```

**选择理由**: MAFFT是目前最准确和高效的多序列比对工具之一，支持大规模数据集。

**替代方案**: MUSCLE (适用于较小数据集)

#### 3.3.5 系统发育树构建
**工具**: IQ-TREE 2 (conda安装)
**方法**: 最大似然法 (Maximum Likelihood)

**命令**:
```bash
iqtree2 -s aligned_sequences.fasta \
        -m MFP \
        -bb 1000 \
        -alrt 1000 \
        -nt AUTO \
        -o outgroup.fasta
```

**参数说明**:
- `-m MFP`: 自动选择最优替换模型
- `-bb 1000`: ultrafast bootstrap (1000次重复)
- `-alrt 1000`: SH-aLRT分支支持度检验
- `-nt AUTO`: 自动使用可用CPU核心数

**选择理由**: 
1. IQ-TREE 2是目前最快、最准确的ML树构建工具
2. 支持自动模型选择和多种分支支持度评估
3. 相比Neighbor-Joining，ML方法能更好地处理不同进化速率
4. Ultrafast bootstrap比传统bootstrap快10-100倍

**替代方案**: 
- FastTree (更快但精度略低，适用于超大数据集)
- RAxML-NG (与IQ-TREE性能相当)

**输出**: 
- `phylogenetic_tree.nwk` (Newick格式)
- `iqtree.log` (模型选择和统计信息)

#### 3.3.6 系统发育树可视化
**工具**: R (ggtree包) 或 Python (ete3)

**R代码示例**:
```R
library(ggtree)
library(treeio)

tree <- read.tree("phylogenetic_tree.nwk")
metadata <- read.csv("selected_phage_ids.csv")

p <- ggtree(tree) %<+% metadata +
     geom_tippoint(aes(color = host_genus)) +
     theme_tree2()

ggsave("phylogenetic_tree.png", p, width = 12, height = 10)
```

**选择理由**: ggtree是R生态中最强大的系统发育树可视化工具，支持丰富的注释和美化功能。

**输出**: `phylogenetic_tree.png`

---

### 3.4 任务四：比较基因组学分析

#### 3.4.1 平均核苷酸一致性（ANI）计算
**工具**: FastANI (conda安装)
**方法**: 计算代表性基因组间的成对ANI

**命令**:
```bash
# 计算所有对所有的ANI
fastANI --ql query_list.txt \
        --rl reference_list.txt \
        --output ani_results.txt \
        --threads 16
```

**物种界定阈值**: 
- ANI ≥ 95%: 同一种
- ANI 85-95%: 同一属
- ANI < 85%: 不同属

**选择理由**: 
1. FastANI是目前ANI计算的金标准工具
2. 基于MinHash算法，速度快且准确
3. 广泛应用于原核生物分类学

**输出**: `ani_results.csv` (成对ANI矩阵)

#### 3.4.2 基因共享网络分析（可选，计算资源充足时）
**工具**: vConTACT2 (conda安装)
**方法**: 基于基因共享构建噬菌体分类网络

**命令**:
```bash
vcontact2 --raw-proteins proteins.faa \
          --pcs-mode MCL \
          --vcs-mode ClusterONE \
          --db ProkaryoticViralRefSeq \
          --output-dir vcontact2_results/
```

**选择理由**: vConTACT2是噬菌体分类学的标准工具，能识别基于基因共享的病毒簇(VCs)。

**注意**: 此分析计算量大，仅在资源充足时执行。

#### 3.4.3 功能注释富集分析
**工具**: Python (scipy.stats, statsmodels)
**方法**: 
- 对每个host_genus，计算各功能基因的富集频率
- 使用Fisher精确检验或卡方检验
- Benjamini-Hochberg FDR校正 (p < 0.05)

**Python代码示例**:
```python
from scipy.stats import fisher_exact
from statsmodels.stats.multitest import multipletests

# Fisher精确检验
odds_ratio, p_value = fisher_exact(contingency_table)

# FDR校正
reject, pvals_corrected, _, _ = multipletests(p_values, 
                                               alpha=0.05, 
                                               method='fdr_bh')
```

**输出**: `enrichment_results.csv` (功能基因、宿主属、OR、p值、FDR校正p值)

#### 3.4.4 群体内与群体间比较
**工具**: Python (pandas, scipy.stats)
**方法**:
- 群体内ANI分布 vs 群体间ANI分布
- 功能基因频率的群体差异
- Mann-Whitney U检验或Kruskal-Wallis检验

**输出**: `comparative_metrics.csv`

#### 3.4.5 可视化
**工具**: R (ggplot2, ggpubr) 或 Python (matplotlib, seaborn)

| 图表类型 | 内容 | 工具 |
|---------|-----|------|
| 堆叠柱状图 | 各属功能注释频率 | ggplot2 / matplotlib |
| PCA双标图 | 功能特征的群体分布 | ggplot2 / matplotlib |
| 热图 | 属特异性功能特征富集 | pheatmap / seaborn |
| ANI分布图 | 群体内vs群体间ANI比较 | ggplot2 / seaborn |

**输出**: `results/task4/figures/` 目录下的PNG文件

---

### 3.5 任务五：综合研究报告生成

#### 3.5.1 报告结构
**工具**: Markdown + Pandoc (可选转换为PDF)

**章节安排**:
1. **摘要** (Abstract)
2. **引言** (Introduction)
   - 噬菌体多样性研究背景
   - PhageScope数据库优势
   - 研究目标与意义
3. **材料与方法** (Methods)
   - 数据来源与质控标准
   - 多样性指数计算方法
   - 系统发育分析流程
   - 比较基因组学方法
   - 统计检验与FDR校正
4. **结果** (Results)
   - 质控统计与数据子集描述
   - 基因组特征分布
   - α多样性和β多样性模式
   - 系统发育关系
   - 功能基因富集特征
5. **讨论** (Discussion)
   - 宿主-噬菌体共进化关系
   - 功能基因的生态意义
   - 方法学局限性与改进方向
6. **结论** (Conclusions)
7. **参考文献** (References)

#### 3.5.2 方法学论证要求
在Methods部分需明确说明：
- 为何选择IQ-TREE而非Neighbor-Joining（进化模型灵活性）
- 为何使用FastANI而非基于比对的方法（计算效率与准确性平衡）
- 稀释深度的选择依据（避免小群体丢失）
- FDR校正的必要性（多重检验问题）
- PERMANOVA相比ANOVA的优势（非参数、适用于距离矩阵）

#### 3.5.3 图表整合
- 使用Markdown图片语法嵌入所有生成的图表
- 每个图表配有详细的图注说明

**输出**: `phage_genomic_diversity_report.md`

---

## 4. 工具清单与安装

### 4.1 Conda环境

```bash
# 创建conda环境
conda create -n phage_diversity python=3.9 r-base=4.2

# 激活环境
conda activate phage_diversity

# 安装Python包
conda install -c conda-forge pandas numpy scipy scikit-bio matplotlib seaborn statsmodels

# 安装R包
conda install -c conda-forge r-ggplot2 r-pheatmap r-ggtree r-treeio r-vegan

# 安装生物信息学工具
conda install -c bioconda seqkit mafft iqtree fastani vcontact2

# 可选：安装Pandoc用于PDF转换
conda install -c conda-forge pandoc
```

### 4.2 工具版本要求

| 工具 | 推荐版本 | 用途 |
|-----|---------|------|
| Python | ≥3.9 | 数据处理与分析 |
| R | ≥4.2 | 统计分析与可视化 |
| pandas | ≥1.5 | 数据操作 |
| scikit-bio | ≥0.5.8 | 多样性计算 |
| vegan | ≥2.6 | 生态学分析 |
| seqkit | ≥2.5 | FASTA处理 |
| MAFFT | ≥7.5 | 多序列比对 |
| IQ-TREE 2 | ≥2.2 | 系统发育树构建 |
| FastANI | ≥1.3 | ANI计算 |
| vConTACT2 | ≥2.0 | 基因共享网络（可选） |

---

## 5. 计算资源估算

### 5.1 存储需求
- PhageScope数据: ~100 GB
- 中间文件: ~20 GB
- 最终输出: ~5 GB
- **总计**: 约125 GB

### 5.2 计算时间估算

| 任务 | 预估时间 | 主要瓶颈 |
|-----|---------|---------|
| 质控与特征提取 | 2-4小时 | I/O密集 |
| 多样性计算 | 1-2小时 | 稀释计算 |
| 序列提取与比对 | 4-8小时 | MAFFT比对 |
| 系统发育树构建 | 2-6小时 | IQ-TREE计算 |
| ANI计算 | 6-12小时 | 成对比较 |
| 功能富集分析 | 1-2小时 | 统计检验 |
| 报告生成 | 1小时 | 文本撰写 |
| **总计** | **17-35小时** | - |

### 5.3 硬件建议
- CPU: ≥16核心
- 内存: ≥64 GB
- 存储: ≥200 GB可用空间
- 并行化: IQ-TREE和FastANI支持多线程

---

## 6. 预期输出清单

### 6.1 数据文件

| 文件名 | 内容描述 | 格式 |
|-------|---------|------|
| filtered_metadata.csv | 质控后的元数据 | CSV |
| qc_report.tsv | 质控统计报告 | TSV |
| top15_genera_list.tsv | 前15宿主属列表 | TSV |
| functional_gene_counts.csv | 功能基因计数矩阵 | CSV |
| comprehensive_feature_table.csv | 综合特征表 | CSV |
| alpha_diversity_metrics.csv | α多样性指标 | CSV |
| bray_curtis_matrix.csv | β多样性距离矩阵 | CSV |
| permanova_results.csv | PERMANOVA检验结果 | CSV |
| selected_phage_ids.csv | 代表性序列ID列表 | CSV |
| sequence_stats.tsv | 序列统计信息 | TSV |
| phylogenetic_tree.nwk | 系统发育树 | Newick |
| ani_results.csv | ANI成对比较结果 | CSV |
| enrichment_results.csv | 功能富集分析结果 | CSV |
| comparative_metrics.csv | 比较基因组学指标 | CSV |

### 6.2 可视化图表

| 文件名 | 图表类型 | 内容 |
|-------|---------|------|
| shannon_entropy_bar.png | 柱状图 | 各属Shannon熵比较 |
| bray_curtis_heatmap.png | 热图 | β多样性相异度矩阵 |
| genome_length_boxplot.png | 箱线图 | 基因组长度分布 |
| phylogenetic_tree.png | 树形图 | 系统发育关系 |
| functional_stacked_bar.png | 堆叠柱状图 | 功能注释频率 |
| pca_biplot.png | PCA图 | 功能特征分布 |
| enrichment_heatmap.png | 热图 | 功能富集模式 |
| ani_distribution.png | 分布图 | ANI比较分布 |

### 6.3 最终报告
- `phage_genomic_diversity_report.md`: Markdown格式完整研究报告
- (可选) `phage_genomic_diversity_report.pdf`: PDF格式报告

---

## 7. 质量保障与可重复性

### 7.1 随机种子设置
所有随机操作（序列抽样、稀释等）使用固定随机种子：
```python
import random
random.seed(42)
numpy.random.seed(42)
```

### 7.2 版本控制
- 记录所有工具的版本号
- 保存完整的conda环境配置：
```bash
conda env export > environment.yml
```

### 7.3 中间文件保留
保留所有中间分析文件，确保可追溯性。

### 7.4 统计检验标准
- 显著性阈值: p < 0.05
- FDR校正: Benjamini-Hochberg方法
- 效应量报告: 所有显著性检验同时报告效应量

---

## 8. 潜在风险与应对策略

### 8.1 数据不平衡
**风险**: 不同宿主属的序列数量差异巨大
**应对**: 
- 使用稀释标准化
- 报告每个属的实际样本量
- 对小群体结果进行谨慎解读

### 8.2 计算资源不足
**风险**: ANI计算和系统发育分析耗时过长
**应对**:
- 减少代表性序列数量（每属10条而非20条）
- 使用FastTree替代IQ-TREE（更快但精度略低）
- 跳过vConTACT2分析

### 8.3 序列比对质量
**风险**: 噬菌体基因组高度多样化，全局比对困难
**应对**:
- 使用MAFFT的L-INS-i模式（局部比对）
- 考虑使用核心基因而非全基因组比对
- 评估比对质量并修剪低质量区域

---

## 9. 时间规划

| 阶段 | 任务 | 预估时间 |
|-----|------|---------|
| 第1周 | 环境搭建、质控、特征提取 | 3天 |
| 第2周 | 多样性计算、可视化 | 3天 |
| 第3周 | 序列选择、比对、系统发育 | 5天 |
| 第4周 | ANI计算、功能富集分析 | 5天 |
| 第5周 | 报告撰写、图表整合 | 3天 |
| 第6周 | 审阅、修订、最终提交 | 3天 |
| **总计** | - | **约6周** |

---

## 10. 总结

本研究方案提供了一套完整的噬菌体群体基因组多样性分析流程，从数据质控到最终报告生成，涵盖5个主要分析任务。方案明确区分了PhageScope已有数据（直接使用）和需要重新计算的分析步骤，并详细说明了每个步骤使用的工具和选择理由。

**核心优势**:
1. 充分利用PhageScope的高质量注释数据
2. 采用经典的生物信息学工具，确保方法的可靠性和可重复性
3. 严格的统计检验和FDR校正
4. 完整的可视化方案
5. 详细的方法学论证

**预期成果**:
- 一套完整的多样性分析数据集
- 8-10张高质量可视化图表
- 一份详尽的研究报告
- 可重复的分析流程文档
