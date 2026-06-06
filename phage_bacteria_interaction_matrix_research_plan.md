# 研究方案：基于 PhageScope 注释数据的噬菌体-细菌互作矩阵构建与网络拓扑分析

**Research Protocol: Construction and Topological Analysis of Phage-Bacteria Interaction Matrices Derived from PhageScope Annotations**

---

## 1. 研究背景与创新点

### 1.1 科学背景

噬菌体与细菌的互作是塑造微生物群落结构与功能的核心力量。噬菌体通过裂解优势菌株（"Kill-the-Winner" 动态）维持群落多样性，并驱动细菌进化出多样化的免疫策略（CRISPR-Cas、限制修饰系统、受体突变等）。在宏观生态层面，这些互作呈现出两类典型的网络拓扑模式：

- **嵌套性（Nestedness）**：专家型噬菌体的宿主范围被嵌套在广谱噬菌体的宿主范围之内，形成宿主抗性与噬菌体感染能力的等级结构。
- **模块性（Modularity）**：互作被组织为相对离散的功能/系统发育模块，模块内互作频繁，模块间互作稀疏。

Weitz 等（2013, Science）已证明自然噬菌体-细菌网络呈"嵌套-模块"（nested-modular）架构：模块内嵌套性强、模块间边界清晰。这种双重结构被认为同时提升了群落的共存稳定性（嵌套性）与抗扰动韧性（模块性）。2025 年的多项研究进一步拓展了这一框架：机器学习方法被用于菌株级别互作预测；综述系统总结了细菌免疫策略与噬菌体协调机制；宏分析将噬菌体-细菌互作映射到活性污泥等功能生态系统。

### 1.2 关键问题

现有实验测定的噬菌体-细菌互作数据（培养依赖的裂解实验）规模极小（通常 <10³ 条），无法覆盖自然界数十万噬菌体的宿主多样性。尽管个别高通量交叉感染实验已产生数百至上千条互作记录，但仍不足以支撑宏观生态学级别的拓扑推断。**核心瓶颈在于：缺乏一个规模足够大、系统发育覆盖足够广、注释足够完整的噬菌体-细菌互作矩阵。**

### 1.3 创新点

本研究提出从 **PhageScope** 数据库（873,718 条噬菌体序列）的计算注释中系统构建互作矩阵，实现以下创新：

1. **规模跃升**：从千级实验数据跃升至数十万级注释互作，首次构建属级别的全域噬菌体-细菌互作二分网络。
2. **多源整合**：整合 14 个数据库（GOV2、MGV、IMG_VR、GPD、TEMPHD、CHVD、GVD、IGVD、REFSEQ、STV、PHAGESDB、GENBANK、DDBJ、EMBL），覆盖肠道、海洋、土壤、活性污泥等多生态环境。
3. **拓扑-环境耦合分析**：首次在同一框架下比较不同环境、不同噬菌体谱系、不同生活方式的网络拓扑差异，检验"嵌套-模块"架构的普适性。
4. **ML 辅助链接补全**：利用基于序列特征的宿主预测模型，对稀疏矩阵进行置信度加权的链接预测，量化注释噪声对拓扑推断的影响。

---

## 2. 数据基础

### 2.1 PhageScope 数据集概况

| 指标 | 数值 |
|---|---|
| 噬菌体序列总数 | 873,718 |
| 唯一 phage_id | 873,718（无重复） |
| 具有属级宿主标签 | 495,148（56.7%） |
| 宿主属数量（min ≥ 20 条过滤后） | 616 |
| 元数据文件 | `curated_metadata.tsv`（83.8 MB） |

### 2.2 数据来源分布

| 数据库 | 序列数 | 主要生态环境 |
|---|---|---|
| GOV2 | 195,699 | 海洋 |
| MGV | 189,680 | 人类肠道 |
| IMG_VR | 177,361 | 多环境（土壤、沉积物等） |
| GPD | 142,809 | 人类肠道 |
| TEMPHD | 66,823 | 温带噬菌体参考集 |
| CHVD | 44,935 | 儿童/临床 |
| GVD | 31,402 | 肠道 |
| IGVD | 10,021 | 肠道 |
| REFSEQ | 4,637 | 参考基因组 |
| STV | 4,065 | 土壤 |
| PHAGESDB | 3,754 | 分枝杆菌噬菌体 |
| GENBANK | 2,086 | 通用 |
| DDBJ | 290 | 通用 |
| EMBL | 156 | 通用 |

