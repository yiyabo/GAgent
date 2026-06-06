# 人体肠道噬菌体-细菌军备竞赛的分子生态学研究
# Molecular Ecology of Phage-Bacteria Arms Races in the Human Gut

---

## 项目概述

**研究主题**：以人体肠道为目标生态系统，系统解析噬菌体反适应策略的多样性、分布规律与进化动态

**核心叙事**：肠道作为高密度、高多样性的"分子军备竞赛微缩宇宙"，塑造了噬菌体独特的反适应武器库

**数据基础**：PhageScope 整合的 14 个数据库，重点关注肠道来源噬菌体（GPD、CHVD、IGVD 等数据库）

**方法学原则**：全部使用成熟的生物信息学工具（Conda/R 环境），不涉及新算法开发；充分利用 PhageScope 已有注释，避免重复计算

---

## 一、数据资源与预处理策略

### 1.1 PhageScope 已有数据资产（直接使用）

以下数据已由 PhageScope 平台完成注释，可直接用于下游分析：

| 数据类型 | PhageScope 模块 | 文件位置 | 关键字段 |
|---------|----------------|---------|---------|
| **基础元数据** | meta_data | `phagescope/meta_data/{database}_phage_meta_data.tsv` | Phage_ID, Host, Taxonomy, Lifestyle, Completeness, Source |
| **Anti-CRISPR 注释** | anticrispr_protein | `phagescope/anticrispr_protein/{database}_phage_anticrispr_protein_meta_data.tsv` | Phage_ID, Protein_ID, Acr_family |
| **CRISPR 阵列** | crispr_array | `phagescope/crispr_array/{database}_phage_crispr_array_meta_data.tsv` | Phage_ID, CRISPR_type, Spacer_count |
| **蛋白质注释** | annotated_protein | `phagescope/annotated_protein/{database}_phage_annotated_protein_meta_data.tsv` | Phage_ID, Protein_ID, Function_annotation |
| **tRNA/tmRNA** | trna_tmrna | `phagescope/trna_tmrna/{database}_phage_trna_tmrna_meta_data.tsv` | Phage_ID, tRNA_type |
| **毒力因子** | virulent_factor | `phagescope/virulent_factor/{database}_phage_virulent_factor_meta_data.tsv` | Phage_ID, VF_type |
| **耐药基因** | antimicrobial_resistance_gene | `phagescope/antimicrobial_resistance_gene/{database}_phage_ARG_meta_data.tsv` | Phage_ID, ARG_type |
| **跨膜蛋白** | transmembrane_protein | `phagescope/transmembrane_protein/{database}_phage_transmembrane_meta_data.tsv` | Phage_ID, TM_type |
| **基因组序列** | phage_fasta | `phagescope/phage_fasta/{database}_phage.fasta` | FASTA 格式 |
| **蛋白质序列** | protein_fasta | `phagescope/protein_fasta/{database}_phage_protein.fasta` | FASTA 格式 |
| **基因结构** | gff3 | `phagescope/gff3/{database}_phage.gff3` | GFF3 格式 |

### 1.2 肠道噬菌体数据筛选

**目标数据库**（按肠道相关性排序）：

