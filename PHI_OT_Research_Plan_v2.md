# PHI-OT：基于最优传输理论的噬菌体-宿主互作预测框架

## 完整研究计划与实验方案

---

## 目录

1. 研究背景与目标
2. PhageScope 数据使用定义
3. 算法理论与模型架构
   - 3.1 整体框架概览
   - 3.2 模块一：Dual-VAE 结构化潜在空间
   - 3.3 模块二：FGW-OT 匹配引擎
   - 3.4 模块三：迭代精化与社区检测
   - 3.5 模块四：多任务预测头
4. 金标准数据集构建
5. 实验设计
   - 5.1 实验一：表征质量验证
   - 5.2 实验二：交互预测性能
     - 5.2.0 OT相对于替代方案的理论优势
     - 5.2.1 研究问题
     - 5.2.2 实验配置
     - 5.2.3 评估指标
     - 5.2.4 预期结果与结论
   - 5.3 实验三：零样本与少样本泛化
   - 5.4 实验四：超参数敏感性与鲁棒性分析
     - 5.4.1–5.4.5 单参数敏感性、稳定性、效率分析
     - 5.4.6 实验协议（网格搜索 + 贝叶斯优化）
     - 5.4.7 交互效应分析（α×ε、α×latent_dim）
     - 5.4.8 最终超参数选择表与置信区间
   - 5.5 实验五：跨数据库泛化能力验证
     - 5.5.1–5.5.4 研究问题、配置、指标、预期结果
     - 5.5.5 统计显著性检验协议
     - 5.5.6 零样本场景的域适应策略
     - 5.5.7 各泛化测试的详细实验协议
   - 5.6 实验六：可解释性分析
     - 5.6.1–5.6.3 研究问题、配置、评估
     - 5.6.4 案例研究（3个噬菌体-宿主对的详细OT计划分析）
     - 5.6.5 可解释性的统计验证（传输计划显著性的置换检验）
     - 5.6.6 与基于注意力的可解释性方法对比
   - 5.7 实验七：计算效率与可扩展性
     - 5.7.1–5.7.3 研究问题、配置、指标
     - 5.7.4 可扩展性分析（性能随数据集规模的变化）
     - 5.7.5 GPU显存优化策略
     - 5.7.6 在线/增量学习能力
6. 预期成果与时间线
   - 6.1 预期成果
   - 6.2 时间线
   - 6.3 风险评估与缓解策略

---

## 第一章：研究背景与目标

### 1.1 核心科学问题

噬菌体-宿主互作（Phage-Host Interaction, PHI）是微生物组研究的核心问题，直接影响：
- **生态平衡**：噬菌体通过裂解特定宿主调控微生物群落组成
- **基因转移**：转导过程促进水平基因转移，包括抗生素抗性基因
- **治疗应用**：噬菌体疗法是应对抗生素耐药性的重要替代方案

**当前挑战**：传统方法（实验验证、基于序列相似性）效率低、覆盖有限，无法应对海量宏基因组数据。

### 1.2 现有方法的局限性

| 方法类别 | 代表工具 | 主要局限 |
|---------|---------|---------|
| 序列相似性 | BLAST | 高假阴性率，无法检测远缘关系 |
| 机器学习 | iPHoP, PHP | 二分类，缺乏可解释性 |
| 深度学习 | DeepHost | 黑盒模型，难以利用生物学先验 |

### 1.3 研究目标

构建 **PHI-OT 框架**，基于最优传输（Optimal Transport, OT）理论，实现：
1. **高精度预测**：噬菌体-宿主交互概率的准确预测
2. **可解释性**：通过传输计划揭示交互的生物学机制
3. **泛化能力**：对未见过的噬菌体/宿主类别的零样本预测
4. **效率**：支持大规模宏基因组数据的快速分析

### 1.4 创新点

1. **理论创新**：首次将 OT 理论应用于 PHI 预测，将交互建模为分布匹配问题
2. **架构创新**：Dual-VAE + FGW-OT + 迭代精化的端到端框架
3. **应用创新**：支持零样本预测、可解释性分析、不确定性量化

---

## 第二章：PhageScope 数据使用定义

### 2.1 数据来源

本项目使用 **PhageScope 3.0** 数据库作为核心数据源：

| 数据项 | 文件 | 用途 |
|-------|------|------|
| 噬菌体元数据 | `curated_metadata.tsv` | 噬菌体特征、宿主信息、分类学 |
| 基因组序列 | `phage_fasta/*.fasta` | 序列特征提取 |
| 蛋白质注释 | `annotated_protein/` | 功能注释、蛋白家族 |
| 蛋白序列 | `protein_fasta/` | 蛋白嵌入生成 |

### 2.2 数据筛选标准

```python
# 数据筛选流程
def load_phagescope_data(data_dir):
    # 加载元数据
    curated_metadata = pd.read_csv(f"{data_dir}/curated_metadata.tsv", sep='\t')
    
    # 加载注释蛋白信息（用于补充元数据中可能缺失的字段）
    annotated_protein = pd.read_csv(f"{data_dir}/annotated_protein/*.tsv", sep='\t')
    
    # 筛选标准
    filtered_data = curated_metadata[
        (curated_metadata['Host_label'].notna()) &          # 有宿主标注
        (curated_metadata['Host_label'] != 'Unclassified') & # 非未分类
        (curated_metadata['Genus'].notna()) &               # 有分类信息
        (curated_metadata['Genome_length'] >= 10000) &      # 基因组长度 >= 10kb
        (curated_metadata['Protein_count'] >= 10)           # 蛋白数量 >= 10
    ].copy()
    
    return filtered_data
```

#### 2.2.1 筛选标准说明

| 筛选条件 | 阈值 | 理由 |
|---------|------|------|
| Host_label 非空 | 必须有值 | 监督学习需要标签 |
| Host_label ≠ Unclassified | 排除未分类 | 避免噪声标签 |
| Genus 非空 | 必须有值 | 分类学特征需要 |
| Genome_length ≥ 10kb | 10,000 bp | 排除碎片化基因组 |
| Protein_count ≥ 10 | 10 个蛋白 | 确保足够特征 |

#### 2.2.2 数据质量验证

```python
def validate_data_quality(df):
    """验证数据质量"""
    checks = {
        'no_duplicate_phage_ids': df['Phage_ID'].is_unique,
        'host_label_coverage': df['Host_label'].notna().mean(),
        'genus_coverage': df['Genus'].notna().mean(),
        'genome_length_stats': df['Genome_length'].describe().to_dict(),
        'protein_count_stats': df['Protein_count'].describe().to_dict(),
    }
    return checks
```

#### 2.2.3 数据使用声明

- **数据来源**：PhageScope 3.0（公开数据库）
- **数据性质**：仅使用预计算的元数据和序列特征，**不重新运行 PhageScope 分析流程**
- **引用**：在使用 PhageScope 数据时需引用其原始论文

#### 2.2.4 Protein_count 字段处理

**注意事项**：`Protein_count` 字段可能不直接存在于 `curated_metadata.tsv` 中。当该字段缺失时，需要从 `annotated_protein` 表聚合计算：

```python
# Fallback: compute from annotated_protein if not in metadata
if 'Protein_count' not in curated_metadata.columns:
    protein_counts = annotated_protein.groupby('Phage_ID').size().reset_index(name='Protein_count')
    curated_metadata = curated_metadata.merge(protein_counts, on='Phage_ID', how='left')
```

此 fallback 确保即使元数据缺少蛋白质计数，也可从注释蛋白表中正确计算。需注意 `merge` 后可能存在 `NaN`（某些 Phage_ID 在 annotated_protein 中无记录），这些记录会被后续的 `Protein_count >= 10` 筛选条件自动排除。

#### 2.2.5 数据规模估算（基于数据审计验证值）

| 筛选步骤 | 保留数量 | 百分比 |
|---------|---------|--------|
| 原始数据 | ~570,000 | 100% |
| Host标注非空 | ~103,000 | ~18.1% (实测 Host_label 非空率 11.8%) |
| Host genus 标准化后 | ~87,000 | ~15.3% |
| 非Unclassified且非Unknown | ~70,000 | ~12.3% |
| Genome_length >= 10kb | ~66,000 | ~11.6% |
| Protein_count >= 10 | ~63,000 | ~11.1% |