### 2.3 噬菌体分类学分布

| 分类群 | 序列数 |
|---|---|
| Caudovirales | 583,452 |
| Caudoviricetes | 165,109 |
| Siphoviridae | 22,029 |
| Myoviridae | 9,963 |
| Microviridae | 9,362 |
| Inoviridae | 6,037 |
| Podoviridae | 4,352 |
| Crassvirales | 3,727 |

### 2.4 宿主分布（Top 20 属）

| 宿主属 | 噬菌体数 | 典型生态位 |
|---|---|---|
| Salmonella | 31,511 | 肠道/环境 |
| Bacteroides | 29,910 | 肠道 |
| Lachnospiraceae | 23,584 | 肠道 |
| Faecalibacterium | 22,247 | 肠道 |
| Bacteroidaceae | 17,508 | 肠道 |
| Escherichia | 16,335 | 肠道 |
| Prevotella | 14,834 | 口腔/肠道 |
| Streptococcus | 12,872 | 口腔/上呼吸道 |
| Oscillospirales | 10,880 | 肠道 |
| Bacteroidales | 10,838 | 肠道 |
| Lawsonia | 10,132 | 动物肠道 |
| Bifidobacterium | 9,884 | 肠道 |
| Klebsiella | 9,563 | 肠道/临床 |
| Parabacteroides | 9,140 | 肠道 |
| Ruminococcus | 8,726 | 肠道 |
| Staphylococcus | 8,703 | 皮肤/临床 |
| Pseudomonas | 8,115 | 土壤/水 |
| Oscillospiraceae | 7,408 | 肠道 |
| Clostridia | 6,610 | 肠道/土壤 |
| Alistipes | 6,488 | 肠道 |

### 2.5 训练/验证/测试划分

| 分割 | 数量 |
|---|---|
| Train | 395,972 |
| Validation | 49,517 |
| Test | 49,659 |
| **总计** | **495,148** |

---

## 3. 核心技术路线

### Phase 1：互作矩阵构建

**目标**：从 PhageScope 注释中构建噬菌体-细菌二分网络邻接矩阵。

**步骤**：

1. **读取元数据**：加载 `curated_metadata.tsv`，提取字段 `phage_id`, `host_genus`, `source_database`, `phage_taxonomy`, `lifestyle`（如可用）。
2. **构建原始互作表**：对每个 `phage_id → host_genus` 配对计数，形成噬菌体 × 宿主二分边列表。
3. **聚合矩阵**：
   - **全局矩阵 M<sub>global</sub>**：行 = 噬菌体分类群（family/genus 级），列 = 宿主属。矩阵元素 M<sub>ij</sub> = 分类群 i 中注释到宿主属 j 的噬菌体数量。
   - **环境分层矩阵 M<sub>env</sub>**：按 `source_database` 映射至生态域（gut: MGV+GPD+GVD+IGVD+CHVD; marine: GOV2; soil: IMG_VR+STV; sludge: 特定子集）分别构建。
   - **分类群分层矩阵 M<sub>tax</sub>**：按噬菌体目/科分别构建（Caudovirales 各亚群、Microviridae、Inoviridae 等）。
4. **互作强度量化**：
   - **二值互作**：M<sub>ij</sub> > 0 → 1（存在互作）
   - **加权互作**：以噬菌体计数归一化为概率权重，或引入生活方式权重（裂解性噬菌体权重 > 温和噬菌体权重）
   - **宿主范围宽度**（Host range breadth）：每个噬菌体分类群注释到的不同宿主属数

**输出**：
- `interaction_matrix_global.csv`
- `interaction_matrix_gut.csv`, `interaction_matrix_marine.csv`, ...
- `edge_list_global.tsv`

---

### Phase 2：网络拓扑分析

**目标**：量化互作网络的嵌套性、模块性、连通性等拓扑指标。

**指标体系**：