| 数据库 | 噬菌体数量 | 来源特征 | 优先级 |
|--------|-----------|---------|--------|
| **GPD** (Gut Phage Database) | 142,809 | 人体肠道宏基因组组装 | ★★★ 核心 |
| **CHVD** (Children's Virome Database) | 44,935 | 儿童肠道病毒组 | ★★★ 核心 |
| **IGVD** (Israeli Gut Virome Database) | 10,021 | 以色列成人肠道 | ★★☆ 补充 |
| **GVD** (Gut Virome Database) | 31,402 | 多来源肠道病毒 | ★★☆ 补充 |
| **IMG_VR** | 177,361 | 宏基因组（含部分肠道） | ★☆☆ 可选 |
| **GOV2** | 195,699 | 海洋为主 | ☆☆☆ 对照 |
| **MGV** | 189,680 | 多环境 | ☆☆☆ 对照 |

**筛选策略**：

```bash
# Step 1: 提取肠道核心数据库噬菌体 ID 列表
cut -f1 phagescope/meta_data/gpd_phage_meta_data.tsv | tail -n +2 > gut_core_ids.txt
cut -f1 phagescope/meta_data/chvd_phage_meta_data.tsv | tail -n +2 >> gut_core_ids.txt
cut -f1 phagescope/meta_data/igvd_phage_meta_data.tsv | tail -n +2 >> gut_core_ids.txt
cut -f1 phagescope/meta_data/gvd_phage_meta_data.tsv | tail -n +2 >> gut_core_ids.txt

# Step 2: 去重并统计
sort -u gut_core_ids.txt > gut_phage_ids_unique.txt
wc -l gut_phage_ids_unique.txt  # 预期约 228,000 条

# Step 3: 提取高质量子集（Completeness = High-quality 或 Medium-quality）
# 从 curated_metadata.tsv 筛选
awk -F'\t' 'NR==1 || ($2=="High-quality" || $2=="Medium-quality")' \
    phagescope/curated_metadata.tsv | \
    grep -F -f gut_phage_ids_unique.txt > gut_phage_hq_metadata.tsv
```

**质量控制标准**：
- 保留 Completeness = "High-quality" 或 "Medium-quality" 的噬菌体
- 去除碎片化基因组（< 10 kb）
- 去除来源不明的序列

### 1.3 环境对照组构建

为检验"肠道特异性"假设，构建以下对照数据集：

| 对照组 | 数据库 | 代表环境 | 预期样本量 |
|--------|--------|---------|-----------|
| 海洋组 | GOV2 | 开阔海洋 | ~195K |
| 土壤/污泥组 | STV | 活性污泥 | ~4K |
| 培养组 | REFSEQ + GENBANK + PHAGESDB | 实验室培养 | ~10K |

---

## 二、分析流程与工具链

### 第一幕：肠道噬菌体武器库的全景扫描

**生物学问题**：肠道噬菌体携带哪些反适应基因？与海洋、土壤噬菌体相比有何特殊之处？

#### 2.1.1 Anti-CRISPR 基因的深度注释

**PhageScope 已有**：基于 Alignment 的 anti-CRISPR 蛋白注释（见 `anticrispr_protein/` 目录）

**补充注释**：使用 HMMER 进行更敏感的 profile 搜索

```bash
# 环境准备
conda create -n acrgenesis -c bioconda hmmer diamond blast mafft iqtree
conda activate acrgenesis

# Step 1: 准备 anti-CRISPR HMM profile 数据库
# 下载 AcrDB (https://bcb.unl.edu/AcrDB/)
wget https://bcb.unl.edu/AcrDB/download/AcrDB_HMMs.tar.gz
tar -xzf AcrDB_HMMs.tar.gz

# Step 2: 对肠道噬菌体蛋白质组进行 hmmsearch
hmmsearch --tblout gut_acr_hits.tbl \
          --domtblout gut_acr_dom.tbl \
          --cut_ga \
          --cpu 16 \
          AcrDB_HMMs/AcrDB.hmm \
          phagescope/protein_fasta/gpd_phage_protein.fasta

# Step 3: 解析结果，提取显著匹配 (E-value < 1e-5, 覆盖度 > 50%)
python3 parse_hmmsearch.py gut_acr_dom.tbl > gut_acr_annotations.tsv

# Step 4: 对 CHVD、IGVD、GVD 重复上述流程
for db in chvd igvd gvd; do
    hmmsearch --tblout ${db}_acr_hits.tbl \
              --domtblout ${db}_acr_dom.tbl \
              --cut_ga \
              --cpu 16 \
              AcrDB_HMMs/AcrDB.hmm \
              phagescope/protein_fasta/${db}_phage_protein.fasta
done

# Step 5: 合并所有肠道数据库的 anti-CRISPR 注释
cat gut_acr_annotations.tsv chvd_acr_annotations.tsv \
    igvd_acr_annotations.tsv gvd_acr_annotations.tsv > \
    gut_all_acr_annotations.tsv
```

**扩展反适应基因注释**：

除 anti-CRISPR 外，还需注释以下反适应系统：

| 反适应系统 | 靶标 | HMM Profile 来源 | 预期检出率 |
|-----------|------|-----------------|-----------|
| Anti-restriction | Type I-IV RM 系统 | Pfam + 文献收集 | 中 |
| Anti-CBASS | CBASS 信号通路 | 自建 HMM（基于文献序列） | 低 |
| Anti-retron | Retron 逆转录系统 | 自建 HMM | 低 |
| Anti-DISARM | DISARM 甲基化防御 | 自建 HMM | 低 |
| Anti-Gabija | Gabija 核酸酶 | 自建 HMM | 低 |

```bash
# 使用 DIAMOND 进行快速相似性搜索（作为 HMMER 的补充）
diamond makedb --in known_anti_defense_proteins.faa \
               --db anti_defense_db

diamond blastp --query gut_phage_proteins.faa \
               --db anti_defense_db \
               --out gut_anti_defense_diamond.tsv \
               --evalue 1e-10 \
               --max-target-seqs 1 \
               --outfmt 6 qseqid sseqid pident length evalue bitscore stitle \
               --threads 16
```

#### 2.1.2 细菌防御系统的注释（宿主端）

**目标**：对肠道细菌宿主的防御系统进行系统注释

**数据来源**：
- PhageScope 的 Host 预测结果（从 `curated_metadata.tsv` 提取）
- 对应细菌基因组的防御系统注释

**工具选择**：DefenseFinder (MacSyFinder 扩展)

```bash
# 环境准备
conda create -n defensefinder -c bioconda macsyfinder defense-finder-models
conda activate defensefinder

# Step 1: 提取肠道噬菌体的宿主预测结果
awk -F'\t' 'NR==1 || ($3 ~ /Bacteroides|Prevotella|Faecalibacterium|Bifidobacterium|Escherichia|Clostridium/)' \
    phagescope/curated_metadata.tsv > gut_phage_hosts.tsv

# Step 2: 下载代表性肠道细菌基因组（从 NCBI RefSeq）
# 例如：Bacteroides thetaiotaomicron, Bifidobacterium longum, E. coli 等
# 使用 entrez-direct 或直接下载

# Step 3: 对每个细菌基因组运行 DefenseFinder
defense-finder update  # 更新模型数据库

for genome in bacteria_genomes/*.fna; do
    defense-finder run $genome \
        --out-dir defense_results/$(basename $genome .fna) \
        --preserve-raw \
        --cpu 8
done

# Step 4: 汇总防御系统类型
python3 aggregate_defense_systems.py defense_results/ > \
    gut_bacteria_defense_inventory.tsv
```

#### 2.1.3 生态系统层面的武器库比较

**分析内容**：

1. **武器库丰富度**：每个噬菌体携带的反适应基因家族数量
2. **武器库多样性**：Shannon 指数、Simpson 指数
3. **武器库组成**：各类反适应基因的相对丰度

**统计方法**（R 语言）：

```r
# 加载必要的 R 包
library(tidyverse)
library(vegan)
library(patchwork)

# 读取反适应基因注释数据
acr_data <- read_tsv("gut_all_acr_annotations.tsv")

# 计算每个噬菌体的武器库丰富度
arsenal_richness <- acr_data %>%
  group_by(Phage_ID) %>%
  summarise(
    richness = n_distinct(Acr_family),
    total_acr = n()
  )

# 计算生态系统层面的多样性指数
ecosystem_diversity <- acr_data %>%
  group_by(Ecosystem) %>%  # Gut, Ocean, Soil 等
  summarise(
    shannon = diversity(table(Acr_family), index = "shannon"),
    simpson = diversity(table(Acr_family), index = "simpson"),
    richness = n_distinct(Acr_family)
  )

# 可视化：生态系统间的武器库多样性比较
p1 <- ggplot(ecosystem_diversity, aes(x = Ecosystem, y = shannon)) +
  geom_boxplot(fill = "steelblue") +
  geom_jitter(width = 0.2, alpha = 0.3) +
  labs(x = "Ecosystem", y = "Shannon Diversity Index",
       title = "Anti-CRISPR Diversity Across Ecosystems") +
  theme_minimal()

ggsave("figures/ecosystem_acr_diversity.png", p1, width = 8, height = 6, dpi = 300)
```

**假设检验**：

```r
# 假设 1：肠道噬菌体的反适应基因多样性高于海洋噬菌体
gut_acr <- acr_data %>% filter(Ecosystem == "Gut") %>% pull(Acr_family) %>% table()
ocean_acr <- acr_data %>% filter(Ecosystem == "Ocean") %>% pull(Acr_family) %>% table()

# 使用 Wilcoxon 秩和检验
wilcox.test(gut_acr, ocean_acr, alternative = "greater")

# 假设 2：不同生态系统的武器库组成存在显著差异
# 使用 PERMANOVA (vegan::adonis2)
acr_matrix <- acr_data %>%
  pivot_wider(names_from = Acr_family, values_from = n, values_fill = 0) %>%
  column_to_rownames("Phage_ID") %>%
  as.matrix()

metadata <- acr_data %>% distinct(Phage_ID, Ecosystem)
adonis2(acr_matrix ~ Ecosystem, data = metadata, permutations = 9999)
```

---

### 第二幕：肠道噬菌体-宿主的"匹配法则"检验

**生物学问题**：肠道噬菌体的反适应武器是否与其宿主的防御系统存在非随机关联？

#### 2.2.1 宿主-噬菌体配对矩阵构建

```bash
# 从 PhageScope 提取宿主预测信息
awk -F'\t' 'NR==1 {for(i=1;i<=NF;i++) if($i=="Host") host_col=i}
            NR>1 && $host_col!="" {print $1"\t"$host_col}' \
    phagescope/curated_metadata.tsv > phage_host_pairs.tsv

# 过滤：仅保留高置信度宿主预测
# （例如，基于 CRISPR spacer 匹配或培养来源的噬菌体）
awk -F'\t' '$3 ~ /High_confidence|RefSeq|GenBank/' \
    phage_host_pairs.tsv > phage_host_hq.tsv
```

#### 2.2.2 "匹配度"量化分析

**方法**：构建 2×2 列联表，检验噬菌体 anti-defense 基因与宿主防御系统的共现模式

```r
# 构建噬菌体-宿主防御系统关联矩阵
phage_defense_matrix <- read_tsv("phage_defense_matrix.tsv")
# 列：Phage_ID, Acr_family, Host_genus, Host_defense_system

# 对每个 (Acr_family, Host_defense_system) 配对，计算共现频率
cooccurrence <- phage_defense_matrix %>%
  filter(!is.na(Acr_family) & !is.na(Host_defense_system)) %>%
  count(Acr_family, Host_defense_system) %>%
  pivot_wider(names_from = Host_defense_system, 
              values_from = n, values_fill = 0)

# 对每个配对进行卡方检验（或 Fisher 精确检验）
# 构建 2×2 列联表：
#                  Host has defense X    Host lacks defense X
# Phage has Acr Y         a                    b
# Phage lacks Acr Y       c                    d

chi_square_tests <- list()
for (acr in unique(cooccurrence$Acr_family)) {
  for (defense in colnames(cooccurrence)[-1]) {
    # 提取 2×2 表
    a <- cooccurrence %>% filter(Acr_family == acr) %>% pull(defense) %>% sum()
    b <- sum(cooccurrence$Acr_family == acr) - a
    c <- sum(cooccurrence[[defense]]) - a
    d <- nrow(cooccurrence) - a - b - c
    
    matrix_2x2 <- matrix(c(a, b, c, d), nrow = 2, byrow = TRUE)
    
    # Fisher 精确检验（适用于小样本）
    test_result <- fisher.test(matrix_2x2, alternative = "greater")
    
    chi_square_tests[[paste(acr, defense, sep = "_")]] <- 
      tibble(
        Acr_family = acr,
        Host_defense = defense,
        odds_ratio = test_result$estimate,
        p_value = test_result$p.value,
        a = a, b = b, c = c, d = d
      )
  }
}

# 合并结果并进行多重比较校正
matching_results <- bind_rows(chi_square_tests) %>%
  mutate(
    p_adjusted = p.adjust(p_value, method = "BH"),
    significant = p_adjusted < 0.05
  )

# 输出显著的"匹配"配对
matching_results %>%
  filter(significant) %>%
  arrange(desc(odds_ratio)) %>%
  write_tsv("significant_acr_defense_matches.tsv")
```

#### 2.2.3 "宽松匹配" vs "精确匹配" 检验

**假设**：噬菌体倾向于携带广谱反适应武器（"瑞士军刀"策略），而非精确匹配单一宿主防御

**分析方法**：

```r
# 计算每个噬菌体的"宿主范围广度"和"武器库广度"
phage_arsenal_summary <- phage_defense_matrix %>%
  group_by(Phage_ID) %>%
  summarise(
    host_range = n_distinct(Host_genus),  # 感染的宿主属数量
    acr_diversity = n_distinct(Acr_family),  # 携带的 anti-CRISPR 家族数
    defense_coverage = n_distinct(Host_defense_system)  # 覆盖的防御系统类型数
  )

# 检验：武器库广度与宿主范围广度的相关性
cor_test <- cor.test(
  phage_arsenal_summary$host_range,
  phage_arsenal_summary$acr_diversity,
  method = "spearman"
)

cat("Spearman's rho =", cor_test$estimate, "\n")
cat("p-value =", cor_test$p.value, "\n")

# 可视化
ggplot(phage_arsenal_summary, aes(x = host_range, y = acr_diversity)) +
  geom_point(alpha = 0.5, color = "steelblue") +
  geom_smooth(method = "lm", color = "red", se = TRUE) +
  labs(
    x = "Host Range Breadth (# of host genera)",
    y = "Anti-CRISPR Arsenal Diversity (# of Acr families)",
    title = "Phage Arsenal Complexity Correlates with Host Range"
  ) +
  theme_minimal()

ggsave("figures/host_range_vs_arsenal_diversity.png", 
       width = 8, height = 6, dpi = 300)
```

---

### 第三幕：溶原性噬菌体的"特洛伊木马"策略

**生物学问题**：肠道中温和噬菌体（前噬菌体）是否通过整合到宿主基因组，实现对宿主防御系统的"内部瓦解"？

#### 2.3.1 生活方式与反适应武器的关联分析

**PhageScope 已有**：Lifestyle 预测（virulent vs temperate）

```r
# 读取生活方式注释
lifestyle_data <- read_tsv("phagescope/curated_metadata.tsv") %>%
  select(Phage_ID, Lifestyle, Host)

# 合并反适应基因数据
acr_lifestyle <- acr_data %>%
  left_join(lifestyle_data, by = "Phage_ID")

# 比较 virulent vs temperate 噬菌体的武器库差异
arsenal_by_lifestyle <- acr_lifestyle %>%
  group_by(Phage_ID, Lifestyle) %>%
  summarise(
    acr_count = n(),
    acr_diversity = n_distinct(Acr_family)
  ) %>%
  group_by(Lifestyle) %>%
  summarise(
    mean_acr_count = mean(acr_count, na.rm = TRUE),
    mean_acr_diversity = mean(acr_diversity, na.rm = TRUE),
    n_phages = n()
  )

# 统计检验：Mann-Whitney U 检验
wilcox.test(
  acr_count ~ Lifestyle,
  data = acr_lifestyle,
  alternative = "two.sided"
)
```

#### 2.3.2 前噬菌体的基因组邻域分析

**假设**：整合到宿主基因组的前噬菌体，其反适应基因应与宿主防御系统基因在基因组上邻近

**工具**：自定义 Python 脚本 + GFF3 文件解析

```python
#!/usr/bin/env python3
"""
analyze_prophage_neighborhoods.py
分析前噬菌体整合位点附近的宿主防御系统基因
"""

import sys
from Bio import SeqIO
from BCBio import GFF
from collections import defaultdict

def parse_gff3(gff_file):
    """解析 GFF3 文件，提取基因位置信息"""
    genes = []
    with open(gff_file) as handle:
        for rec in GFF.parse(handle):
            for feature in rec.features:
                if feature.type in ['gene', 'CDS']:
                    genes.append({
                        'id': feature.id,
                        'start': int(feature.location.start),
                        'end': int(feature.location.end),
                        'strand': feature.location.strand,
                        'product': feature.qualifiers.get('product', [''])[0],
                        'phage_id': rec.id
                    })
    return genes

def find_defense_neighbors(genes, window=10000):
    """
    对每个 anti-CRISPR 基因，查找 window bp 内的防御系统基因
    """
    defense_keywords = [
        'CRISPR', 'Cas', 'restriction', 'methyltransferase',
        'CBASS', 'retron', 'DISARM', 'Gabija'
    ]
    
    acr_genes = [g for g in genes if 'anti-CRISPR' in g['product'].lower() 
                 or 'Acr' in g['product']]
    defense_genes = [g for g in genes if any(kw in g['product'] for kw in defense_keywords)]
    
    neighbors = []
    for acr in acr_genes:
        for defense in defense_genes:
            if acr['phage_id'] == defense['phage_id']:
                distance = min(
                    abs(acr['start'] - defense['end']),
                    abs(acr['end'] - defense['start'])
                )
                if distance <= window:
                    neighbors.append({
                        'acr_id': acr['id'],
                        'defense_id': defense['id'],
                        'defense_product': defense['product'],
                        'distance_bp': distance
                    })
    
    return neighbors

if __name__ == '__main__':
    gff_file = sys.argv[1]
    genes = parse_gff3(gff_file)
    neighbors = find_defense_neighbors(genes)
    
    # 输出结果
    for n in neighbors:
        print(f"{n['acr_id']}\t{n['defense_id']}\t{n['defense_product']}\t{n['distance_bp']}")
```

```bash
# 对所有肠道噬菌体 GFF3 文件批量运行
for gff in phagescope/gff3/gpd_*.gff3; do
    python3 analyze_prophage_neighborhoods.py $gff >> prophage_neighborhoods.tsv
done

# 统计：多少 anti-CRISPR 基因与防御系统基因邻近？
awk '{count++} END {print "Total neighborhoods:", count}' \
    prophage_neighborhoods.tsv
```

---

### 第四幕：军备竞赛的进化考古学

**生物学问题**：肠道噬菌体的反适应武器库是如何在进化过程中逐步积累的？

#### 2.4.1 噬菌体系统发育树构建

**方法**：基于保守蛋白（大亚基末端酶 TerL、主要衣壳蛋白 MCP）构建系统发育框架

```bash
# Step 1: 提取保守蛋白序列
# 从 annotated_protein 注释中识别 TerL 和 MCP
grep -i "terminase large subunit\|TerL" \
    phagescope/annotated_protein/gpd_phage_annotated_protein_meta_data.tsv | \
    cut -f2 > terl_protein_ids.txt

grep -i "major capsid protein\|MCP" \
    phagescope/annotated_protein/gpd_phage_annotated_protein_meta_data.tsv | \
    cut -f2 > mcp_protein_ids.txt

# 从 protein_fasta 提取对应序列
seqkit grep -f terl_protein_ids.txt \
    phagescope/protein_fasta/gpd_phage_protein.fasta > gut_terl.faa

seqkit grep -f mcp_protein_ids.txt \
    phagescope/protein_fasta/gpd_phage_protein.fasta > gut_mcp.faa

# Step 2: 多序列比对
mafft --auto --thread 16 gut_terl.faa > gut_terl_aligned.faa
mafft --auto --thread 16 gut_mcp.faa > gut_mcp_aligned.faa

# Step 3: 去除低质量比对区域
trimal -in gut_terl_aligned.faa -out gut_terl_trimmed.faa -automated1
trimal -in gut_mcp_aligned.faa -out gut_mcp_trimmed.faa -automated1

# Step 4: 构建最大似然树
iqtree2 -s gut_terl_trimmed.faa \
        -m MFP \
        -bb 1000 \
        -alrt 1000 \
        -nt AUTO \
        -pre gut_terl_tree

iqtree2 -s gut_mcp_trimmed.faa \
        -m MFP \
        -bb 1000 \
        -alrt 1000 \
        -nt AUTO \
        -pre gut_mcp_tree

# Step 5: 合并 TerL + MCP 构建树（可选，提高分辨率）
# 使用 ASTRAL 或其他物种树推断方法
```

#### 2.4.2 Anti-CRISPR 基因的祖先状态重建

**目标**：推断每个 anti-CRISPR 家族在噬菌体系统发育树上的起源节点

**工具**：R 包 `phytools` 或 Python 包 `PastML`

```r
# R 代码：使用 phytools 进行祖先状态重建
library(ape)
library(phytools)

# 读取系统发育树
tree <- read.tree("gut_terl_tree.treefile")

# 读取 anti-CRISPR 存在/缺失数据
acr_presence <- read_tsv("acr_presence_matrix.tsv")
# 列：Phage_ID, Acr_family_1 (0/1), Acr_family_2 (0/1), ...

# 对每个 anti-CRISPR 家族进行祖先状态重建
for (acr_col in colnames(acr_presence)[-1]) {
  # 提取该家族的存在/缺失向量
  states <- setNames(acr_presence[[acr_col]], acr_presence$Phage_ID)
  
  # 确保状态向量与树的叶节点匹配
  tree_tips <- tree$tip.label
  states_aligned <- states[tree_tips]
  states_aligned[is.na(states_aligned)] <- 0  # 缺失数据视为 0
  
  # 使用最大似然法进行祖先状态重建
  fit <- ace(states_aligned, tree, type = "discrete", model = "ARD")
  
  # 可视化
  pdf(paste0("ancestral_reconstruction_", acr_col, ".pdf"), width = 10, height = 12)
  plotTree(tree, fsize = 0.5)
  tiplabels(pie = to.matrix(states_aligned, c(0, 1)), 
            piecol = c("white", "steelblue"), cex = 0.5)
  nodelabels(pie = fit$lik.anc, piecol = c("white", "steelblue"), cex = 0.3)
  title(main = paste("Ancestral State Reconstruction:", acr_col))
  dev.off()
  
  # 推断起源节点（所有后代均携带该 anti-CRISPR 的最深节点）
  origin_node <- find_origin_node(fit, tree)
  cat("Inferred origin node for", acr_col, ":", origin_node, "\n")
}
```

**Python 替代方案（PastML）**：

```bash
# 安装 PastML
pip install pastml

# 运行祖先状态重建
pastml --tree gut_terl_tree.treefile \
       --data acr_presence_matrix.tsv \
       --columns Acr_family_1 Acr_family_2 Acr_family_3 \
       --prediction_method mpp \
       --model F81 \
       --out_dir pastml_results/

# 输出包括：每个节点的推断状态、状态转换概率等
```

#### 2.4.3 "创新浪潮"检测

**假设**：anti-CRISPR 基因的多样化不是均匀的，而是存在"爆发期"

**方法**：在系统发育树上识别 anti-CRISPR 基因的快速多样化事件

```r
# 使用 BAMM (Bayesian Analysis of Macroevolutionary Mixtures)
# 检测反适应基因获得速率的变化

library(BAMMtools)

# 读取 BAMM 事件数据（需要先用 BAMM 软件运行 MCMC）
event_data <- getEventData(tree, "bamm_events.txt", type = "macro")

# 可视化速率变化
plot(event_data, lwd = 2)

# 识别 anti-CRISPR 获得速率显著增加的分支
rate_shifts <- getRateShifts(event_data)
print(rate_shifts)
```

---

## 三、整合分析与可视化

### 3.1 四幕叙事整合

**目标**：将四幕分析结果整合为一个连贯的生物学故事

**整合框架**：

```
第一幕（武器库全景）→ 第二幕（匹配法则）→ 第三幕（溶原策略）→ 第四幕（进化历史）
     ↓                      ↓                      ↓                      ↓
  "有什么？"          "如何匹配？"          "如何利用？"          "何时获得？"
```

### 3.2 出版级图表生成

**工具**：R (ggplot2 + patchwork) 或 Python (matplotlib + seaborn)

```r
# 示例：多面板整合图
library(patchwork)

# Panel A: 生态系统间的武器库多样性
p_a <- ggplot(ecosystem_data, aes(x = Ecosystem, y = Shannon)) +
  geom_boxplot(fill = "lightblue") +
  labs(title = "A. Arsenal Diversity by Ecosystem") +
  theme_minimal()

# Panel B: 宿主范围 vs 武器库复杂度
p_b <- ggplot(phage_summary, aes(x = host_range, y = acr_diversity, color = Lifestyle)) +
  geom_point(alpha = 0.6) +
  geom_smooth(method = "lm") +
  labs(title = "B. Host Range vs Arsenal Complexity") +
  theme_minimal()

# Panel C: 前噬菌体的基因组邻域
p_c <- ggplot(neighborhood_data, aes(x = distance_bp, fill = defense_type)) +
  geom_histogram(bins = 50) +
  labs(title = "C. Prophage Defense Gene Neighborhoods") +
  theme_minimal()

# Panel D: 系统发育树上的反适应基因分布
# （使用 ggtree 包）
library(ggtree)
p_d <- ggtree(tree) + 
  geom_tippoint(aes(color = Acr_presence)) +
  labs(title = "D. Anti-CRISPR Distribution on Phylogeny") +
  theme_tree2()

# 组合四面板图
(p_a | p_b) / (p_c | p_d) +
  plot_annotation(
    title = "Gut Phage Arms Race: A Four-Act Story",
    theme = theme(plot.title = element_text(size = 16, face = "bold"))
  )

ggsave("figures/four_act_integration.png", width = 12, height = 10, dpi = 300)
```

---

## 四、假设检验汇总

| 幕次 | 假设编号 | 假设内容 | 检验方法 | 预期结果 |
|------|---------|---------|---------|---------|
| 第一幕 | H1 | 肠道噬菌体的反适应基因多样性高于海洋噬菌体 | Wilcoxon 秩和检验 | 肠道 > 海洋 |
| 第一幕 | H2 | 不同生态系统的武器库组成存在显著差异 | PERMANOVA | p < 0.05 |
| 第二幕 | H3 | 噬菌体 anti-CRISPR 与宿主防御系统存在非随机关联 | Fisher 精确检验 + BH 校正 | OR > 1, p < 0.05 |
| 第二幕 | H4 | 武器库广度与宿主范围广度正相关 | Spearman 相关 | rho > 0, p < 0.05 |
| 第三幕 | H5 | 温和噬菌体比毒力噬菌体携带更多反适应基因 | Mann-Whitney U 检验 | temperate > virulent |
| 第三幕 | H6 | 前噬菌体的反适应基因与宿主防御基因邻近 | 基因组邻域分析 | 距离 < 10 kb |
| 第四幕 | H7 | anti-CRISPR 基因在系统发育树上非随机分布 | Pagel's lambda | lambda > 0.5 |
| 第四幕 | H8 | 存在 anti-CRISPR 获得速率的"爆发期" | BAMM 速率变化检测 | 速率显著增加 |

---

## 五、计算资源与环境配置

### 5.1 Conda 环境配置

```bash
# 主分析环境
conda create -n gut_phage_analysis \
    -c bioconda -c conda-forge \
    python=3.9 \
    hmmer=3.3.2 \
    diamond=2.0.15 \
    blast=2.13.0 \
    mafft=7.505 \
    iqtree=2.2.0.3 \
    trimal=1.4.1 \
    seqkit=2.3.1 \
    r-base=4.2.0 \
    r-tidyverse \
    r-vegan \
    r-patchwork \
    r-ggtree \
    r-phytools \
    biopython=1.79 \
    pandas=1.5.0 \
    numpy=1.23.0 \
    matplotlib=3.6.0 \
    seaborn=0.12.0

conda activate gut_phage_analysis

# DefenseFinder 环境（单独安装）
conda create -n defensefinder -c bioconda macsyfinder defense-finder-models
conda activate defensefinder

# PastML 环境（Python）
pip install pastml
```

### 5.2 计算资源需求

| 分析步骤 | CPU | 内存 | 存储 | 预计时间 |
|---------|-----|------|------|---------|
| HMMER 搜索 (anti-CRISPR) | 16 cores | 32 GB | 50 GB | 4-6 小时 |
| DIAMOND 比对 | 16 cores | 16 GB | 10 GB | 1-2 小时 |
| DefenseFinder (细菌基因组) | 8 cores × N | 16 GB | 5 GB × N | 2-4 小时 × N |
| MAFFT 比对 (TerL + MCP) | 16 cores | 64 GB | 20 GB | 2-4 小时 |
| IQ-TREE 树构建 | 32 cores | 128 GB | 10 GB | 4-8 小时 |
| R 统计分析 | 4 cores | 16 GB | 5 GB | 1-2 小时 |
| 图表生成 | 2 cores | 8 GB | 2 GB | 30 分钟 |

**总计**：约 200-400 CPU-hours，150-200 GB 存储

---

## 六、预期成果与科学意义

### 6.1 核心发现

1. **肠道噬菌体武器库图谱**：首次系统描绘人体肠道噬菌体的反适应基因全景
2. **匹配法则验证**：量化噬菌体反适应策略的"精确匹配" vs "广谱覆盖"程度
3. **溶原性优势假说**：验证温和噬菌体是否通过基因组整合实现"内部瓦解"
4. **进化时间线**：追溯关键反适应武器在肠道噬菌体谱系中的起源与多样化

### 6.2 科学意义

- **噬菌体疗法**：为设计针对肠道病原菌的噬菌体鸡尾酒提供进化生态学指导
- **微生物组工程**：揭示噬菌体如何塑造肠道菌群结构，为定向调控提供理论依据
- **基础进化生物学**：为"红皇后假说"提供迄今最大规模的肠道生态系统分子证据

### 6.3 目标期刊

- **首选**：*Nature Microbiology* 或 *ISME Journal*
- **备选**：*Cell Host & Microbe* 或 *Nature Ecology & Evolution*

---

## 七、风险与局限性

### 7.1 数据局限性

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 宿主预测准确性 | 假阳性/假阴性配对 | 分层分析（高置信度 vs 全部） |
| Anti-CRISPR 注释不完整 | 遗漏新家族 | HMMER + DIAMOND 双重验证 |
| 采样偏倚 | 肠道样本过度代表某些人群 | 稀有化处理 + 敏感性分析 |

### 7.2 方法学局限性

- **相关性 ≠ 因果性**：所有关联分析均为观察性，需实验验证
- **计算预测 vs 实验验证**：PhageScope 的宿主预测主要基于计算方法
- **静态快照 vs 动态过程**：横断面数据无法捕捉军备竞赛的时间动态

---

## 八、时间线与里程碑

| 阶段 | 时间 | 任务 | 交付物 |
|------|------|------|--------|
| **准备** | 第 1-2 周 | 数据筛选、环境配置 | 肠道噬菌体高质量子集 |
| **第一幕** | 第 3-4 周 | 武器库全景扫描 | anti-CRISPR 注释表、多样性分析 |
| **第二幕** | 第 5-6 周 | 匹配法则检验 | 宿主-噬菌体关联矩阵、显著配对 |
| **第三幕** | 第 7-8 周 | 溶原策略分析 | 生活方式关联、基因组邻域 |
| **第四幕** | 第 9-10 周 | 进化考古学 | 系统发育树、祖先状态重建 |
| **整合** | 第 11-12 周 | 结果整合、图表制作 | 四面板整合图、统计汇总 |
| **写作** | 第 13-16 周 | 论文撰写、修订 | 论文初稿、补充材料 |

**里程碑 M1**（第 4 周末）：肠道噬菌体武器库数据库完成
**里程碑 M2**（第 10 周末）：四幕分析全部完成
**里程碑 M3**（第 16 周末）：论文投稿

---

*项目完成日期：2026年6月*  
*数据基础：PhageScope 873,718 条噬菌体基因组（肠道子集约 228K）*  
*目标期刊：Nature Microbiology / ISME Journal*