> **注意**：以上估算基于 PhageScope 数据审计结果。Host_label 非空率为 **11.8%**（非此前预估的 ~18%），导致后续各级筛选保留量均有所下调。最终可用数据约 **63,000** 个噬菌体（非此前预估的 ~94,000）。此规模仍足以支撑 FGW-OT 模型的训练与评估——以 142 个宿主属计算，平均每属约 444 个噬菌体样本。

### 2.3 数据预处理流程

```
原始数据
  ↓
[1] 加载 curated_metadata.tsv
  ↓
[2] 筛选：Host_label 非空且非 Unclassified
  ↓
[3] 筛选：Genus 非空
  ↓
[4] 筛选：Genome_length >= 10kb
  ↓
[5] 筛选：Protein_count >= 10
  ↓
[6] 标准化 Host genus 名称
  ↓
[7] 构建蛋白质家族特征矩阵
  ↓
[8] 生成序列嵌入（ESM-2）
  ↓
最终数据集
```

### 2.4 数据划分策略

| 划分 | 比例 | 用途 | 划分方式 |
|-----|------|------|---------|
| 训练集 | 70% | 模型训练 | 按宿主属分层抽样 |
| 验证集 | 15% | 超参数调优 | 按宿主属分层抽样 |
| 测试集 | 15% | 性能评估 | 按宿主属分层抽样 |

**关键约束**：同一宿主属的噬菌体不跨划分，避免数据泄露。

### 2.5 与文献综述的关系

| 文献综述领域 | 数据使用 |
|-------------|---------|
| 噬菌体生物学 | 理解宿主特异性机制，指导特征工程 |
| OT 理论 | 算法设计基础，不直接使用数据 |
| VAE 在生物学应用 | 架构设计参考，不直接使用数据 |
| 金标准数据集 | 独立数据来源，用于验证 PHI-OT 性能 |

---

## 第三章：算法理论与模型架构

### 3.1 整体框架概览

```
┌─────────────────────────────────────────────────────────────────┐
│                        PHI-OT 框架                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  输入层                                                          │
│  ├── 噬菌体: 蛋白质家族谱 + 基因组序列                            │
│  └── 宿主菌: 蛋白质家族谱 + CRISPR spacer                         │
│                                                                 │
│  模块一: Dual-VAE 结构化潜在空间                                  │
│  ├── VAE_phage: 噬菌体分布 → N(μ_p, Σ_p)                        │
│  ├── VAE_host: 宿主分布 → N(μ_h, Σ_h)                           │
│  └── 对齐约束: 同一分布下噬菌体-宿主接近                            │
│                                                                 │
│  模块二: FGW-OT 匹配引擎                                         │
│  ├── 特征: 蛋白质家族丰度向量                                      │
│  ├── 结构: 蛋白质共现矩阵/序列相似性图                              │
│  ├── FGW距离: min_π (1-α)·FCD + α·GCD                           │
│  └── 输出: 传输计划 π* (可解释的匹配方案)                          │
│                                                                 │
│  模块三: 迭代精化                                                 │
│  ├── OT-guided VAE: 用 π* 重构 VAE 损失                         │
│  ├── Community Detection: Leiden算法识别功能模块                   │
│  └── 迭代: 3-5轮收敛                                             │
│                                                                 │
│  模块四: 多任务预测头                                              │
│  ├── 交互概率: σ(-OT_distance)                                   │
│  ├── 宿主分类: Softmax(MLP(特征))                                 │
│  └── 不确定性: 分布方差估计                                        │
│                                                                 │
│  输出层                                                          │
│  ├── 噬菌体-宿主交互概率                                          │
│  ├── 可解释传输计划                                               │
│  └── 不确定性量化                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 模块一：Dual-VAE 结构化潜在空间

#### 3.2.1 理论动机

传统 VAE 将每个样本编码为潜在空间的单点，忽略了生物实体的**分布特性**：
- 噬菌体的宿主范围是一个**分布**（可感染多种宿主）
- 蛋白质家族的表达是一个**分布**（不同条件下变化）

#### 3.2.2 架构设计

```python
class DistributionalVAE(nn.Module):
    """
    将生物实体编码为分布而非点
    """
    def __init__(self, input_dim, latent_dim=64):
        super().__init__()
        # 编码器: 输出均值和对数方差
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU()
        )
        self.fc_mu = nn.Linear(128, latent_dim)
        self.fc_logvar = nn.Linear(128, latent_dim)
        
        # 解码器
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 256),
            nn.ReLU(),
            nn.Linear(256, input_dim),
            nn.Sigmoid()
        )
    
    def encode(self, x):
        h = self.encoder(x)
        mu = self.fc_mu(h)
        logvar = self.fc_logvar(h)
        return mu, logvar
    
    def reparameterize(self, mu, logvar):
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        return mu + eps * std
    
    def forward(self, x):
        mu, logvar = self.encode(x)
        z = self.reparameterize(mu, logvar)
        x_recon = self.decoder(z)
        return x_recon, mu, logvar
```

#### 3.2.3 损失函数

```python
def vae_loss(x_recon, x, mu, logvar, beta=1.0):
    """
    VAE 损失 = 重构损失 + β * KL散度
    """
    recon_loss = F.binary_cross_entropy(x_recon, x, reduction='sum')
    kl_loss = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
    return recon_loss + beta * kl_loss
```

#### 3.2.4 对齐约束

```python
def alignment_loss(mu_phage, mu_host, interaction_matrix):
    """
    已知交互的噬菌体-宿主对在潜在空间应接近
    """
    # 正样本: 已知交互的对
    pos_pairs = interaction_matrix.nonzero()
    pos_loss = F.mse_loss(mu_phage[pos_pairs[:, 0]], mu_host[pos_pairs[:, 1]])
    
    # 负样本: 随机采样的非交互对
    neg_pairs = sample_negative_pairs(interaction_matrix)
    neg_loss = F.relu(
        margin - F.pairwise_distance(mu_phage[neg_pairs[:, 0]], mu_host[neg_pairs[:, 1]])
    ).mean()
    
    return pos_loss + neg_loss
```

### 3.3 模块二：FGW-OT 匹配引擎

#### 3.3.1 最优传输理论基础

**问题定义**：给定两个概率分布 $\mu$ 和 $\nu$，找到最优传输计划 $\pi^*$：

$$
\pi^* = \arg\min_{\pi \in \Pi(\mu,\nu)} \int c(x,y) d\pi(x,y)
$$

其中 $\Pi(\mu,\nu)$ 是所有边际为 $\mu$ 和 $\nu$ 的联合分布集合。

#### 3.3.2 Fused Gromov-Wasserstein 距离

FGW 结合了特征相似性和结构相似性：

$$
FGW_\alpha(\mu, \nu) = \min_{\pi \in \Pi(\mu,\nu)} (1-\alpha) \cdot FCD(\pi) + \alpha \cdot GCD(\pi)
$$

**特征成本 (FCD)**：
$$
FCD(\pi) = \sum_{i,j} c_f(x_i, y_j) \pi_{ij}
$$
其中 $c_f$ 是蛋白质家族丰度向量的距离

**结构成本 (GCD)**：
$$
GCD(\pi) = \sum_{i,j,k,l} |d_X(x_i,x_k) - d_Y(y_j,y_l)|^2 \pi_{ij}\pi_{kl}
$$
其中 $d_X, d_Y$ 是内部结构距离

#### 3.3.3 特征与结构定义

**关于 ESM-2 嵌入与 PhageScope 预计算特征的说明**：

在本框架中，不同类型的特征服务于不同的模块：

- **PhageScope 预计算特征**（MW、pI、芳香性、脂肪族指数等 physicochemical 属性）：用于 **模块一（Dual-VAE）** 的结构化潜在空间学习。这些特征是 PhageScope 数据库已预计算好的，无需重新运行分析流程。

- **ESM-2 蛋白嵌入**（768维/层，密集向量表示）：用于 **模块二（FGW-OT 匹配引擎）**。OT 距离的计算需要在嵌入空间中将蛋白质表示为点云（point cloud），每个噬菌体的蛋白质组被表示为一组密集向量 $\{e_1, e_2, \ldots, e_n\}$，FGW 在此点云上进行分布匹配。ESM-2 嵌入捕获了蛋白质的语义信息（结构域、功能位点、进化保守性），是 OT 匹配引擎所需的细粒度特征。

简而言之：Dual-VAE 使用宏观 physicochemical 特征学习分布表示；FGW-OT 使用微观蛋白嵌入进行精确匹配。两者互补，分别捕获不同尺度的生物学信息。

```python
def compute_fgw_distance(phage_features, host_features, 
                         phage_structure, host_structure, alpha=0.5):
    """
    计算噬菌体-宿主的 FGW 距离
    
    Args:
        phage_features: 噬菌体蛋白质家族丰度 [n_proteins, d_features]
        host_features: 宿主蛋白质家族丰度 [m_proteins, d_features]
        phage_structure: 噬菌体内部结构 [n_proteins, n_proteins]
        host_structure: 宿主内部结构 [m_proteins, m_proteins]
        alpha: 特征vs结构的权衡参数
    """
    # 使用 POT 库计算 FGW
    import ot
    
    # 均匀分布权重
    p = np.ones(phage_features.shape[0]) / phage_features.shape[0]
    q = np.ones(host_features.shape[0]) / host_features.shape[0]
    
    # 特征成本矩阵 (欧氏距离)
    C_features = ot.dist(phage_features, host_features, metric='euclidean')
    
    # FGW 距离
    fgw_dist, pi = ot.gromov.fused_gromov_wasserstein2(
        M=C_features,
        C1=phage_structure,
        C2=host_structure,
        p=p, q=q,
        alpha=alpha,
        loss_name='square_loss'
    )
    
    return fgw_dist, pi