| 指标 | 定义 | 计算方法 |
|---|---|---|
| **NODF**（Nestedness metric based on Overlap and Decreasing Fill） | 嵌套性标准化度量 [0,1] | `bipartite::nestednodf()` / `bipartite` R 包 |
| **Weighted NODF (wNODF)** | 考虑互作强度的加权嵌套性 | `bipartite::networklevel(type="weighted NODF")` |
| **Newman Modularity Q** | 模块划分质量 [−0.5, 1] | `bipartite::computeModules()` + `LPAwb+` 算法 |
| **Connectance C** | 实际链接数 / 最大可能链接数 | L / (P × H) |
| **Specialization H2'** | 网络级互作特异性 [0,1] | `bipartite::H2fun()` |
| **Degree distribution** | 节点度分布（幂律 vs 指数） | `NetworkX.degree_histogram()` |
| **Betweenness centrality** | 节点中介中心性 | `NetworkX.betweenness_centrality()` |
| **Nested-modular ratio** | NODF/Q 比值，衡量嵌套-模块平衡 | 自定义 |

**分析层级**：

1. **全局网络**：全域互作矩阵的拓扑特征基线
2. **环境子网络**：gut / marine / soil / sludge 各环境的拓扑比较
3. **分类群子网络**：各噬菌体科/目的拓扑比较
4. **生活方式子网络**：裂解性 vs 温和噬菌体的拓扑差异

**输出**：
- `topology_metrics_global.json`
- `topology_comparison_environment.csv`
- `topology_comparison_taxonomy.csv`

---

### Phase 3：统计检验与零模型

**目标**：评估观测到的拓扑指标是否显著偏离随机期望。

**方法**：

1. **零模型生成**：对每个互作矩阵生成 ≥1000 个零模型矩阵
   - **Swap model**（`r2dtable`）：保持行列边际总和不变，随机交换链接
   - **Vaznull model**：保持边际概率结构，允许连接概率异质性
   - **Shuffle model**：完全随机重连（保持填充率）
2. **统计检验**：
   - 计算观测值在零模型分布中的 p 值：`p = (count(null ≥ obs) + 1) / (N + 1)`
   - 计算标准化效应大小（SES）：`SES = (obs − mean(null)) / sd(null)`
   - 多重比较校正：Benjamini-Hochberg FDR 校正
3. **环境间差异检验**：
   - Kruskal-Wallis 检验比较不同环境子网络的拓扑指标
   - 事后 Dunn 检验 + BH 校正

**输出**：
- `null_model_results.json`
- `ses_pvalues_all_matrices.csv`

---

### Phase 4：环境分层与比较分析

**目标**：系统比较不同生态环境下噬菌体-细菌互作网络的拓扑差异。

**环境映射策略**：

| 生态域 | 来源数据库 | 预期特征 |
|---|---|---|
| Human Gut | MGV, GPD, GVD, IGVD, CHVD | 高密度互作、高模块性（功能群特异性） |
| Marine | GOV2 | 大型稀疏网络、高嵌套性（KtW 动态） |
| Soil/Sediment | IMG_VR, STV | 高度多样化、中等嵌套+模块 |
| Activated Sludge | 特定 IMG_VR 子集 | 工程化群落、可能低嵌套性 |
| Clinical | PHAGESDB (Mycobacterium) | 高度特异性互作 |

**分析内容**：
- 网络规模与稀疏度的环境梯度
- 嵌套性 vs 模块性的环境依赖模式
- 枢纽噬菌体（hub phages）和枢纽宿主（hub hosts）的环境分布
- 宿主范围宽度的环境差异（专一性 vs 广谱性）

---

### Phase 5：机器学习辅助链接预测

**目标**：利用宿主预测模型补全互作矩阵，评估链接补全对拓扑推断的影响。

**步骤**：

1. **基线宿主预测模型**：
   - 输入：噬菌体序列特征（k-mer 频率 / DNABERT 嵌入 / 蛋白质域组成）
   - 输出：616 类宿主属的多分类概率
   - 模型：随机森林基线 + 梯度提升（XGBoost） + 神经网络（可选）
   - 评估：固定 test 集（49,659 条）上的 top-1/top-3/top-5 准确率、F1-macro
