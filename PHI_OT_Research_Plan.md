# PHI-OT：基于最优传输理论的噬菌体-宿主互作预测框架

## 完整研究计划与实验方案

---

## 目录

1. [研究背景与目标](#第一章研究背景与目标)
2. [PhageScope 数据使用定义](#第二章phagescope-数据使用定义)
3. [算法理论与模型架构](#第三章算法理论与模型架构)
4. [金标准数据集构建](#第四章金标准数据集构建)
5. [实验设计](#第五章实验设计)
6. [预期成果与时间线](#第六章预期成果与时间线)
7. [附录](#附录)

---

## 第一章：研究背景与目标

### 1.1 核心科学问题

噬菌体-宿主互作（Phage-Host Interaction, PHI）预测是噬菌体疗法、微生物组工程和抗药性细菌控制的基础。当前面临三大瓶颈：

1. **宿主范围预测精度不足**：现有工具在属级预测上准确率约85-92%，但菌株级预测骤降至60-78%（Gaborieau et al., 2024, Nature Microbiology）。
2. **进化偏差导致泛化性差**：训练集和测试集存在系统发育泄漏，模型在真正"新"噬菌体上表现显著下降。
3. **缺乏可解释性**：黑盒模型无法揭示互作的分子机制，限制了生物学洞见的产生。

### 1.2 2020-2025年领域进展回顾

| 年份 | 工具/方法 | 期刊 | 核心技术 | 主要贡献 |
|------|-----------|------|----------|----------|
| 2021 | DeepHost | Briefings in Bioinformatics | k-mer + DNN | 首个深度学习PHI预测工具 |
| 2023 | iPHoP (Roux et al.) | Nature Biotechnology | 集成学习（5类信号） | 属级预测标准工具 |
| 2023 | Zhong et al. | Nature Biotechnology | ESM + 多实例学习(MIL) | 首次用PLM编码噬菌体蛋白 |
| 2023 | Borin et al. | Science | 共进化实验 | 证明宿主范围是动态演化的 |
| 2024 | Gaborieau et al. | Nature Microbiology | 菌株级预测框架 | 78-94%菌株级准确率 |
| 2024 | Pyenson et al. | Science | 表型异质性 | 多种噬菌体在单一克隆宿主上共存 |
| 2025 | PHIStruct | Bioinformatics | ESMFold结构嵌入+SVM | 结构信息优于纯序列 |
| 2025 | GE-PHI | Briefings in Bioinformatics | 图嵌入 + GNN | 蛋白互作网络拓扑特征 |
| 2025 | MoEPH | BMC Biology | 混合专家+PLM | 大语言模型进入PHI领域 |
| 2025 | PhageCGRNet | Computers in Biology and Medicine | 混沌博弈表示+CNN | 可视化基因组特征 |

### 1.3 现有方法的三大局限

**局限1：向量空间假设**
- 现有方法将噬菌体和宿主编码为固定长度向量，然后用余弦相似度或分类器比较
- 问题：噬菌体是50-200个蛋白的**集合**，宿主是数千个蛋白的**集合**，固定向量丢失了集合内部的分布信息

**局限2：进化偏差**
- 训练/测试随机划分导致系统发育相关的噬菌体同时出现在训练集和测试集
- iPHoP的交叉验证未控制进化距离，可能高估泛化能力

**局限3：静态预测**
- Borin et al. (2023, Science) 证明宿主范围是动态共进化的
- 现有工具输出静态0/1标签，无法捕捉互作的概率性和动态性

### 1.4 本研究的创新贡献

1. **首次将最优传输（OT）理论引入PHI预测**：将噬菌体和宿主建模为蛋白嵌入的概率分布，用Wasserstein距离衡量互作可能性
2. **Fused Gromov-Wasserstein (FGW) 距离**：同时考虑蛋白功能相似性和系统发育结构，实现多模态对齐
3. **共进化感知评估体系**：系统发育感知的交叉验证策略，消除进化偏差
4. **概率预测与不确定性量化**：输出传输代价和概率配对矩阵，而非硬标签

---

## 第二章：PhageScope 数据使用定义

### 2.1 数据源概览

PhageScope 包含 873,718 条噬菌体序列，覆盖 14 个数据源，提供 13 个数据模块：

| 模块名称 | 数据源覆盖 | 最大数据量 | 内容描述 | PHI相关性 |
|----------|------------|------------|----------|-----------|
| `phage_meta_data` | 14源 | ~5GB | 噬菌体元数据总表 | 核心 |
| `phage_fasta` | 14源 | ~1.8GB | 噬菌体基因组DNA序列 | 基础 |
| `annotated_protein` | 14源 | ~3.7GB | 蛋白注释（19个结构化字段） | 核心 |
| `protein_fasta` | 14源 | ~1.8GB | 蛋白氨基酸序列 | 核心 |
| `anticrispr_protein` | 13源 | ~200MB | 抗CRISPR蛋白 | 直接互作信号 |
| `transmembrane_protein` | 14源 | ~300MB | 跨膜蛋白 | 受体识别相关 |
| `crispr_array` | 14源 | ~500MB | CRISPR间隔序列 | 宿主防御历史 |
| `transcription_terminator` | 14源 | ~100MB | 转录终止子 | 辅助 |
| `trna_tmrna` | 14源 | ~50MB | tRNA/tmRNA | 辅助 |
| `antimicrobial_resistance_gene` | 14源 | ~150MB | 抗药性基因 | 功能相关 |
| `virulent_factor` | 14源 | ~100MB | 毒力因子 | 功能相关 |
| `gff3` | 14源 | ~2GB | 基因组注释文件 | 辅助 |
| `curated_metadata` | - | ~1GB | 策划后的元数据表 | 入口 |

### 2.2 数据筛选标准

#### 2.2.1 完整性阈值

```python
# 仅保留高质量和中等质量噬菌体
completeness_filter = curated_metadata['Completeness'].isin([
    'High-quality',    # 完整性 > 90%
    'Medium-quality'   # 完整性 50-90%
])
# 排除 Low-quality（完整性 < 50%），因为蛋白注释不完整会导致特征偏差
```

#### 2.2.2 宿主标注过滤

```python
# Host字段非空
host_filter = curated_metadata['Host'].notna()

# Host归一化到Genus级别
curated_metadata['Host_genus'] = curated_metadata['Host'].apply(
    lambda x: x.split()[0] if pd.notna(x) else None
)

# 排除Host标注为Unclassified或未鉴定的
host_genus_filter = curated_metadata['Host_genus'].notna() & \
                    (curated_metadata['Host_genus'] != 'Unclassified')
```

#### 2.2.3 分类学范围

```python
# 噬菌体分类学范围：仅保留已知Family的噬菌体
phage_taxonomy_filter = curated_metadata['Family'].notna() & \
                        (curated_metadata['Family'] != 'Unclassified')

# 预期覆盖的主要噬菌体科
expected_families = [
    'Myoviridae', 'Siphoviridae', 'Podoviridae',
    'Autographiviridae', 'Drexlerviridae', 'Straboviridae',
    'Inoviridae', 'Microviridae',
]

# 宿主分类学范围：仅保留已知Genus的宿主
expected_host_genera = [
    'Escherichia', 'Pseudomonas', 'Staphylococcus', 'Klebsiella',
    'Salmonella', 'Acinetobacter', 'Enterococcus', 'Streptococcus',
    'Mycobacterium', 'Bacillus', 'Clostridium', 'Vibrio',
]
```

#### 2.2.4 序列长度过滤

```python
# 噬菌体基因组长度过滤（排除异常值）
length_filter = (curated_metadata['Length'] >= 5000) & \
                (curated_metadata['Length'] <= 500000)

# 蛋白数量过滤
protein_count_filter = (curated_metadata['Protein_count'] >= 5) & \
                       (curated_metadata['Protein_count'] <= 1000)
```

#### 2.2.5 最终数据集规模估算

| 筛选步骤 | 保留数量（估算） | 保留比例 |
|----------|-----------------|----------|
| 原始PhageScope | 873,718 | 100% |
| 完整性 >= Medium-quality | ~524,230 | ~60% |
| Host标注非空 | ~157,269 | ~18% |
| Host归一化到Genus | ~131,057 | ~15% |
| 噬菌体Family已知 | ~104,846 | ~12% |
| 基因组长度5kb-500kb | ~99,603 | ~11.4% |
| 蛋白数量5-1000 | ~94,360 | ~10.8% |
| **最终数据集** | **~94,000** | **~10.8%** |

> 以上为基于PhageScope数据结构的合理估算。实际数值需在数据加载后确认。预计最终正样本对约为 40,000-60,000 对。

### 2.3 三层特征工程体系

#### 2.3.1 第一层：宏观基因组层（Genome-level）

从 `curated_metadata.tsv` 提取，每个噬菌体一条记录：

| 特征名称 | 数据类型 | 维度 | 提取方法 | 生物学意义 |
|----------|----------|------|----------|------------|
| `genome_length` | 连续数值 | 1 | 直接读取 Length 字段 | 基因组大小与宿主范围相关 |
| `gc_content` | 连续数值 | 1 | 直接读取 GC_content 字段 | GC偏好反映宿主适应性 |
| `protein_count` | 离散数值 | 1 | 直接读取或计算 | 编码能力 |
| `completeness` | 类别(编码) | 2 | One-hot: High/Medium | 数据质量权重 |
| `lifestyle` | 类别(编码) | 2 | One-hot: virulent/temperate | 裂解/溶源影响互作模式 |
| `taxonomy_family` | 类别(编码) | N | Target encoding/Embedding | 噬菌体科级分类 |
| `taxonomy_genus` | 类别(编码) | M | Target encoding/Embedding | 噬菌体属级分类 |
| `cluster` | 类别(编码) | K | Target encoding | PhageScope聚类归属 |
| `subcluster` | 类别(编码) | L | Target encoding | PhageScope亚聚类归属 |
| `gc_skew` | 连续数值 | 1 | 从序列计算 (G-C)/(G+C) | 复制链偏好 |

**总维度**：约 50-100维（取决于分类学类别数）

#### 2.3.2 第二层：蛋白功能层（Protein-level）

从 `annotated_protein` 提取，每个噬菌体汇总其所有蛋白的特征：

**A. 功能分类计数特征**

| 特征名称 | 数据类型 | 描述 |
|----------|----------|------|
| `func_infection_count` | 离散 | 感染相关蛋白数量 |
| `func_lysis_count` | 离散 | 裂解相关蛋白数量 |
| `func_assembly_count` | 离散 | 组装相关蛋白数量 |
| `func_regulation_count` | 离散 | 调控相关蛋白数量 |
| `func_structural_count` | 离散 | 结构蛋白数量 |
| `func_replication_count` | 离散 | 复制相关蛋白数量 |
| `func_modification_count` | 离散 | DNA修饰相关蛋白数量 |
| `func_other_count` | 离散 | 其他功能蛋白数量 |
| `func_hypothetical_ratio` | 连续 | 假设蛋白占比 |

**B. 理化性质统计特征**

对每个噬菌体的所有蛋白，计算以下理化性质的均值、方差、最大值、最小值：

| 理化性质 | 统计量 | 特征数 | 生物学意义 |
|----------|--------|--------|------------|
| `molecular_weight` (MW) | mean, std, max, min | 4 | 蛋白大小分布 |
| `isoelectric_point` (pI) | mean, std, max, min | 4 | 电荷分布 |
| `aromaticity` | mean, std | 2 | 芳香族氨基酸比例 |
| `instability_index` | mean, std | 2 | 蛋白稳定性 |
| `helix_fraction` (H) | mean, std | 2 | alpha螺旋比例 |
| `turn_fraction` (T) | mean, std | 2 | 转角比例 |
| `strand_fraction` (E) | mean, std | 2 | beta折叠比例 |
| `aliphatic_index` | mean, std | 2 | 脂肪族指数 |
| `gravy` (GRAVY) | mean, std | 2 | 亲水性指数 |

**总维度**：9 + 22 = 31维

#### 2.3.3 第三层：互作信号层（Interaction-level）

| 特征来源 | 特征名称 | 数据类型 | 提取方法 | 生物学意义 |
|----------|----------|----------|----------|------------|
| `anticrispr_protein` | `acr_type_count` | 离散 | 按Acr类型计数 | 抗CRISPR能力 |
| `anticrispr_protein` | `acr_total_count` | 离散 | 所有Acr蛋白总数 | 抗防御总能力 |
| `anticrispr_protein` | `acr_diversity` | 离散 | 不同Acr类型数 | 抗防御多样性 |
| `transmembrane_protein` | `tm_protein_count` | 离散 | 跨膜蛋白总数 | 膜穿透和受体识别 |
| `transmembrane_protein` | `tm_domain_count` | 离散 | 跨膜域总数 | 膜整合程度 |
| `transmembrane_protein` | `tm_mean_domains` | 连续 | 平均跨膜域数 | 跨膜复杂度 |
| `crispr_array` | `spacer_count` | 离散 | CRISPR spacer总数 | 宿主历史感染记录 |
| `crispr_array` | `unique_spacers` | 离散 | 去重后的spacer数 | 感染多样性 |
| `crispr_array` | `spacer_host_match_ratio` | 连续 | spacer与宿主匹配比例 | 直接互作证据 |

**总维度**：9维

#### 2.3.4 三层特征汇总

| 层级 | 维度 | 信息来源 | 预测能力预期 |
|------|------|----------|-------------|
| 宏观基因组层 | ~50-100 | curated_metadata | 中等（全局特征） |
| 蛋白功能层 | ~31 | annotated_protein | 强（功能直接相关） |
| 互作信号层 | ~9 | anticrispr/transmembrane/crispr | 最强（直接互作信号） |
| **合计** | **~90-140** | - | - |

### 2.4 宿主侧特征定义

#### 2.4.1 宿主元数据特征

| 特征名称 | 数据类型 | 来源 | 描述 |
|----------|----------|------|------|
| `host_genus` | 类别 | curated_metadata.Host | 宿主属名 |
| `host_phylum` | 类别 | taxonomy | 宿主门 |
| `host_gc_content` | 连续 | 文献/数据库 | 宿主基因组GC含量 |
| `host_genome_size` | 连续 | 文献/数据库 | 宿主基因组大小 |
| `host_oxygen_requirement` | 类别 | 文献/数据库 | 需氧/厌氧/兼性 |
| `host_pathogenicity` | 类别 | 文献/数据库 | 致病/非致病 |
| `host_habitat` | 类别 | 文献/数据库 | 肠道/土壤/水体等 |

#### 2.4.2 系统发育距离矩阵

```python
# 构建噬菌体-噬菌体系统发育距离矩阵 D_phage
# 方法1：基于分类学层级
# D_phage[i,j] = 同Family不同Genus -> 3, 同Genus不同Species -> 2, 同Species -> 1, 完全相同 -> 0

# 方法2：基于基因组ANI (Average Nucleotide Identity)
# D_phage[i,j] = 1 - ANI(phage_i, phage_j)

# 构建宿主-宿主系统发育距离矩阵 D_host
# 基于16S rRNA序列或taxonomy层级
# D_host[i,j] = 系统发育分支距离
```

#### 2.4.3 宿主受体蛋白特征

```python
# 常见受体类型：外膜蛋白(OmpA/C/F)、脂多糖(LPS)、鞭毛蛋白、菌毛蛋白、磷壁酸
host_receptor_features = {
    'omp_present': 0/1,          # 是否有外膜蛋白
    'lps_gene_count': int,       # LPS合成基因数量
    'flagella_gene_count': int,  # 鞭毛相关基因数量
    'pili_gene_count': int,      # 菌毛相关基因数量
}
```

### 2.5 数据预处理流程

#### 2.5.1 缺失值处理策略

| 特征类型 | 缺失率范围 | 处理策略 | 理由 |
|----------|------------|----------|------|
| 连续数值 | < 5% | 中位数填充 | 对异常值鲁棒 |
| 连续数值 | 5-30% | KNN填充 (k=5) | 利用相似噬菌体的信息 |
| 连续数值 | > 30% | 丢弃该特征 | 信息量不足 |
| 类别型 | < 10% | 众数填充 | 最常见的类别 |
| 类别型 | 10-30% | 新增"Unknown"类别 | 保留数据 |
| 类别型 | > 30% | 丢弃该特征 | 噪声太大 |

#### 2.5.2 类别特征编码

```python
# 低基数类别（< 10个唯一值）：One-hot编码
# 中基数类别（10-100个唯一值）：Target Encoding
# 高基数类别（> 100个唯一值）：Embedding层 (nn.Embedding)
```

#### 2.5.3 数值特征标准化

```python
# 选择 RobustScaler（而非StandardScaler）
# 理由：噬菌体基因组长度、蛋白数量等特征存在长尾分布
from sklearn.preprocessing import RobustScaler
scaler = RobustScaler()
X_scaled = scaler.fit_transform(X_numeric)
```

#### 2.5.4 特征选择方法

```python
# 阶段1：方差阈值过滤 (threshold=0.01)
# 阶段2：互信息排序 (保留top-80%)
# 阶段3：LASSO正则化选择 (保留非零系数)
# 最终特征集 = 三阶段的交集
```

---

## 第三章：算法理论与模型架构

### 3.1 最优传输理论基础

#### 3.1.1 Monge 问题

给定两个概率空间 (X, mu) 和 (Y, nu)，Monge问题寻找映射 T: X -> Y：

$$T_{\#}\mu = \nu \quad \text{且} \quad \min_T \int_X c(x, T(x)) \, d\mu(x)$$

其中 c(x,y) 是将质量从 x 传输到 y 的代价。Monge问题可能无解（当 mu 有原子而 nu 没有时）。

#### 3.1.2 Kantorovich 松弛

松弛为寻找联合分布（耦合）：

$$\min_{\gamma \in \Pi(\mu, \nu)} \int_{X \times Y} c(x, y) \, d\gamma(x, y)$$

其中 Pi(mu, nu) 是所有边际为 mu 和 nu 的联合分布的集合。始终有解。

#### 3.1.3 Wasserstein 距离

$$W_p(\mu, \nu) = \left( \inf_{\gamma \in \Pi(\mu, \nu)} \int_{X \times Y} c(x, y)^p \, d\gamma(x, y) \right)^{1/p}$$

当 p=2, c(x,y) = ||x - y||_2 时即为 2-Wasserstein 距离。

#### 3.1.4 Sinkhorn 算法与熵正则化

$$\min_{\gamma \in \Pi(\mu, \nu)} \langle \gamma, C \rangle_F - \varepsilon H(\gamma)$$

其中 H(gamma) 是熵，epsilon > 0 是正则化参数。

**Sinkhorn迭代**：gamma = diag(u) * K * diag(v)，其中 K = exp(-C/epsilon)

$$u^{(k+1)} = \frac{a}{K v^{(k)}}, \quad v^{(k+1)} = \frac{b}{K^T u^{(k+1)}}$$

**收敛性**：当 epsilon > 0 时保证线性收敛。

#### 3.1.5 Sliced-Wasserstein 距离

$$SW_p(\mu, \nu) = \left( \int_{S^{d-1}} W_p^p(\theta_\#^* \mu, \theta_\#^* \nu) \, d\sigma(\theta) \right)^{1/p}$$

一维Wasserstein有闭合解（排序），复杂度 O(n log n)。

#### 3.1.6 Gromov-Wasserstein 距离

$$GW(C_X, C_Y, \mu, \nu) = \min_{\gamma \in \Pi(\mu, \nu)} \sum_{i,j,k,l} (C_X(i,k) - C_Y(j,l))^2 \gamma_{ij} \gamma_{kl}$$

比较 X 内部结构和 Y 内部结构的一致性。

#### 3.1.7 Fused Gromov-Wasserstein (FGW) 距离

$$FGW_\alpha(C_X, C_Y, M, \mu, \nu) = \min_{\gamma \in \Pi(\mu, \nu)} (1-\alpha) \sum_{i,j} M_{ij} \gamma_{ij} + \alpha \sum_{i,j,k,l} (C_X(i,k) - C_Y(j,l))^2 \gamma_{ij} \gamma_{kl}$$

其中：
- M_{ij}: 噬菌体蛋白 i 和宿主蛋白 j 之间的特征距离
- C_X(i,k): 噬菌体蛋白 i 和 k 之间的系统发育/功能距离
- C_Y(j,l): 宿主蛋白 j 和 l 之间的距离
- alpha 属于 [0, 1]: 控制结构项和属性项的平衡

**本研究的创新**：首次在PHI预测中使用FGW距离。

### 3.2 PHI-OT 模型架构（三大模块）

#### 模块1：双变分自编码器（Dual-VAE）

**架构图**：

```
噬菌体侧                                    宿主侧

[噬菌体特征 x_p]                           [宿主特征 x_h]
     |                                          |
     v                                          v
+-----------------+                    +-----------------+
|  Phage Encoder  |                    |  Host Encoder   |
|  FC(140->256)   |                    |  FC(30->128)    |
|  ReLU + BN      |                    |  ReLU + BN      |
|  FC(256->128)   |                    |  FC(128->64)    |
|  ReLU + BN      |                    |  ReLU + BN      |
|  FC(128->64)    |                    |  FC(64->32)     |
|  -> mu_p, sigma_p                    |  -> mu_h, sigma_h
+--------+--------+                    +--------+--------+
         |                                      |
    重参数化: z_p = mu_p + sigma_p * eps   重参数化: z_h = mu_h + sigma_h * eps
         |                                      |
         v                                      v
+-----------------+                    +-----------------+
|  Phage Decoder  |                    |  Host Decoder   |
|  FC(32->64)     |                    |  FC(32->64)     |
|  FC(64->128)    |                    |  FC(64->128)    |
|  FC(128->256)   |                    |  FC(128->140)   | <-- 跨域
|  FC(256->140)   |                    |  (重构噬菌体特征) |
|  -> x_hat_p     |                    |  -> x_hat_cross |
+-----------------+                    +-----------------+
```

**损失函数**：

$$\mathcal{L}_{VAE} = \|x_p - \hat{x}_p\|^2 + \beta \cdot D_{KL}(q(z_p|x_p) \| p(z_p)) + \lambda \cdot \|x_p - \hat{x}_{p,cross}\|^2$$

- beta: 控制潜在空间正则化强度（beta-VAE策略，从1逐渐增加到4）
- lambda: 控制跨域学习权重（初始0.1，训练后期增加到0.5）

**跨域拼接的生物学意义**：
- Phage Encoder -> Host Decoder：迫使潜在空间编码与宿主相关的信息
- Host Encoder -> Phage Decoder：迫使潜在空间编码与噬菌体相关的信息

#### 模块2：FGW-OT 匹配引擎

**步骤1：构建蛋白嵌入分布**

```python
# 噬菌体侧
for each phage protein:
    embedding = ESM2_encode(protein_sequence)  # -> 1280维向量
phage_distribution = empirical_measure(phage_embeddings)  # 均匀权重 1/m

# 宿主侧
for each host surface protein:
    embedding = ESM2_encode(protein_sequence)
host_distribution = empirical_measure(host_embeddings)  # 均匀权重 1/n
```

**步骤2：构建距离矩阵**

```python
# M: 跨域特征距离矩阵 (m x n), cosine_distance
M = pairwise_cosine_distance(phage_embeddings, host_embeddings)

# C_X: 噬菌体内部结构距离矩阵 (m x m), Jaccard距离
C_X[i,k] = jaccard_distance(phage_func[i], phage_func[k])

# C_Y: 宿主内部结构距离矩阵 (n x n), pathway距离
C_Y[j,l] = pathway_distance(host_protein[j], host_protein[l])
```

**步骤3：FGW距离计算**

$$FGW_\alpha = (1-\alpha) \sum_{i,j} M_{ij} \pi_{ij} + \alpha \sum_{i,j,k,l} (C_X(i,k) - C_Y(j,l))^2 \pi_{ij} \pi_{kl}$$

**步骤4：Sinkhorn求解**

```python
pi = sinkhorn_knopp(
    a=phage_weights,      # [1/m, 1/m, ..., 1/m]
    b=host_weights,       # [1/n, 1/n, ..., 1/n]
    M=cost_matrix,        # FGW代价矩阵
    reg=epsilon,          # 熵正则化参数
    numItermax=1000,
    stopThr=1e-9
)

# 传输代价作为互作可能性分数
interaction_score = np.sum(pi * cost_matrix)

# 概率配对矩阵 pi 揭示关键互作位点
key_pairs = get_top_k_pairs(pi, k=10)
```

#### 模块3：迭代改进循环

```
+-------------------------------------------------+
|                  迭代改进循环                      |
|                                                  |
|  +----------+    +----------+    +----------+   |
|  |  预测    |--->|  评估    |--->|  筛选    |   |
|  | FGW-OT   |    | 置信度   |    | 高/低    |   |
|  +----------+    +----------+    +----+-----+   |
|       ^                               |          |
|       |         +----------+          |          |
|       +---------|  更新    |<---------+          |
|                 | 训练集   |                     |
|                 +----------+                     |
|                                                  |
|  高置信预测（score > 0.9）-> 加入训练集            |
|  低置信预测（0.4 < score < 0.6）-> 主动学习查询   |
|  中间预测（0.6 <= score <= 0.9）-> 保持不变       |
+-------------------------------------------------+
```

**迭代策略**：
1. 第1轮：使用有标注数据训练初始模型
2. 第2轮：对无标注噬菌体预测，高置信预测作为伪标签加入训练
3. 第3轮：低置信预测提交给湿实验验证或文献检索
4. 收敛判据：连续2轮验证集F1变化 < 0.5%

### 3.3 OT与替代方案的对比

| 方法 | 输入表示 | 比较方式 | 可解释性 | 集合大小不变性 | 理论保证 |
|------|----------|----------|----------|----------------|----------|
| 余弦相似度 | 固定向量 | 点对点 | 低 | 需固定长度 | 无 |
| 注意力机制 | 序列/集合 | 加权求和 | 中 | 是 | 部分（梯度问题） |
| 图神经网络 | 图结构 | 消息传递 | 中 | 是 | 部分（过平滑） |
| 集成学习(iPHoP) | 多源特征 | 投票/加权 | 中 | 否 | 无 |
| **Wasserstein** | **概率分布** | **全局最优配对** | **高** | **是** | **是（度量性质）** |
| **FGW** | **分布+结构** | **结构+属性融合** | **高** | **是** | **是** |

---

## 第四章：金标准数据集构建

### 4.1 正样本构建

**来源**：PhageScope中 Host 字段已标注的噬菌体-宿主对

**构建流程**：

```python
# 步骤1：提取所有有宿主标注的噬菌体
phage_host_pairs = curated_metadata[
    curated_metadata['Host'].notna() &
    (curated_metadata['Completeness'].isin(['High-quality', 'Medium-quality']))
][['Phage_ID', 'Host', 'Family', 'Genus', 'Completeness']]

# 步骤2：Host归一化到Genus级别
phage_host_pairs['Host_genus'] = phage_host_pairs['Host'].apply(normalize_to_genus)
```

**验证标准详解**：

| 验证级别 | 条件 | 预期数量 | 使用场景 |
|----------|------|----------|----------|
| Level 1 | Host字段非空 + High/Medium quality | ~40,000-60,000对 | 大规模训练 |
| Level 2 | Level 1 + 至少1个CRISPR spacer匹配 | ~15,000-25,000对 | 主实验 |
| Level 3 | Level 2 + 文献实验验证 | ~2,000-5,000对 | 最终评估 |

### 4.2 负样本构建

#### 策略A：随机配对

```python
negative_pairs_A = []
for phage_id, true_host_genus in positive_pairs:
    candidate_genera = all_genera - {true_host_genus}
    false_hosts = random.sample(candidate_genera, 3)
    for fh in false_hosts:
        negative_pairs_A.append((phage_id, fh))
```

优点：简单，大量可用。缺点：可能包含假阴性。

#### 策略B：系统发育感知配对

```python
for phage_id, true_host_genus in positive_pairs:
    nearby_genera = get_phylogenetically_close(true_host_genus, top_k=3)
    for nearby in nearby_genera:
        if (phage_id, nearby) not in positive_pairs:
            negative_pairs_B.append((phage_id, nearby))
```

优点：更困难的负样本。缺点：假阴性风险更高。

#### 策略C：反向CRISPR证据

```python
for host_genus in all_genera:
    host_crispr_spacers = get_crispr_spacers(host_genus)
    for phage_id in all_phages:
        matches = count_spacer_matches(host_crispr_spacers, get_genome(phage_id))
        if matches == 0 and (phage_id, host_genus) not in positive_pairs:
            negative_pairs_C.append((phage_id, host_genus))
```

优点：最严格的负样本。缺点：数量有限。

#### 正负样本比例实验设计

| 实验编号 | 正样本 | 负样本策略 | 比例 | 预期难度 |
|----------|--------|------------|------|----------|
| Exp-N1 | Level 2 | 策略A | 1:1 | 简单（baseline） |
| Exp-N2 | Level 2 | 策略A | 1:3 | 中等 |
| Exp-N3 | Level 2 | 策略A | 1:5 | 困难（类别不平衡） |
| Exp-N4 | Level 2 | 策略B | 1:1 | 困难（系统发育近） |
| Exp-N5 | Level 2 | 策略C | 1:1 | 最严格 |
| Exp-N6 | Level 2 | 策略A+B+C混合 | 1:3 | 综合评估 |

### 4.3 数据划分策略

#### 划分方案1：随机划分（Baseline）

```python
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, stratify=y)
# 不控制系统发育泄漏
```

#### 划分方案2：系统发育感知划分（推荐主实验）

```python
from sklearn.model_selection import GroupKFold
groups = curated_metadata['Subcluster']
gkf = GroupKFold(n_splits=5)
for train_idx, test_idx in gkf.split(X, y, groups):
    # 同一Subcluster的噬菌体不会同时出现在训练和测试集
    X_train, X_test = X[train_idx], X[test_idx]
```

#### 划分方案3：时间感知划分

```python
# 按PhageScope收录时间排序
split_date = curated_metadata['submission_date'].quantile(0.8)
train_data = curated_metadata[curated_metadata['submission_date'] <= split_date]
test_data = curated_metadata[curated_metadata['submission_date'] > split_date]
```

#### 划分方案4：宿主感知划分（零样本测试）

```python
held_out_genera = ['Mycobacterium', 'Vibrio', 'Clostridium']
train_data = curated_metadata[~curated_metadata['Host_genus'].isin(held_out_genera)]
test_data = curated_metadata[curated_metadata['Host_genus'].isin(held_out_genera)]
```

#### 划分比例汇总

| 划分方案 | 训练/验证/测试 | 主要用途 | 评估能力 |
|----------|----------------|----------|----------|
| 随机划分 | 70/10/20 | Baseline | 标准泛化 |
| 系统发育感知 | 80/20 (5-fold CV) | **主实验** | 无泄漏泛化 |
| 时间感知 | 80/20 | 辅助实验 | 时间泛化 |
| 宿主感知 | 85/15 | 零样本实验 | 零样本泛化 |

---

## 第五章：实验设计

### 5.1 评估指标体系

#### 5.1.1 属级预测指标

| 指标 | 公式/描述 | 预期目标 | 说明 |
|------|-----------|----------|------|
| **Accuracy** | (TP+TN)/(TP+TN+FP+FN) | > 90% | 整体正确率 |
| **Precision** | TP/(TP+FP) | > 88% | 预测为正的准确率 |
| **Recall** | TP/(TP+FN) | > 85% | 正样本的检出率 |
| **F1-macro** | 2*P*R/(P+R)，按类别平均 | > 87% | 平衡精确和召回 |
| **AUROC** | ROC曲线下面积 | > 0.95 | 排序能力 |
| **AUPRC** | PR曲线下面积 | > 0.90 | 不平衡数据下的排序能力 |

#### 5.1.2 菌株级预测指标

| 指标 | 描述 | 预期目标 | 说明 |
|------|------|----------|------|
| **Top-1 Accuracy** | 最高分预测正确的比例 | > 70% | 最严格 |
| **Top-3 Accuracy** | 前3名中包含正确答案 | > 85% | 实用性指标 |
| **Top-5 Accuracy** | 前5名中包含正确答案 | > 90% | 宽松指标 |
| **MRR** | Mean Reciprocal Rank | > 0.75 | 排序质量 |

#### 5.1.3 校准指标

| 指标 | 公式/描述 | 预期目标 | 说明 |
|------|-----------|----------|------|
| **ECE** | Expected Calibration Error | < 0.05 | 预测概率与实际频率一致性 |
| **Brier Score** | (1/N)*sum(p_i - y_i)^2 | < 0.10 | 概率预测的均方误差 |

#### 5.1.4 生物学相关性指标

| 指标 | 描述 | 计算方式 |
|------|------|----------|
| **网络Jaccard** | 预测互作网络与已知网络的相似度 | 预测交已知 / 预测并已知 |
| **宿主范围覆盖** | 预测的宿主范围与实验验证的重叠度 | 按噬菌体计算 |
| **RBP-receptor匹配** | 预测的关键蛋白对与已知RBP-receptor对的匹配率 | OT传输计划分析 |

### 5.2 基准工具对比实验

#### 5.2.1 基准工具列表

| 工具 | 版本 | 核心技术 | 输入格式 | 关键参数 |
|------|------|----------|----------|----------|
| **iPHoP** (Roux 2023) | v1.4.1 | 集成学习（5类信号） | FASTA | 默认参数，score_threshold=0.7 |
| **PHIStruct** (2025) | v1.0 | ESMFold结构嵌入+SVM | FASTA | kernel=RBF, C=1.0 |
| **GE-PHI** (2025) | v1.0 | 图嵌入+GNN | 蛋白互作网络 | embedding_dim=256, layers=3 |
| **MoEPH** (2025) | v1.0 | 混合专家+PLM | FASTA | num_experts=8, PLM=ESM-2 |
| **PhageCGRNet** (2025) | v1.0 | 混沌博弈表示+CNN | FASTA | CGR_resolution=128, epochs=100 |
| **DeepHost** (2021) | v1.0 | k-mer+DNN | FASTA | k=6, hidden=512 |
| **PHI-OT (ours)** | v1.0 | 双VAE+FGW-OT | 多模态特征 | alpha=0.5, epsilon=0.1, latent=32 |

#### 5.2.2 对比实验结果表格模板

所有数值报告为 5-fold CV 的均值 +/- 标准差

| 方法 | Accuracy | Precision | Recall | F1-macro | AUROC | AUPRC | ECE |
|------|----------|-----------|--------|----------|-------|-------|-----|
| DeepHost | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | 0.xxx | 0.xxx | 0.xxx |
| iPHoP | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | 0.xxx | 0.xxx | 0.xxx |
| PHIStruct | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | 0.xxx | 0.xxx | 0.xxx |
| GE-PHI | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | 0.xxx | 0.xxx | 0.xxx |
| MoEPH | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | 0.xxx | 0.xxx | 0.xxx |
| PhageCGRNet | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | xx.x +/- x.x | 0.xxx | 0.xxx | 0.xxx |
| **PHI-OT (ours)** | **xx.x +/- x.x** | **xx.x +/- x.x** | **xx.x +/- x.x** | **xx.x +/- x.x** | **0.xxx** | **0.xxx** | **0.xxx** |

#### 5.2.3 分层评估（按宿主属）

| 宿主属 | 样本数 | iPHoP F1 | PHIStruct F1 | PHI-OT F1 | 提升幅度 |
|--------|--------|----------|-------------|-----------|----------|
| Escherichia | ~8,000 | xx.x | xx.x | xx.x | +x.x |
| Pseudomonas | ~5,000 | xx.x | xx.x | xx.x | +x.x |
| Staphylococcus | ~3,000 | xx.x | xx.x | xx.x | +x.x |
| Klebsiella | ~4,000 | xx.x | xx.x | xx.x | +x.x |
| Salmonella | ~2,500 | xx.x | xx.x | xx.x | +x.x |
| Acinetobacter | ~2,000 | xx.x | xx.x | xx.x | +x.x |
| Mycobacterium | ~1,500 | xx.x | xx.x | xx.x | +x.x |
| Bacillus | ~1,800 | xx.x | xx.x | xx.x | +x.x |

### 5.3 消融实验设计

#### 5.3.1 实验列表

| 实验编号 | 配置 | 移除/替换的组件 | 验证的假设 |
|----------|------|-----------------|-----------|
| **Exp-A1** | 完整 PHI-OT | 无（完整模型） | 基准 |
| **Exp-A2** | OT->余弦 | FGW-OT替换为余弦相似度 | OT优于简单向量比较 |
| **Exp-A3** | FGW->W | FGW替换为普通Wasserstein | 结构信息的重要性 |
| **Exp-A4** | 无VAE | 移除双VAE，原始特征直接计算OT | VAE表示学习的贡献 |
| **Exp-A5** | 无互作信号层 | 移除Acr/跨膜/CRISPR特征 | 互作信号层特征的贡献 |
| **Exp-A6** | 无蛋白功能层 | 移除蛋白功能和理化特征 | 蛋白功能层特征的贡献 |
| **Exp-A7** | 无基因组层 | 移除宏观基因组特征 | 基因组层特征的贡献 |
| **Exp-A8** | 无跨域拼接 | 移除跨域VAE拼接（仅独立VAE） | 跨域学习的贡献 |
| **Exp-A9** | 无迭代改进 | 移除迭代改进循环 | 迭代改进的贡献 |
| **Exp-A10** | ESM->ProtBERT | 蛋白嵌入使用ProtBERT替代ESM-2 | 嵌入模型选择的影响 |
| **Exp-A11** | 无蛋白嵌入 | 完全移除蛋白嵌入，仅用PhageScope预计算特征 | PLM vs 预计算特征 |

#### 5.3.2 消融实验结果表格模板

| 实验 | 配置 | Accuracy | F1-macro | AUROC | AUPRC | 相对A1变化 |
|------|------|----------|----------|-------|-------|-----------|
| A1 | 完整模型 | xx.x | xx.x | 0.xxx | 0.xxx | - |
| A2 | OT->余弦 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A3 | FGW->W | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A4 | 无VAE | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A5 | 无互作信号层 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A6 | 无蛋白功能层 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A7 | 无基因组层 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A8 | 无跨域拼接 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A9 | 无迭代改进 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |
| A10 | ESM->ProtBERT | xx.x | xx.x | 0.xxx | 0.xxx | +/-x.x |
| A11 | 无蛋白嵌入 | xx.x | xx.x | 0.xxx | 0.xxx | -x.x |

#### 5.3.3 预期结果与解释

| 实验 | 预期变化 | 解释 |
|------|----------|------|
| A2 (OT->余弦) | F1下降 5-10% | OT的全局最优配对优于点对比较 |
| A3 (FGW->W) | F1下降 2-5% | 结构信息（系统发育）提供补充信号 |
| A4 (无VAE) | F1下降 3-8% | VAE学习的潜在表示优于原始高维特征 |
| A5 (无互作信号层) | F1下降 8-15% | Acr/CRISPR是最直接的互作信号 |
| A6 (无蛋白功能层) | F1下降 5-10% | 蛋白功能分类提供重要上下文 |
| A7 (无基因组层) | F1下降 1-3% | 基因组层信息较弱，主要作为辅助 |
| A8 (无跨域拼接) | F1下降 3-6% | 跨域学习迫使潜在空间编码互作信息 |
| A9 (无迭代改进) | F1下降 2-4% | 伪标签扩展训练数据 |

### 5.4 超参数敏感性实验

#### 5.4.1 FGW 平衡参数 alpha

| alpha 值 | 含义 | 预期表现 |
|----------|------|----------|
| 0.0 | 纯Wasserstein（仅属性） | 忽略结构信息，表现较差 |
| 0.1 | 属性主导 | 接近纯Wasserstein |
| 0.3 | 属性偏重 | 较好平衡 |
| **0.5** | **等权** | **预期最优** |
| 0.7 | 结构偏重 | 系统发育信号过强 |
| 0.9 | 结构主导 | 接近纯GW |
| 1.0 | 纯GW（仅结构） | 忽略属性信息 |

实验方法：在验证集上网格搜索，绘制 alpha vs F1-macro 曲线。

#### 5.4.2 Sinkhorn 熵正则化 epsilon

| epsilon 值 | 效果 | 收敛速度 | 精度 |
|------------|------|----------|------|
| 0.01 | 接近精确OT | 慢 | 高 |
| 0.05 | 轻微平滑 | 中等 | 较高 |
| **0.1** | **平衡** | **快** | **良好** |
| 0.5 | 明显平滑 | 很快 | 中等 |
| 1.0 | 高度平滑 | 最快 | 低 |

#### 5.4.3 VAE 潜在空间维度

| 维度 | 表示能力 | 过拟合风险 | 计算成本 |
|------|----------|------------|----------|
| 16 | 低 | 低 | 低 |
| **32** | **中等** | **低** | **低** |
| 64 | 高 | 中等 | 中等 |
| 128 | 很高 | 高 | 高 |

#### 5.4.4 正负样本比例

| 比例 | 类别平衡 | 假阴性风险 | 计算成本 |
|------|----------|------------|----------|
| 1:1 | 平衡 | 低 | 低 |
| **1:3** | **适度不平衡** | **中等** | **中等** |
| 1:5 | 高度不平衡 | 高 | 高 |

#### 5.4.5 蛋白嵌入模型选择

| 模型 | 参数量 | 维度 | 预训练数据 | 推理速度 |
|------|--------|------|------------|----------|
| ESM-2 (8M) | 8M | 320 | UniRef50 | 快 |
| ESM-2 (35M) | 35M | 480 | UniRef50 | 中等 |
| **ESM-2 (150M)** | **150M** | **640** | **UniRef50** | **中等** |
| ESM-2 (650M) | 650M | 1280 | UniRef50 | 慢 |
| ProtBERT | 420M | 1024 | BFD | 中等 |

### 5.5 泛化性实验

#### 5.5.1 跨家族泛化

```python
train_families = ['Myoviridae', 'Siphoviridae', 'Podoviridae',
                  'Autographiviridae', 'Drexlerviridae', 'Straboviridae']
test_families = ['Inoviridae', 'Microviridae']
# 预期：F1下降10-20%
```

#### 5.5.2 跨宿主泛化

```python
train_hosts = ['Escherichia', 'Salmonella', 'Klebsiella', 'Enterobacter']
test_hosts = ['Pseudomonas', 'Acinetobacter']
# 预期：F1下降5-15%
```

#### 5.5.3 零样本宿主预测

```python
held_out = ['Mycobacterium', 'Vibrio', 'Clostridium']
# 模型从未在训练中见过这些宿主
# 预期：F1下降20-35%
```

#### 5.5.4 数据稀缺场景

| 训练集比例 | 样本数（估算） | 预期F1变化 | 说明 |
|------------|----------------|------------|------|
| 100% | ~40,000 | baseline | 完整数据 |
| 50% | ~20,000 | -2-3% | 半数据 |
| 25% | ~10,000 | -5-8% | 1/4数据 |
| 10% | ~4,000 | -10-15% | 稀缺数据 |
| 5% | ~2,000 | -15-25% | 极少数据 |

### 5.6 可解释性分析

#### 5.6.1 OT传输计划可视化

```python
import matplotlib.pyplot as plt
import seaborn as sns

fig, ax = plt.subplots(figsize=(12, 8))
sns.heatmap(pi_matrix,
            xticklabels=host_protein_names,
            yticklabels=phage_protein_names,
            cmap='YlOrRd', ax=ax)
ax.set_xlabel('Host Proteins')
ax.set_ylabel('Phage Proteins')
ax.set_title('OT Transport Plan: Phage XXX vs Host YYY')
plt.savefig('ot_transport_plan.png', dpi=300, bbox_inches='tight')
```

#### 5.6.2 关键蛋白识别

```python
def get_key_interactions(pi_matrix, phage_proteins, host_proteins, top_k=10):
    flat_pi = pi_matrix.flatten()
    top_indices = np.argsort(flat_pi)[-top_k:]
    key_pairs = []
    for idx in top_indices[::-1]:
        i = idx // pi_matrix.shape[1]
        j = idx % pi_matrix.shape[1]
        key_pairs.append({
            'phage_protein': phage_proteins[i],
            'host_protein': host_proteins[j],
            'transport_mass': flat_pi[idx],
            'phage_function': get_function(phage_proteins[i]),
            'host_pathway': get_pathway(host_proteins[j])
        })
    return key_pairs
```

#### 5.6.3 与已知RBP-receptor对的验证

```python
known_pairs = [
    ('T4_gp37', 'Ecoli_OmpC'),
    ('lambda_J', 'Ecoli_LamB'),
    ('T7_gp17', 'Ecoli_LPS'),
    ('P22_tailspike', 'Salmonella_LPS'),
]

for phage_rbp, host_receptor in known_pairs:
    rank = get_pair_rank(pi_matrix, phage_rbp, host_receptor)
    print(f"{phage_rbp} - {host_receptor}: rank = {rank}")
```

### 5.7 计算效率分析

#### 5.7.1 时间复杂度分析

| 组件 | 时间复杂度 | 实际耗时（估算） |
|------|------------|-----------------|
| 特征提取（PhageScope） | O(N) | ~2小时（一次性） |
| ESM-2蛋白嵌入 | O(N*L^2) | ~8小时（GPU） |
| VAE训练 | O(N*d^2*epochs) | ~4小时（GPU） |
| FGW-OT（单次推理） | O(m*n*iter) | ~0.5秒/对 |
| Sinkhorn迭代 | O(m*n*iter) | ~0.1秒/对 |
| **总计（训练）** | - | **~14小时** |
| **总计（推理）** | - | **~0.5秒/对** |

其中：N=94000, L=300aa, d=140, m=100, n=500, iter=100

#### 5.7.2 与基准工具的效率对比

| 工具 | 训练时间 | 单样本推理时间 | 内存占用 | GPU需求 |
|------|----------|----------------|----------|---------|
| iPHoP | ~24h | ~2min | ~32GB | 不需要 |
| PHIStruct | ~48h | ~5min | ~64GB | 需要 |
| GE-PHI | ~12h | ~1s | ~16GB | 需要 |
| MoEPH | ~36h | ~3min | ~48GB | 需要 |
| PhageCGRNet | ~8h | ~0.5s | ~8GB | 需要 |
| **PHI-OT (ours)** | **~14h** | **~0.5s** | **~24GB** | **需要** |

#### 5.7.3 Sinkhorn收敛分析

```python
convergence_log = []
for iteration in range(max_iter):
    pi_new = sinkhorn_step(...)
    residual = np.max(np.abs(pi_new - pi_old))
    convergence_log.append({'iter': iteration, 'residual': residual})
    if residual 