```

#### 3.3.4 传输计划的生物学解释

传输计划 $\pi^*$ 提供了**可解释的匹配方案**：

$$
\pi^*_{ij} = \text{噬菌体蛋白 } i \text{ 与宿主蛋白 } j \text{ 的匹配强度}
$$

**生物学意义**：
- $\pi^*_{ij}$ 高值 → 蛋白 $i$ 和 $j$ 可能参与同一生物学过程
- 传输路径揭示感染机制（如受体结合、DNA注入）
- 聚合 $\pi^*$ 可获得蛋白家族层面的匹配模式

### 3.4 模块三：迭代精化与社区检测

#### 3.4.1 OT-guided VAE 重构

使用传输计划 $\pi^*$ 作为软标签，重构 VAE 的对齐约束：

```python
def ot_guided_alignment(mu_phage, mu_host, transport_plan):
    """
    用 OT 传输计划指导 VAE 对齐
    """
    # 软对齐目标: π* 加权的距离
    alignment_target = torch.zeros_like(mu_phage)
    for i in range(mu_phage.shape[0]):
        weights = transport_plan[i, :] / transport_plan[i, :].sum()
        alignment_target[i] = (weights.unsqueeze(1) * mu_host).sum(0)
    
    # 对齐损失
    loss = F.mse_loss(mu_phage, alignment_target)
    return loss
```

#### 3.4.2 社区检测

使用 Leiden 算法在传输计划构建的图上识别功能模块：

```python
import leidenalg as la
import igraph as ig

def detect_communities(transport_plan, threshold=0.1):
    """
    从传输计划中检测功能社区
    """
    # 构建二部图
    edges = []
    weights = []
    for i in range(transport_plan.shape[0]):
        for j in range(transport_plan.shape[1]):
            if transport_plan[i, j] > threshold:
                edges.append((i, j + transport_plan.shape[0]))
                weights.append(transport_plan[i, j])
    
    G = ig.Graph(edges=edges)
    G.es['weight'] = weights
    
    # Leiden 社区检测
    partition = la.find_partition(G, la.ModularityVertexPartition, 
                                   weights='weight')
    
    return partition
```

#### 3.4.3 迭代协议

```
初始化:
  1. 训练 Dual-VAE → μ_p, μ_h
  2. 计算初始 FGW 距离和传输计划 π*

迭代 (3-5轮):
  3. 用 π* 重构 VAE 对齐约束 → 更新 μ_p, μ_h
  4. 社区检测识别功能模块
  5. 重新计算 FGW 距离和 π*
  6. 检查收敛: |FGW_new - FGW_old| < ε
```

### 3.5 模块四：多任务预测头

#### 3.5.1 交互概率预测

```python
def predict_interaction(ot_distance, temperature=1.0):
    """
    从 OT 距离预测交互概率
    """
    # Sigmoid 转换: 距离越小，概率越高
    prob = torch.sigmoid(-ot_distance / temperature)
    return prob
```

#### 3.5.2 宿主分类

```python
class HostClassifier(nn.Module):
    def __init__(self, input_dim, num_hosts):
        super().__init__()
        self.classifier = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(64, num_hosts)
        )
    
    def forward(self, features):
        logits = self.classifier(features)
        return F.softmax(logits, dim=-1)
```

#### 3.5.3 不确定性量化

```python
def estimate_uncertainty(mu, logvar, n_samples=100):
    """
    通过重采样估计预测不确定性
    """
    uncertainties = []
    for _ in range(n_samples):
        z = reparameterize(mu, logvar)
        # 计算 OT 距离和预测
        ot_dist = compute_fgw_distance(z, ...)
        prob = predict_interaction(ot_dist)
        uncertainties.append(prob)
    
    uncertainties = torch.stack(uncertainties)
    return uncertainties.mean(), uncertainties.std()
```

---

## 第四章：金标准数据集构建

### 4.1 数据来源

| 数据库 | 类型 | 规模 | 特点 |
|-------|------|------|------|
| PHIST | 实验验证 | ~6,000对 | 高质量，噬菌体-宿主对 |
| PHIDB | 实验验证 | ~1,800对 | 手动 curated |
| GPD | 宏基因组 | ~85,000对 | 大规模，计算预测 |
| Gut Phage | 肠道特异性 | ~25,000对 | 生态位特异性 |

### 4.2 数据整合流程

```
步骤 1: 数据收集
  ├── 下载 PHIST (phist_db.tsv)
  ├── 下载 PHIDB (phidb_interactions.csv)
  ├── 下载 GPD (gpd_interactions.tsv)
  └── 下载 Gut Phage (gut_phage_hosts.tsv)

步骤 2: 标准化
  ├── 统一噬菌体 ID (NCBI Taxonomy ID)
  ├── 统一宿主 ID (NCBI Taxonomy ID)
  ├── 统一交互类型 (lytic/lysogenic/temperate)
  └── 添加证据类型 (experimental/computational)

步骤 3: 质量过滤
  ├── 移除重复对
  ├── 移除证据不足的交互
  └── 保留至少有 2 个独立来源支持的对

步骤 4: 与 PhageScope 映射
  ├── 匹配噬菌体 (Phage_ID)
  ├── 匹配宿主 (Host_label → Host Taxonomy)
  └── 标记映射状态 (mapped/unmapped)
```

### 4.3 数据集统计

```python
def generate_gold_standard_stats(gold_standard):
    """生成金标准数据集统计"""
    stats = {
        'total_interactions': len(gold_standard),
        'unique_phages': gold_standard['phage_id'].nunique(),
        'unique_hosts': gold_standard['host_id'].nunique(),
        'interaction_types': gold_standard['interaction_type'].value_counts().to_dict(),
        'evidence_sources': gold_standard['evidence_source'].value_counts().to_dict(),
        'mapped_to_phagescope': gold_standard['mapped'].sum(),
    }
    return stats
```

### 4.4 负样本构建策略

```python
def construct_negative_samples(positive_pairs, phagescope_data, strategy='taxonomic_aware'):
    """
    构建负样本（非交互对）
    
    策略:
    1. random: 随机配对（简单但有偏）
    2. taxonomic_aware: 避免同一宿主的噬菌体配对
    3. hard_negative: 选择相似但不交互的对
    """
    negatives = []
    
    if strategy == 'taxonomic_aware':
        # 从不同宿主属的噬菌体中采样
        for phage_id, host_id in positive_pairs:
            phage_genus = get_phage_host_genus(phage_id, phagescope_data)
            candidates = phagescope_data[
                phagescope_data['Genus'] != phage_genus
            ]['Phage_ID'].tolist()
            neg_host = random.choice(candidates)
            negatives.append((phage_id, neg_host))
    
    elif strategy == 'hard_negative':
        # 选择特征相似但不交互的对（更具挑战性）
        for phage_id, host_id in positive_pairs:
            similar_phages = find_similar_phages(phage_id, phagescope_data, top_k=10)
            for sim_phage in similar_phages:
                if (sim_phage, host_id) not in positive_pairs:
                    negatives.append((sim_phage, host_id))
                    break
    
    return negatives
