# Analysis Memo

- mode: local_draft_assembly
- task: Generate the definitive PHI-OT Research Plan document (v3). This is a RESEARCH METHODOLOGY DOCUMENT, not a manuscript for publication. Write it in Chinese with English technical terms.

The document must merge the best content from both v1 and v2, fixing all identified issues.

## DOCUMENT STRUCTURE AND CONTENT REQUIREMENTS

### 目录
Full table of contents with all sections and subsections.

### 第一章：研究背景与目标
Include from v1:
- 1.1 核心科学问题 (v1's three bottlenecks: prediction accuracy, evolutionary bias, interpretability)
- 1.2 2020-2025年领域进展回顾 (v1's full 10-row literature table with DeepHost, iPHoP, Zhong 2023, Borin 2023, Gaborieau 2024, Pyenson 2024, PHIStruct, GE-PHI, MoEPH, PhageCGRNet)
- 1.3 现有方法的三大局限 (v1's detailed explanations of vector space assumption, evolutionary bias, static prediction)
- 1.4 本研究的创新贡献 (v1's four contributions)

### 第二章：PhageScope 数据使用定义
Merge v1's detail with v2's corrections:
- 2.1 数据源概览 (v1's full 13-module table with data sizes and PHI relevance)
- 2.2 数据筛选标准
  - 2.2.1 完整性阈值 (High-quality + Medium-quality only)
  - 2.2.2 宿主标注过滤 (use Host field, normalize to genus)
  - 2.2.3 分类学范围 (expected families and host genera lists)
  - 2.2.4 序列长度过滤 (5kb-500kb for genome, protein_count 5-1000)
    - **Include the protein_count fallback code** (compute from annotated_protein if missing)
  - 2.2.5 最终数据集规模估算 (**CORRECTED**: Use verified 11.8% Host_label rate, final ~63,000 not ~94,000)
- 2.3 三层特征工程体系 (FROM v1 - this is CRITICAL content):
  - 2.3.1 第一层：宏观基因组层 (~50-100 dim from curated_metadata)
  - 2.3.2 第二层：蛋白功能层 (~31 dim from annotated_protein: function counts + physicochemical stats)
  - 2.3.3 第三层：互作信号层 (~9 dim from anticrispr/transmembrane/crispr)
  - 2.3.4 三层特征汇总表
- 2.4 宿主侧特征定义 (FROM v1):
  - 2.4.1 宿主元数据特征 table
  - 2.4.2 系统发育距离矩阵 (taxonomy-based and ANI-based methods)
  - 2.4.3 宿主受体蛋白特征
- 2.5 数据预处理流程 (FROM v1):
  - 2.5.1 缺失值处理策略 table
  - 2.5.2 类别特征编码 (low/medium/high cardinality)
  - 2.5.3 数值特征标准化 (RobustScaler rationale)
  - 2.5.4 特征选择方法 (3-stage: variance → mutual information → LASSO)

### 第三章：算法理论与模型架构
- 3.1 最优传输理论基础 (FROM v1: Monge, Kantorovich, Wasserstein, Sinkhorn, Sliced-Wasserstein, GW, FGW - all with mathematical formulas)
- 3.2 PHI-OT 模型架构（三大模块）
  - 模块1：双变分自编码器 (v1's detailed architecture diagram with cross-domain splicing, loss function with beta and lambda)
  - 模块2：FGW-OT 匹配引擎 (v1's 4-step process + **v2's clarification about ESM-2 vs PhageScope precomputed features**: Dual-VAE uses precomputed physicochemical features; FGW-OT uses ESM-2 protein embeddings as point clouds)
  - 模块3：迭代改进循环 (v1's convergence criteria and iteration strategy)
- 3.3 多任务预测头 (v2's interaction probability, host classification, uncertainty quantification)
- **REMOVE** the old 3.3 "OT与替代方案的对比" table → moved to 5.2.0

### 第四章：金标准数据集构建
Merge v1's rigor with v2's structure:
- 4.1 正样本构建 (v1's three-level verification: Level 1 Host+Quality, Level 2+CRISPR, Level 3+literature)
- 4.2 负样本构建 (v1's three strategies A/B/C with code + positive-negative ratio experiment table with 6 configurations)
- 4.3 数据划分策略 (v1's four schemes: random, phylogenetic-aware via GroupKFold, temporal-aware, host-aware zero-shot + summary table)

### 第五章：实验设计 (ALL EXPANDED as requested)
- 5.1 评估指标体系 (FROM v1):
  - 5.1.1 属级预测指标 (Accuracy, Precision, Recall, F1-macro, AUROC, AUPRC)
  - 5.1.2 菌株级预测指标 (Top-1/3/5 Accuracy, MRR)
  - 5.1.3 校准指标 (ECE, Brier Score)
  - 5.1.4 生物学相关性指标 (network Jaccard, host range coverage, RBP-receptor match)

- 5.2 基准工具对比实验:
  - **5.2.0 OT相对于替代方案的理论优势** (MOVED from old 3.3, with bridging paragraph that maps theoretical advantages to specific experimental predictions)
  - 5.2.1 基准工具列表 (v1's 7-tool table: iPHoP, PHIStruct, GE-PHI, MoEPH, PhageCGRNet, DeepHost, PHI-OT)
  - 5.2.2 对比实验结果表格模板 (v1's full template with 7 columns)
  - 5.2.3 分层评估（按宿主属）(v1's 8-genus table)

- 5.3 消融实验设计 (FROM v1):
  - 5.3.1 实验列表 (11 ablation experiments A1-A11)
  - 5.3.2 消融实验结果表格模板
  - 5.3.3 预期结果与解释 table

- 5.4 超参数敏感性实验 (EXPANDED):
  - 5.4.1 FGW 平衡参数 alpha (v1's table)
  - 5.4.2 Sinkhorn 熵正则化 epsilon (v1's table)
  - 5.4.3 VAE 潜在空间维度 (v1's table)
  - 5.4.4 正负样本比例 (v1's table)
  - 5.4.5 蛋白嵌入模型选择 (v1's table: ESM-2 8M/35M/150M/650M, ProtBERT)
  - **5.4.6 实验协议（网格搜索 + 贝叶斯优化）** - NEW: coarse grid search protocol, Bayesian optimization with GP surrogate, EI acquisition function, 100 evaluations budget
  - **5.4.7 交互效应分析（α×ε、α×latent_dim）** - NEW: 5×5 alpha×epsilon heatmap, 5×7 alpha×latent_dim, two-way ANOVA, interaction effect plots
  - **5.4.8 最终超参数选择表与置信区间** - NEW: bootstrap CI estimation, 5-fold CV parameter collection, final confirmation protocol

- 5.5 泛化性实验 (EXPANDED):
  - 5.5.1 跨家族泛化 (v1's train/test family split)
  - 5.5.2 跨宿主泛化
  - 5.5.3 零样本宿主预测 (held-out genera)
  - 5.5.4 数据稀缺场景 (v1's 100%/50%/25%/10%/5% table)
  - **5.5.5 统计显著性检验协议** - NEW: McNemar's test code, paired t-test + Bonferroni correction code, 20 comparisons → α_adj=0.0025, Cohen's d effect sizes, Holm-Bonferroni
  - **5.5.6 零样本场景的域适应策略** - NEW: domain adversarial training (GRL), OT-based domain adaptation (reweighting), pseudo-label self-training
  - **5.5.7 各泛化测试的详细实验协议** - NEW: 4 test configurations (A: PhageScope→PHIST, B: →PHIDB, C: →GPD, D: PHIST+PHIDB→PhageScope) with detailed tables

- 5.6 可解释性分析 (EXPANDED):
  - 5.6.1 OT传输计划可视化 (v1's heatmap code)
  - 5.6.2 关键蛋白识别 (v1's get_key_interactions code)
  - 5.6.3 与已知RBP-receptor对的验证 (v1's known pairs: T4-LamB, lambda-J, T7-gp17, P22-tailspike)
  - **5.6.4 案例研究（3个噬菌体-宿主对的详细OT计划分析）** - NEW: selection criteria, analysis template (A. visualization, B. high-transport analysis, C. family-level aggregation, D. ablation comparison)
  - **5.6.5 可解释性的统计验证（置换检验）** - NEW: permutation test code (1000 permutations, entropy statistic, p-value), Bonferroni correction, transport concentration and modularity scores
  - **5.6.6 与基于注意力的可解释性方法对比** - NEW: 4 methods comparison (FGW-OT vs Self-Attention vs GradCAM vs SHAP), 4 evaluation metrics (functional consistency, stability, faithfulness, biological validation), Wilcoxon test

- 5.7 计算效率分析 (EXPANDED):
  - 5.7.1 时间复杂度分析 (v1's component table)
  - 5.7.2 与基准工具的效率对比 (v1's 6-tool comparison table)
  - 5.7.3 Sinkhorn收敛分析 (v1's convergence code)
  - **5.7.4 可扩展性分析** - NEW: XS/S/M/L/XL scale table, learning curve fitting (power law), computational cost scaling, bottleneck identification and solutions
  - **5.7.5 GPU显存优化策略** - NEW: memory consumption analysis table, mixed precision training code, gradient checkpointing, sparse transport plan, ESM-2 batch precomputation, hardware requirements table (RTX 3090/A100 40GB/A100 80GB)
  - **5.7.6 在线/增量学习能力** - NEW: incremental learning protocol, EWC regularizer code, evaluation metrics (forward/backward transfer, forgetting rate)

### 第六章：预期成果与时间线
- 6.1 预期成果 (algorithm, data, publications)
- 6.2 时间线 (v1's 24-week Gantt-style table)
- **6.3 风险评估与缓解策略** (EXPANDED with detailed mitigations):
  - Risk 1: 宿主标注覆盖率低 (HIGH prob, HIGH impact) → CRISPR spacer matching, iPHoP pseudo-labels, active learning, semi-supervised consistency regularization
  - Risk 2: FGW计算复杂度高 (MEDIUM, HIGH) → Mini-batch OT, entropic regularization acceleration, POT GPU backend, low-rank approximation, progressive precision
  - Risk 3: 负样本质量差 (MEDIUM, MEDIUM) → Multi-strategy consensus, confidence-weighted negative sampling, iterative refinement, PU learning framework
  - Risk 4: ESM-2嵌入计算量大 (HIGH, MEDIUM) → Batch inference, FP16 mixed precision, distillation to ESM-2 8M, incremental HDF5 cache, quality verification
  - Risk 5: 迭代精化不收敛 (LOW, MEDIUM) → Confidence threshold monitoring, early stopping K_max=10, held-out validation set, Frobenius norm convergence, divergence detection and rollback

### 附录
- 附录A：符号表
- 附录B：代码仓库结构
- 附录C：依赖项
- 附录D：配置文件示例

## WRITING GUIDELINES
- Write in Chinese with English technical terms
- Use proper Markdown formatting
- Include all mathematical formulas using LaTeX notation ($$...$$)
- Include all code blocks with proper Python syntax
- Include all tables with proper formatting
- Total target length: 60,000-80,000 characters
- This is a comprehensive research methodology document - be thorough and precise
- Every section should have concrete, actionable content (not placeholders)
- All numerical estimates should use the corrected values (~63,000 final dataset, 11.8% host label rate)

## OUTPUT
Write the complete document to: /home/zczhao/Phage-Agent/PHI_OT_Research_Plan_v3.md
- source_files_used: 0
- method_sources: 0
- result_sources: 0
- supplementary_sources: 0

## Included source files

- None

## Notes

- This draft was assembled locally from existing Markdown outputs.
- Missing sections remain explicitly marked as not available.