2. **链接预测与矩阵补全**：
   - 对无宿主注释的噬菌体（873,718 − 495,148 = 378,570 条），预测其宿主概率分布
   - 以置信度阈值（如 P > 0.8）筛选高置信预测，添加为加权边
   - 构建补全矩阵 M<sub>completed</sub>
3. **拓扑鲁棒性分析**：
   - 比较 M<sub>global</sub> vs M<sub>completed</sub> 的拓扑指标
   - 在不同置信度阈值下观察拓扑指标的敏感性曲线
   - 评估：注释噪声是否系统性地偏置了嵌套性/模块性估计

**输出**：
- `host_prediction_model_metrics.json`
- `completed_interaction_matrix.csv`
- `topology_robustness_analysis.csv`

---

## 4. 分析维度

| 维度 | 分层方式 | 分析目的 |
|---|---|---|
| 噬菌体分类学 | 按目/科（Caudovirales 亚群、Microviridae、Inoviridae 等） | 检验不同噬菌体谱系的互作策略差异 |
| 宿主系统发育 | 按宿主门/纲/属聚类；计算系统发育距离矩阵 | 检验互作的系统发育保守性（phylogenetic signal） |
| 生活方式 | Lytic vs Temperate（如 PhageScope 提供 lifestyle 注释） | 检验温和噬菌体是否产生更模块化的网络 |
| 环境来源 | Gut / Marine / Soil / Sludge / Clinical | 检验环境过滤对网络拓扑的塑造作用 |
| 宿主范围 | Specialist（1 属）vs Generalist（≥5 属） | 识别广谱噬菌体及其生态功能 |

---

## 5. 关键指标与预期结果

| 预期发现 | 具体指标 | 生物学含义 |
|---|---|---|
| 全局网络呈显著嵌套-模块架构 | NODF > null（p < 0.001），Q > null（p < 0.001） | 支持协同进化塑造的等级互作结构 |
| 肠道网络模块化最强 | Q<sub>gut</sub> > Q<sub>marine</sub> > Q<sub>soil</sub> | 肠道功能群高度特化 |
| 海洋网络嵌套性最强 | NODF<sub>marine</sub> > NODF<sub>gut</sub> | 海洋 KtW 动态驱动等级结构 |
| 温和噬菌体网络模块化 > 裂解性 | Q<sub>temperate</sub> > Q<sub>lytic</sub> | 温和噬菌体整合策略依赖宿主系统发育 |
| 枢纽宿主集中于肠道优势菌 | betweenness centrality top nodes: Bacteroides, Escherichia | 这些菌是噬菌体多样性的"汇" |
| ML 补全后拓扑稳定 | ΔNODF/NODF < 10%，ΔQ/Q < 10% | 注释噪声未系统偏置拓扑推断 |

---

## 6. 技术栈

| 环节 | 工具 | 用途 |
|---|---|---|
| 数据处理 | Python (pandas, numpy, scipy) | 元数据加载、矩阵构建、统计计算 |
| 网络分析 | NetworkX, python-bipartite | 二分网络拓扑计算 |
| 高级拓扑 | R (bipartite, igraph, vegan) | NODF、computeModules、零模型、H2' |
| 机器学习 | scikit-learn, XGBoost, PyTorch (可选) | 宿主预测模型 |
| 可视化 | matplotlib, plotly, Cytoscape | 网络可视化、热力图、嵌套性矩阵图 |
| 系统发育 | ete3 / Bio.Phylo | 宿主系统发育树构建与信号分析 |
| 工作流 | Snakemake / Nextflow | 可复现分析流水线 |
| 版本管理 | Git, DVC | 数据版本化与代码管理 |

---

## 7. 时间线与里程碑

| 阶段 | 时间 | 里程碑 |
|---|---|---|
| **Phase 0** — 数据审计与准备 | Week 1 | 元数据字段完整性验证；curated_metadata.tsv 质量报告 |
| **Phase 1** — 矩阵构建 | Week 2-3 | 全局 + 环境分层互作矩阵生成；边列表与填充率统计 |
| **Phase 2** — 拓扑分析 | Week 3-5 | 全套拓扑指标计算完成；初步可视化 |
| **Phase 3** — 统计检验 | Week 5-7 | 零模型运行完毕；SES/p-value 表；环境间差异显著性 |
| **Phase 4** — 环境比较 | Week 7-9 | 跨环境拓扑比较表；枢纽物种注释 |
| **Phase 5** — ML 链接预测 | Week 9-12 | 宿主预测模型训练完成；test 指标报告；补全矩阵拓扑鲁棒性分析 |
| **整合与写作** | Week 12-16 | 论文初稿；补充分析；图/表定稿 |
| **投稿** | Week 16-18 | 目标期刊投稿 |