```

### 4.5 数据集划分

| 划分 | 策略 | 用途 |
|-----|------|------|
| 训练集 (70%) | 按宿主属分层 | 模型训练 |
| 验证集 (15%) | 按宿主属分层 | 超参数调优 |
| 测试集 (15%) | 完全未见过的宿主属 | 泛化评估 |

**关键原则**：测试集中的宿主属在训练集中不出现，评估零样本泛化能力。

---

## 第五章：实验设计

### 5.1 实验一：表征质量验证

#### 5.1.1 研究问题

Dual-VAE 是否学到了有意义的噬菌体-宿主分布表征？

#### 5.1.2 实验配置

| 方法 | 描述 |
|------|------|
| t-SNE/UMAP | 可视化潜在空间结构 |
| 聚类评估 | 按宿主属聚类的轮廓系数 |
| 生物学验证 | 已知功能模块的富集分析 |

#### 5.1.3 评估指标

- **轮廓系数 (Silhouette Score)**：同一宿主属的噬菌体在潜在空间应紧密聚集
- **Calinski-Harabasz Index**：聚类间分离度
- **功能富集 p-value**：已知蛋白质家族在聚类中的富集

#### 5.1.4 预期结果

- 同一宿主属的噬菌体在潜在空间形成明显聚类
- 聚类与已知生物学功能相关（如感染机制、宿主范围）

### 5.2 实验二：交互预测性能

#### 5.2.0 OT相对于替代方案的理论优势

> *本节从原第三章 3.3 节移入，作为实验二的理论基础。*

最优传输相对于现有方法具有系统性的理论优势：

| 对比维度 | OT方法 | 余弦相似度 | 注意力机制 | GNN | iPHoP |
|---------|--------|-----------|-----------|-----|-------|
| **输入表示** | 分布（概率测度） | 向量（点估计） | 序列 | 图 | 二值特征 |
| **比较方式** | 全局最优传输 | 局部逐元素 | 局部加权 | 邻域聚合 | 集成投票 |
| **可解释性** | 传输计划π* | 无 | 注意力权重 | 节点重要性 | 无 |
| **集合大小** | 灵活（不同N） | 必须相同 | 必须相同 | 固定图结构 | 固定特征 |
| **几何保持** | Gromov项保持 | 不保持 | 部分 | 部分 | 不保持 |

**关键优势说明**：

1. **分布 vs 点**：噬菌体的宿主范围本质是分布（可感染多种宿主），OT天然建模分布距离，而余弦相似度只能比较固定向量。

2. **全局最优**：OT寻找全局最优匹配方案，避免局部最优陷阱。传输计划π*直接给出蛋白-蛋白匹配强度，具备生物学可解释性。

3. **Gromov结构保持**：GCD项确保匹配时保持内部结构（蛋白共现模式），这对于捕获功能模块至关重要。

4. **灵活基数**：不同噬菌体有不同数量的蛋白，OT不要求集合大小相同，而注意力机制和余弦相似度需要padding或pooling。

#### 从理论优势到实验预测的桥梁

上述理论优势转化为以下可验证的实验预测：

1. **分布建模优势** → 预测 PHI-OT 在宿主范围较广的噬菌体（多宿主phage）上显著优于点对比较方法（余弦/MLP），预期 AUC 提升 3-5%。
2. **全局最优传输** → 预测 FGW-OT 在蛋白质家族组成差异较大的噬菌体-宿主对上仍保持稳健性能，而局部方法（attention）性能下降更明显。
3. **Gromov结构保持** → 预测在消融实验中移除 GCD 项（α=0）导致功能模块检测精度下降 10-15%，尤其影响蛋白共现模式的捕获。
4. **灵活基数** → 预测 PHI-OT 在蛋白数量差异大的噬菌体对（如 10 vs 200 蛋白）上的性能方差小于需要 padding 的方法。

这些预测将在 5.2.1-5.2.4 的实验中得到系统验证。

#### 5.2.1 研究问题

PHI-OT 是否优于现有噬菌体-宿主交互预测方法？

#### 5.2.2 实验配置

| 方法 | 类别 | 关键特点 |
|------|------|---------|
| **PHI-OT (ours)** | OT-based | 可解释，分布匹配 |
| iPHoP | ML ensemble | 当前 SOTA |
| PHP | Deep learning | CNN-based |
| DeepHost | Deep learning | Attention-based |
| BLAST baseline | Sequence similarity | 传统方法 |

#### 5.2.3 评估指标

| 指标 | 描述 |
|------|------|
| AUC-ROC | 整体分类性能 |
| AUC-PR | 不平衡数据下的性能 |
| F1-score | 精确率-召回率平衡 |
| Top-K Accuracy | 推荐场景下的性能 |

#### 5.2.4 预期结果与结论

- PHI-OT 在 AUC-ROC 和 AUC-PR 上达到或超过 iPHoP
- **关键优势**：提供可解释的传输计划，而非黑盒预测
- 零样本场景下性能优势更明显

### 5.3 实验三：零样本与少样本泛化

#### 5.3.1 研究问题

PHI-OT 能否预测未见过噬菌体/宿主的交互？

#### 5.3.2 实验设置

| 场景 | 描述 |
|------|------|
| 零样本宿主 | 测试集中的宿主在训练集中未出现 |
| 零样本噬菌体 | 测试集中的噬菌体在训练集中未出现 |
| 少样本 (K=5) | 仅提供 5 个已知交互 |

#### 5.3.3 评估方法

- 与随机基线和特征相似度基线比较
- 分析性能随 K 的变化曲线

### 5.4 实验四：超参数敏感性与鲁棒性分析

#### 5.4.1 关键超参数

| 超参数 | 范围 | 影响 |
|--------|------|------|
| α (FGW权衡) | [0.1, 0.9] | 特征vs结构 |
| β (KL权重) | [0.1, 1.0] | 正则化强度 |
| latent_dim | [32, 128] | 表征容量 |
| n_iterations | [1, 10] | 迭代精化轮数 |
| ε (OT正则化) | [0.01, 0.1] | 数值稳定性 |

#### 5.4.2 敏感性分析方法

- 网格搜索关键超参数
- 分析性能随参数变化的曲线
- 识别鲁棒区间 vs 敏感区间

#### 5.4.3 鲁棒性测试

| 扰动类型 | 方法 |
|---------|------|
| 输入噪声 | 添加高斯噪声到特征 |
| 缺失值 | 随机遮蔽部分蛋白质家族 |
| 标签噪声 | 翻转一定比例的标签 |

#### 5.4.4 稳定性分析

- 多次运行的性能方差
- 不同随机种子的影响

#### 5.4.5 效率-性能权衡

- 分析迭代次数与性能的权衡
- 确定最优迭代次数

#### 5.4.6 实验协议（网格搜索 + 贝叶斯优化）

**阶段一：粗粒度网格搜索**

对 5 个关键超参数进行粗粒度网格扫描，确定各参数的有效范围和敏感区间：

| 超参数 | 网格值 | 采样点数 |
|--------|--------|---------|
| α | {0.1, 0.3, 0.5, 0.7, 0.9} | 5 |
| β | {0.1, 0.3, 0.5, 0.7, 1.0} | 5 |
| latent_dim | {32, 64, 96, 128} | 4 |
| n_iterations | {1, 3, 5, 7, 10} | 5 |
| ε | {0.01, 0.03, 0.05, 0.08, 0.1} | 5 |

完整网格为 $5 \times 5 \times 4 \times 5 \times 5 = 2500$ 种组合。为控制计算成本，采用分步策略：
1. 固定其他参数为默认值，逐一扫描每个参数（单参数分析，共 24 组实验）
2. 识别敏感参数（性能变化 >5% 的参数）
3. 仅对敏感参数组合进行联合搜索

**阶段二：贝叶斯优化**

在粗粒度搜索确定的有效范围内，使用贝叶斯优化（BO）进行细粒度调优：

```python
from skopt import gp_minimize
from skopt.space import Real, Integer

search_space = [
    Real(0.1, 0.9, name='alpha'),
    Real(0.1, 1.0, name='beta'),
    Integer(32, 128, name='latent_dim'),
    Integer(1, 10, name='n_iterations'),
    Real(0.01, 0.1, name='epsilon')
]

result = gp_minimize(
    func=objective_function,  # 返回 -AUC_PR（最小化）
    dimensions=search_space,
    n_calls=100,              # 100 次评估
    n_initial_points=20,      # 初始随机采样
    acq_func='EI',            # Expected Improvement
    random_state=42
)
```

- **目标函数**：$-AUC\text{-}PR_{val}$（验证集上的 AUC-PR，取负以转化为最小化）
- **代理模型**：高斯过程（GP），Matérn 5/2 核函数
- **采集函数**：Expected Improvement (EI)
- **预算**：100 次评估（约 100 GPU-hours）
- **重复**：3 次独立 BO 运行，取最佳结果

#### 5.4.7 交互效应分析（α×ε、α×latent_dim）

**α×ε 交互效应**：

FGW 权衡参数 α 与 OT 熵正则化参数 ε 存在理论上的耦合关系：
- 高 α（结构主导）+ 低 ε（精确传输）→ 理论上最优但数值不稳定
- 低 α（特征主导）+ 高 ε（平滑传输）→ 稳定但可能模糊结构信息

实验设计：
```
α ∈ {0.1, 0.3, 0.5, 0.7, 0.9}
ε ∈ {0.01, 0.03, 0.05, 0.08, 0.1}
→ 5×5 = 25 组实验，每组 3 次重复
```

预期结果：
- 绘制 AUC-PR 热力图（α vs ε）
- 识别"稳定高性能区域"（AUC-PR > 0.85 的参数区间）
- 验证假设：高 α 需要低 ε 来保持结构信息

**α×latent_dim 交互效应**：

表征维度影响特征空间与结构空间的相对信息量：
- 高 latent_dim → 特征空间信息丰富 → 可能需要降低 α 以充分利用特征
- 低 latent_dim → 特征空间信息有限 → 可能需要提高 α 以依赖结构

实验设计：
```
α ∈ {0.1, 0.3, 0.5, 0.7, 0.9}
latent_dim ∈ {32, 48, 64, 80, 96, 112, 128}
→ 5×7 = 35 组实验，每组 3 次重复
```

分析方法：
- 双因素方差分析 (Two-way ANOVA) 检验交互效应显著性
- 绘制交互效应图（α 为 x 轴，不同 latent_dim 为不同曲线）
- 若交互效应显著 (p < 0.05)，报告联合最优区间而非单独最优值

#### 5.4.8 最终超参数选择表与置信区间

最终超参数选择基于 BO 结果 + 交互效应分析，使用 bootstrap 估计置信区间：

| 超参数 | 选定值 | 95% CI | 选择依据 |
|--------|--------|--------|---------|
| α (FGW权衡) | 待实验确定 | [α_opt ± CI] | BO最优 + α×ε交互分析 |
| β (KL权重) | 待实验确定 | [β_opt ± CI] | β-VAE文献推荐范围 |
| latent_dim | 待实验确定 | [dim_opt ± CI] | BO最优 + α×dim交互分析 |
| n_iterations | 待实验确定 | [iter_opt ± CI] | 收敛曲线拐点 |
| ε (OT正则化) | 待实验确定 | [ε_opt ± CI] | 数值稳定性 + α×ε交互 |

**置信区间估计方法**：
1. 使用 5 折交叉验证的 5 个最优参数集
2. 对每个折的参数取 bootstrap 1000 次重采样
3. 报告 2.5% 和 97.5% 分位数作为 95% CI

**最终确认实验**：
- 使用选定参数在独立测试集上运行 5 次（不同随机种子）
- 报告均值 ± 标准差
- 与 ablation 基线（各参数取默认值）对比

### 5.5 实验五：跨数据库泛化能力验证

#### 5.5.1 研究问题

PHI-OT 在不同数据源上的泛化能力如何？

#### 5.5.2 实验配置

| 训练集 | 测试集 |
|--------|--------|
| PhageScope | PHIST |
| PhageScope | PHIDB |
| PhageScope | GPD (高置信子集) |
| PHIST + PHIDB | PhageScope (子集) |

#### 5.5.3 评估指标

- 跨库性能 vs 库内性能
- 域适应前后的性能差异

#### 5.5.4 预期结果

- PHI-OT 在跨库场景下保持合理性能
- 分析性能下降的原因（分布偏移、特征差异）

#### 5.5.5 统计显著性检验协议

为确保实验结果的统计可靠性，所有泛化实验均执行以下检验协议：

**1. McNemar's 检验（配对分类比较）**

适用于比较 PHI-OT 与基线方法在**同一测试集**上的分类决策差异：

```python
from statsmodels.stats.contingency_tables import mcnemar

def mcnemar_comparison(model_a_preds, model_b_preds, true_labels):
    """
    McNemar's test: 比较两个分类器在相同样本上的差异
    构建 2×2 列联表：
              Model B 正确   Model B 错误
    Model A 正确    a           b
    Model A 错误    c           d
    检验统计量: χ² = (b - c)² / (b + c)
    """
    a = sum((model_a_preds == true_labels) & (model_b_preds == true_labels))
    b = sum((model_a_preds == true_labels) & (model_b_preds != true_labels))
    c = sum((model_a_preds != true_labels) & (model_b_preds == true_labels))
    d = sum((model_a_preds != true_labels) & (model_b_preds != true_labels))
    
    contingency_table = [[a, b], [c, d]]
    result = mcnemar(contingency_table, exact=True)
    return result.pvalue
```

**2. 配对 t 检验 + Bonferroni 校正**

适用于比较 K 折交叉验证中 PHI-OT 与基线方法的**连续指标**差异：

```python
from scipy.stats import ttest_rel

def paired_ttest_with_bonferroni(our_scores, baseline_scores, n_comparisons):
    """
    配对 t 检验 + Bonferroni 多重比较校正
    
    Args:
        our_scores: PHI-OT 在 K 折上的得分 [K,]
        baseline_scores: 基线方法在 K 折上的得分 [K,]
        n_comparisons: 总比较次数（用于 Bonferroni 校正）
    """
    t_stat, p_value = ttest_rel(our_scores, baseline_scores)
    adjusted_alpha = 0.05 / n_comparisons  # Bonferroni 校正
    is_significant = p_value < adjusted_alpha
    
    return {
        't_statistic': t_stat,
        'p_value': p_value,
        'adjusted_alpha': adjusted_alpha,
        'significant': is_significant,
        'mean_diff': np.mean(our_scores) - np.mean(baseline_scores),
        'ci_95': confidence_interval(our_scores - baseline_scores)
    }
```

**3. 多重比较校正方案**

本研究涉及的比较次数：
- 5 个基线方法 × 4 个泛化场景 = **20 次比较**
- Bonferroni 校正后显著性阈值：$\alpha_{adj} = 0.05 / 20 = 0.0025$
- 同时报告 Holm-Bonferroni 校正结果（更宽松但仍控制 FWER）

**4. 效应量报告**

除 p 值外，同时报告 Cohen's d 效应量：
- |d| < 0.2：微小效应
- 0.2 ≤ |d| < 0.5：小效应
- 0.5 ≤ |d| < 0.8：中等效应
- |d| ≥ 0.8：大效应

#### 5.5.6 零样本场景的域适应策略

当目标域（如 PHIST）的宿主分布在训练域（PhageScope）中未覆盖时，需要域适应策略：

**策略一：特征对齐（Feature Alignment）**

```python
class DomainAdversarialLayer(nn.Module):
    """
    域对抗训练：学习域不变特征
    使用梯度反转层 (Gradient Reversal Layer)
    """
    def __init__(self, feature_dim):
        super().__init__()
        self.domain_classifier = nn.Sequential(
            nn.Linear(feature_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 2)  # 源域 vs 目标域
        )
    
    def forward(self, features, alpha=1.0):
        # 梯度反转
        reversed_features = ReverseGradient.apply(features, alpha)
        domain_pred = self.domain_classifier(reversed_features)
        return domain_pred