---

## 8. 预期产出

### 8.1 学术产出

- **研究论文** 1-2 篇，目标期刊：*Nature Communications*, *ISME Journal*, *Microbiome*, *mSystems*
- **预印本**：bioRxiv 先行发布

### 8.2 数据产出

- 开源互作矩阵（CSV/TSV 格式），托管于 Zenodo / Figshare
- 宿主预测模型权重文件
- 补全后的全域噬菌体-细菌互作概率矩阵

### 8.3 工具产出

- 可复现分析流程（Snakemake/Nextflow），托管于 GitHub
- 网络拓扑计算脚本（Python + R）
- 网络可视化配置文件（Cytoscape session files）

---

## 9. 风险与应对

| 风险 | 影响 | 应对策略 |
|---|---|---|
| 宿主注释噪声（错误宿主分配） | 虚假链接 → 偏置拓扑指标 | ML 链接预测交叉验证；敏感性分析；与已知实验互作数据（如 PHIST, PHI-base）对比 |
| 矩阵极度稀疏 | 拓扑指标不稳定 | 多阈值分析；最小连通子图筛选；稀疏网络专用零模型 |
| 环境标签不精确（数据库映射模糊） | 环境分层不准确 | 多映射方案比较；使用元数据中的采样信息辅助判断 |
| 温和噬菌体比例未知 | 生活方式分层分析受限 | 使用 PhageScope lifestyle 注释 + 独立工具（如 PhiSpy, VirSorter2）预测 |
| 宿主范围被高估（注释偏差） | 广谱噬菌体被过度识别 | 引入宿主范围置信度权重；与实验数据对比验证 |

---

## 10. 与现有研究的对话

| 参考工作 | 本研究的定位 |
|---|---|
| Weitz et al. 2013 (Science) — 嵌套-模块架构理论 | 用 10²× 规模数据验证该架构的全域普适性 |
| Flores et al. 2011 (PNAS) — 统计结构分析 | 扩展到属级别全域网络 |
| 2025 ML 互作预测研究 | 不仅预测链接，更用预测结果检验拓扑鲁棒性 |
| 2025 细菌-噬菌体军备竞赛综述 | 用网络拓扑数据提供宏观尺度的实证支持 |
| 2025 活性污泥宏分析 | 将活性污泥网络纳入跨环境比较框架 |

---

## 附录 A：数据文件清单

```
phagescope/
├── curated_metadata.tsv          # 83.8 MB, 495,148 行, 616 宿主属
├── label_counts.tsv              # 各宿主属的噬菌体计数
├── metadata_summary.json         # 元数据摘要
└── phagescope_metadata_*.tsv     # 完整元数据（含时间戳）
```

## 附录 B：核心分析代码框架（伪代码）

```python
# Phase 1: Matrix Construction
import pandas as pd
import numpy as np

meta = pd.read_csv("curated_metadata.tsv", sep="\t")
edges = meta.groupby(["phage_family", "host_genus"]).size().reset_index(name="weight")
matrix = edges.pivot(index="phage_family", columns="host_genus", values="weight").fillna(0)

# Phase 2: Topology (via R)
# library(bipartite)
# nodf <- nestednodf(matrix, weighted=FALSE)
# wnods <- nestednodf(matrix, weighted=TRUE)
# modules <- computeModules(matrix, method="LPAwb+")
# h2 <- H2fun(matrix)

# Phase 3: Null Models (via R)
# nulls <- nullmodel(matrix, N=1000, method=1)  # swap
# ses <- (obs - mean(nulls)) / sd(nulls)

# Phase 5: ML Host Prediction
from sklearn.ensemble import GradientBoostingClassifier
# X = sequence_features, y = host_genus_label
# model.fit(X_train, y_train)
# predictions = model.predict_proba(X_unlabeled)
```