```

**策略二：OT-based 域适应（最优传输域对齐）**

利用 OT 本身作为域适应工具：
1. 计算源域和目标域特征分布之间的 OT 距离
2. 使用传输计划将源域样本重新加权，使其分布更接近目标域
3. 在重新加权的源域上训练模型

```python
def ot_domain_adaptation(source_features, target_features, source_labels):
    """
    使用 OT 进行域适应
    1. 计算源→目标传输计划
    2. 用传输权重重新加权源域样本
    """
    C = ot.dist(source_features, target_features)
    gamma = ot.emd(
        a=np.ones(len(source_features)) / len(source_features),
        b=np.ones(len(target_features)) / len(target_features),
        M=C
    )
    # 传输权重：每个源域样本的重要性
    sample_weights = gamma.sum(axis=1) * len(source_features)
    return sample_weights
```

**策略三：伪标签自训练**

对于零样本目标域：
1. 用源域训练的模型对目标域样本预测
2. 选择高置信度预测（probability > 0.9）作为伪标签
3. 将伪标签样本加入训练集，重新训练
4. 迭代 3 轮，监控验证集性能防止过拟合

#### 5.5.7 各泛化测试的详细实验协议

**测试 A：PhageScope → PHIST**

| 项目 | 配置 |
|------|------|
| 训练数据 | PhageScope 筛选后 ~63,000 噬菌体 |
| 测试数据 | PHIST 实验验证对（~6,000） |
| 映射方式 | NCBI Taxonomy ID 匹配 |
| 评估 | 5 折交叉验证（按宿主属分层） |
| 基线 | iPHoP、BLAST、随机 |
| 域适应 | 先评估无适应性能，再评估 OT 域适应 |

**测试 B：PhageScope → PHIDB**

| 项目 | 配置 |
|------|------|
| 训练数据 | 同上 |
| 测试数据 | PHIDB 手动 curated 对（~1,800） |
| 特殊考虑 | PHIDB 样本量小，使用 leave-one-out 评估 |
| 报告指标 | 因样本量小，报告 bootstrap CI |

**测试 C：PhageScope → GPD 高置信子集**

| 项目 | 配置 |
|------|------|
| 训练数据 | 同上 |
| 测试数据 | GPD 中 confidence ≥ 0.9 的子集 |
| 特殊考虑 | GPD 为计算预测，存在假阳性，评估时考虑噪声标签 |
| 域适应 | 宏基因组数据分布偏移大，需要完整域适应流程 |

**测试 D：PHIST + PHIDB → PhageScope**

| 项目 | 配置 |
|------|------|
| 训练数据 | PHIST + PHIDB 合并（~7,800 对） |
| 测试数据 | PhageScope 中与训练集无重叠的子集 |
| 目的 | 评估小数据训练 → 大数据泛化的可行性 |
| 特殊考虑 | 训练数据量小，使用数据增强（SMOTE + 特征噪声） |

### 5.6 实验六：可解释性分析

#### 5.6.1 研究问题

传输计划是否提供有意义的生物学解释？

#### 5.6.2 实验配置

- 选择 Top-10 高置信度预测的噬菌体-宿主对
- 分析传输计划揭示的蛋白匹配模式
- 与已知生物学知识对比

#### 5.6.3 评估方法

| 方法 | 描述 |
|------|------|
| 案例研究 | 详细分析特定噬菌体-宿主对 |
| 功能富集 | 匹配蛋白的功能注释富集 |
| 与已知机制对比 | 验证是否与已知感染机制一致 |

#### 5.6.4 案例研究（3个噬菌体-宿主对的详细OT计划分析）

**选择标准**：从测试集中选择 3 个具有代表性的噬菌体-宿主对：
1. **典型溶菌对**：已知受体-配体相互作用的经典噬菌体-宿主对
2. **宽宿主范围对**：噬菌体可感染多个宿主属的情况
3. **远缘关系对**：噬菌体和宿主在分类学上距离较远但存在实验验证的交互

**案例研究模板**：

对于每个选定对 $(P_i, H_j)$，分析以下内容：

**A. 传输计划可视化**
```python
def visualize_transport_plan(pi_star, phage_proteins, host_proteins):
    """
    传输计划热力图 + 蛋白注释
    """
    # 热力图：行=噬菌体蛋白，列=宿主蛋白
    fig, ax = plt.subplots(figsize=(12, 10))
    sns.heatmap(pi_star, ax=ax, cmap='YlOrRd',
                xticklabels=[f"{p.gene}:{p.function}" for p in host_proteins],
                yticklabels=[f"{p.gene}:{p.function}" for p in phage_proteins])
    ax.set_title(f'Transport Plan: {phage_id} → {host_id}')
    return fig
```

**B. 高传输值分析**
- 提取 $\pi^*_{ij} > \text{threshold}$（top-10% 传输值）的蛋白对
- 对每个高传输蛋白对，注释：
  - 蛋白功能（COG/KO 注释）
  - 是否涉及已知感染机制（受体结合蛋白、尾纤维蛋白、溶菌酶等）
  - 蛋白家族归属
- 计算高传输蛋白对中**功能相关**（参与同一生物过程）的比例

**C. 传输模式聚合**
- 按蛋白家族聚合传输计划：$\Pi_{family} = \sum_{i \in F_p, j \in F_h} \pi^*_{ij}$
- 识别主导的家族-家族匹配模式
- 与已知的噬菌体感染功能模块对比

**D. 消融对比**
- 比较完整 FGW-OT vs 仅 FCD（α=0）vs 仅 GCD（α=1）的传输计划
- 验证 Gromov 结构项是否真正捕获了蛋白共现模式

#### 5.6.5 可解释性的统计验证（传输计划显著性的置换检验）

**核心问题**：观察到的传输计划是否显著优于随机匹配？

**置换检验协议**：

```python
def permutation_test_transport_plan(phage_features, host_features, 
                                     phage_structure, host_structure,
                                     observed_pi, n_permutations=1000):
    """
    置换检验：验证传输计划的统计显著性
    
    H0: 观察到的传输模式与随机匹配无差异
    H1: 观察到的传输模式显著优于随机
    """
    # 观测统计量：传输计划的集中度（entropy）
    observed_stat = -np.sum(observed_pi * np.log(observed_pi + 1e-10))
    
    null_stats = []
    for _ in range(n_permutations):
        # 打乱宿主特征（保持噬菌体不变）
        permuted_host_features = np.random.permutation(host_features)
        
        # 计算打乱后的传输计划
        _, permuted_pi = compute_fgw_distance(
            phage_features, permuted_host_features,
            phage_structure, host_structure
        )
        
        # 计算打乱后的统计量
        null_stat = -np.sum(permuted_pi * np.log(permuted_pi + 1e-10))
        null_stats.append(null_stat)
    
    # p-value: 观测统计量比随机更集中（更低熵）的比例
    p_value = np.mean([s <= observed_stat for s in null_stats])
    
    return {
        'observed_entropy': observed_stat,
        'null_mean': np.mean(null_stats),
        'null_std': np.std(null_stats),
        'p_value': p_value,
        'z_score': (observed_stat - np.mean(null_stats)) / np.std(null_stats)
    }
```

**多重检验校正**：
- 对 3 个案例研究的 p 值进行 Bonferroni 校正
- 显著性阈值：$\alpha_{adj} = 0.05 / 3 = 0.0167$

**额外统计量**：
- **传输集中度**：top-10% 传输值占总传输量的比例（高集中度 → 更明确的匹配）
- **模块性得分**：传输计划的模块结构强度（与 Leiden 社区检测结果对比）

#### 5.6.6 与基于注意力的可解释性方法对比

**对比方法**：

| 方法 | 可解释性来源 | 代表模型 |
|------|-------------|---------|
| FGW-OT 传输计划 | 全局最优匹配方案 π* | PHI-OT (ours) |
| Self-Attention 权重 | Query-Key 相似度 | DeepHost |
| GradCAM | 梯度加权的特征图 | PHP (CNN-based) |
| SHAP values | 博弈论特征贡献 | iPHoP + SHAP |

**评估指标**：

1. **功能一致性 (Functional Consistency)**：
   - 高重要性蛋白对（top-K）中，功能相关的比例
   - 对照：随机选择 K 个蛋白对的功能相关比例

2. **稳定性 (Stability)**：
   - 对输入添加微小扰动（±5% 高斯噪声），可解释性输出的一致性
   - 度量：Jaccard 相似度（top-K 蛋白对的重叠度）

3. **忠实度 (Faithfulness)**：
   - 移除 top-K 最重要蛋白后，预测性能的下降幅度
   - 好的可解释性方法应该使性能下降最大（说明确实依赖了这些特征）

4. **生物学验证 (Biological Validation)**：
   - 高重要性蛋白与已知感染机制相关蛋白（RBP、尾纤维、溶菌酶等）的重叠
   - 需要手动文献验证

**实验配置**：
- 对测试集中所有样本计算各方法的可解释性输出
- 对每个指标取平均值 ± 标准差
- 使用 Wilcoxon signed-rank test 比较方法间差异的显著性

### 5.7 实验七：计算效率与可扩展性

#### 5.7.1 研究问题

PHI-OT 的计算效率和可扩展性如何？

#### 5.7.2 实验配置

| 数据集规模 | 噬菌体数 | 宿主数 |
|-----------|---------|--------|
| 小规模 | 1,000 | 50 |
| 中规模 | 10,000 | 200 |
| 大规模 | 100,000 | 1,000 |

#### 5.7.3 评估指标

- 训练时间（小时）
- 推理时间（每对，毫秒）
- 内存占用（GB）

#### 5.7.4 可扩展性分析（性能随数据集规模的变化）

**实验设计**：

系统评估模型性能（预测精度）和计算成本如何随数据集规模缩放：

| 规模级别 | 训练噬菌体数 | 测试对数 | 预期训练时间 |
|---------|-------------|---------|-------------|
| XS | 1,000 | 500 | < 10 min |
| S | 5,000 | 2,000 | ~ 30 min |
| M | 10,000 | 5,000 | ~ 1 hr |
| L | 30,000 | 10,000 | ~ 3 hr |
| XL | 63,000 (full) | 15,000 | ~ 8 hr |

**预测性能扩展曲线**：
- 绘制 AUC-PR vs 训练集大小的学习曲线
- 拟合幂律模型：$Performance = a \cdot N^b + c$
- 确定性能饱和点（边际增益 < 1% 的数据量）
- 验证是否在可用数据范围内达到饱和

**计算成本扩展分析**：

| 组件 | 理论复杂度 | 实测扩展 |
|------|-----------|---------|
| Dual-VAE 训练 | O(N·d) | 线性（预期） |
| FGW 计算（单对） | O(n²·m²) | 二次-四次（蛋白数量） |
| 全量 FGW 矩阵 | O(N²·n²·m²) | 需要 mini-batch |
| 迭代精化 | O(K·total) | K=3-5 倍单轮 |
| Leiden 社区检测 | O(E·log(N)) | 近线性 |

**扩展瓶颈识别与解决方案**：
1. **FGW 计算瓶颈**：当 N > 10,000 时，全量 FGW 矩阵计算不可行 → 使用 mini-batch FGW（每次 256 对）+ 负采样
2. **内存瓶颈**：63,000 个噬菌体的嵌入矩阵 → 使用内存映射 (memory-mapped) 存储
3. **迭代瓶颈**：每轮迭代需重新计算 FGW → 缓存不变部分，仅更新 VAE 参数影响的部分

#### 5.7.5 GPU显存优化策略

**显存消耗分析**：

| 组件 | 显存占用 | 优化策略 |
|------|---------|---------|
| ESM-2 嵌入 | ~2 GB (8M 模型) | 预计算并缓存到磁盘 |
| Dual-VAE 参数 | ~50 MB | 无需优化 |
| FGW 成本矩阵 | O(n·m) 每对 | 分批计算，不缓存完整矩阵 |
| 传输计划 π* | O(n·m) 每对 | 稀疏化（阈值 > 0.01） |
| 梯度 | 2× 参数量 | gradient checkpointing |

**具体优化策略**：

**1. 混合精度训练 (Mixed Precision)**
```python
from torch.cuda.amp import autocast, GradScaler

scaler = GradScaler()
for batch in dataloader:
    optimizer.zero_grad()
    with autocast():  # FP16 前向传播
        loss = model(batch)
    scaler.scale(loss).backward()  # FP16 梯度
    scaler.step(optimizer)
    scaler.update()
```
预期效果：显存减少 ~40%，训练速度提升 ~30%

**2. 梯度检查点 (Gradient Checkpointing)**
- 对 VAE 解码器使用梯度检查点
- 以计算时间换显存（+20% 时间，-50% 显存）

**3. 稀疏传输计划**
- 阈值化 π*：$\pi^*_{ij} = 0$ if $\pi^*_{ij} < \tau$（$\tau = 0.01$）
- 使用稀疏矩阵存储和计算
- 预期稀疏度：>90%（大部分传输值为 0）

**4. ESM-2 嵌入分批预计算**
- 使用 ESM-2 (8M 参数版本) 分批嵌入，每批 32 个蛋白序列
- 嵌入结果缓存到 HDF5 文件
- 训练时从磁盘加载，避免 GPU 上持有 ESM-2 模型

**5. 显存预算与硬件要求**：

| 硬件 | 可用显存 | 最大批量 | 适用规模 |
|------|---------|---------|---------|
| RTX 3090 | 24 GB | 128 对 | M-L (10K-30K) |
| A100 40GB | 40 GB | 256 对 | L-XL (30K-63K) |
| A100 80GB | 80 GB | 512 对 | XL+ (>63K) |

#### 5.7.6 在线/增量学习能力

**应用场景**：PhageScope 数据库持续更新，新噬菌体和新宿主不断加入。模型需要能够增量学习新知识而不遗忘旧知识。

**增量学习协议**：

1. **初始训练**：在 PhageScope v3.0 上训练基础模型
2. **增量更新**：当新版本数据到达时：
   - 冻结 Dual-VAE 编码器（保护已有表征）
   - 仅微调对齐层和预测头
   - 使用 Elastic Weight Consolidation (EWC) 防止灾难性遗忘

```python
class EWCRegularizer:
    """
    Elastic Weight Consolidation: 保护重要参数不被覆盖
    """
    def __init__(self, model, dataloader, n_samples=1000):
        self.model = model
        self.fisher_info = self._compute_fisher(dataloader, n_samples)
        self.optimal_params = {n: p.clone().detach() 
                               for n, p in model.named_parameters()}
    
    def _compute_fisher(self, dataloader, n_samples):
        fisher = {n: torch.zeros_like(p) 
                  for n, p in self.model.named_parameters()}
        for i, batch in enumerate(dataloader):
            if i >= n_samples:
                break
            loss = self.model(batch).loss
            loss.backward()
            for n, p in self.model.named_parameters():
                fisher[n] += p.grad ** 2
        return {n: f / n_samples for n, f in fisher.items()}
    
    def penalty(self):
        loss = 0
        for n, p in self.model.named_parameters():
            loss += (self.fisher_info[n] * (p - self.optimal_params[n]) ** 2).sum()
        return loss
```

**评估指标**：
- **前向迁移 (Forward Transfer)**：新数据上学习后，旧数据性能变化
- **后向迁移 (Backward Transfer)**：旧知识对新任务学习的帮助
- **遗忘率 (Forgetting Rate)**：旧任务性能的下降幅度
- 目标：遗忘率 < 5%，新数据性能 ≥ 从头训练的 90%

---

## 第六章：预期成果与时间线

### 6.1 预期成果

#### 6.1.1 算法成果

| 成果 | 描述 |
|------|------|
| PHI-OT 框架 | 首个基于 OT 的噬菌体-宿主互作预测框架 |
| 开源代码 | GitHub 仓库，包含完整实现 |
| 预训练模型 | 可直接使用的训练好的模型 |

#### 6.1.2 数据成果

| 成果 | 描述 |
|------|------|
| 金标准数据集 | 整合多来源的高质量 PHI 数据集 |
| 预测数据库 | 大规模噬菌体-宿主交互预测结果 |

#### 6.1.3 论文发表

| 目标期刊/会议 | 主题 |
|--------------|------|
| Bioinformatics | 方法论文 |
| RECOMB/ISMB | 计算生物学会议 |
| NeurIPS/ICML | 机器学习会议（如理论贡献显著） |

### 6.2 时间线

| 阶段 | 时间 | 任务 |
|------|------|------|
| **阶段 1** | 第 1-2 周 | 文献综述、PhageScope 数据探索 |
| **阶段 2** | 第 3-4 周 | 数据预处理、金标准数据集构建 |
| **阶段 3** | 第 5-8 周 | 模块一 (Dual-VAE) 实现与测试 |
| **阶段 4** | 第 9-12 周 | 模块二 (FGW-OT) 实现与测试 |
| **阶段 5** | 第 13-16 周 | 模块三、四实现与迭代精化 |
| **阶段 6** | 第 17-20 周 | 完整实验、消融研究 |
| **阶段 7** | 第 21-24 周 | 论文撰写、代码完善 |

### 6.3 风险评估与缓解策略

| 风险 | 可能性 | 影响 | 缓解策略 |
|------|--------|------|---------|
| **宿主标注覆盖率低** | 高 | 高 | **1)** CRISPR spacer 匹配补充宿主信息：从宿主菌基因组中提取 CRISPR spacer 序列，与噬菌体基因组比对，建立独立的宿主关联证据。**2)** iPHoP 伪标签：使用 iPHoP 对未标注噬菌体预测宿主，筛选高置信度预测（score > 0.9）作为伪标签扩充训练集。**3)** 主动学习循环：模型对未标注样本的不确定性排序，优先标注不确定性最高的样本，以最少人工标注获得最大信息增益。**4)** 半监督学习：对无标注数据使用一致性正则化（consistency regularization），利用未标注数据的分布信息辅助学习。 |
| **FGW 计算复杂度高** | 中 | 高 | **1)** Mini-batch OT：将大规模 FGW 计算分解为 mini-batch（256 对/批），避免 O(N²) 全量计算。**2)** 熵正则化加速：增大 Sinkhorn 正则化参数 ε，以少量精度损失换取 5-10× 速度提升。**3)** POT 库优化：使用 Python Optimal Transport (POT) 库的 GPU 后端 (`ot.gpu`)，利用 CUDA 加速 Sinkhorn 迭代。**4)** 低秩近似：对大规模传输计划使用低秩分解 $\pi \approx UV^T$，将 O(nm) 存储和计算降至 O((n+m)r)。**5)** 渐进式精度：训练早期使用粗粒度 OT（少迭代、大 ε），后期切换到细粒度。 |
| **负样本质量差** | 中 | 中 | **1)** 多策略共识：同时使用 taxonomic_aware、hard_negative 和 random 三种策略生成负样本，仅保留被多策略一致认定为负样本的对。**2)** 置信度加权负采样：根据负样本的"可信度"分配权重——来自不同门的负样本权重高（可信），来自同属但不同种的权重低（可能是假阴性）。**3)** 迭代负样本精化：使用模型预测结果更新负样本集——将模型高置信度预测为正但标注为负的样本移出负样本集（潜在假阴性处理）。**4)** 正样本-未标注 (PU) 学习框架：承认负样本不完美，使用 PU learning 替代标准二分类。 |
| **ESM-2 嵌入计算量大** | 高 | 中 | **1)** 批量推理：使用 ESM-2 的 batch inference 模式，每批处理 32 条蛋白序列，充分利用 GPU 并行性。**2)** 混合精度推理：使用 FP16 (half precision) 进行 ESM-2 推理，显存减半且速度提升 ~40%，嵌入质量损失 < 0.1%。**3)** 模型蒸馏至 ESM-2 8M：使用最小版本 ESM-2（8M 参数，6 层），相比 650M 版本推理速度提升 ~80×，对蛋白家族级别嵌入质量影响有限。**4)** 增量嵌入缓存：对已计算的嵌入存入 HDF5 数据库，仅对新序列计算嵌入，避免重复计算。**5)** 嵌入质量验证：在子集上对比 8M vs 650M 嵌入的下游预测性能，若差距 >3% 则回退到 35M 版本。 |
| **迭代精化不收敛** | 低 | 中 | **1)** 置信度阈值监控：每轮迭代跟踪传输计划的平均置信度（top-10% π* 值的均值），若连续 2 轮变化 < 1% 则判定收敛。**2)** 早停策略 (Early Stopping)：设置最大迭代轮数 K_max=10，并在每轮后评估验证集性能，选择验证集最优轮次的参数。**3)** 留出验证集监控：使用独立的 5% 数据作为"迭代验证集"，仅用于监控收敛，不参与梯度更新，避免过拟合到训练集。**4)** 传输计划稳定性度量：计算相邻轮次传输计划的 Frobenius 范数差异 $\|\pi^{(k)} - \pi^{(k-1)}\|_F$，设定收敛阈值 ε_conv = 0.01。**5)** 发散检测与回退：若验证集性能连续 2 轮下降 >5%，回退到上一轮参数并终止迭代。 |

---

## 第七章：附录

### 附录 A：符号表

| 符号 | 含义 |
|------|------|
| $\mu, \nu$ | 概率分布 |
| $\pi$ | 传输计划 |
| $\pi^*$ | 最优传输计划 |
| $c(x,y)$ | 传输成本函数 |
| $FGW_\alpha$ | Fused Gromov-Wasserstein 距离 |
| $\alpha$ | 特征-结构权衡参数 |
| $\beta$ | KL 散度权重 |
| $d$ | 潜在空间维度 |

### 附录 B：代码仓库结构

```
phi-ot/
├── data/
│   ├── phagescope/           # PhageScope 数据
│   ├── gold_standard/        # 金标准数据集
│   └── processed/            # 处理后数据
├── src/
│   ├── models/
│   │   ├── dual_vae.py       # Dual-VAE 模型
│   │   ├── fgw_ot.py         # FGW-OT 匹配
│   │   └── phi_ot.py         # 完整框架
│   ├── training/
│   │   ├── trainer.py        # 训练循环
│   │   └── losses.py         # 损失函数
│   ├── evaluation/
│   │   ├── metrics.py        # 评估指标
│   │   └── visualization.py  # 可视化工具
│   └── utils/
│       ├── data_loader.py    # 数据加载
│       └── preprocessing.py  # 预处理
├── experiments/
│   ├── exp1_characterization/
│   ├── exp2_prediction/
│   ├── exp3_zero_shot/
│   └── exp4_interpretability/
├── notebooks/                # Jupyter notebooks
├── configs/                  # 配置文件
├── scripts/                  # 运行脚本
└── README.md
```

### 附录 C：依赖项

```
# 核心依赖
torch>=2.0.0
pot>=0.9.0                  # Python Optimal Transport
numpy>=1.24.0
scipy>=1.10.0
pandas>=2.0.0
scikit-learn>=1.3.0

# 生物信息学
biopython>=1.81
scanpy>=1.9.0               # 单细胞分析（潜在使用）

# 可视化
matplotlib>=3.7.0
seaborn>=0.12.0
plotly>=5.15.0

# 图与社区检测
igraph>=0.10.0
leidenalg>=0.10.0

# 蛋白质嵌入
esm>=2.0.0                  # Meta ESM-2

# 其他
tqdm>=4.65.0
pyyaml>=6.0
wandb>=0.15.0               # 实验跟踪
```

### 附录 D：配置文件示例

```yaml
# config/default.yaml
experiment:
  name: "phi_ot_default"
  seed: 42
  device: "cuda"

data:
  phagescope_dir: "./data/phagescope"
  gold_standard_dir: "./data/gold_standard"
  train_ratio: 0.7
  val_ratio: 0.15
  test_ratio: 0.15

model:
  dual_vae:
    input_dim: 512
    latent_dim: 64
    hidden_dims: [256, 128]
    beta: 1.0
  
  fgw_ot:
    alpha: 0.5
    epsilon: 0.05
    max_iter: 100
  
  iteration:
    n_rounds: 5
    convergence_threshold: 0.01

training:
  batch_size: 256
  learning_rate: 0.001
  epochs: 100
  early_stopping_patience: 10
  
evaluation:
  metrics: ["auc_roc", "auc_pr", "f1", "top_k_accuracy"]
  n_bootstrap: 1000
```

---

*文档版本: 2.0*
*最后更新: 2026-06-06*
*作者: PHI-OT 研究团队*
