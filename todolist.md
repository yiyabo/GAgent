# 项目待办清单（ToDoList）

本清单聚焦于：将上下文感知模块中的 TF-IDF 语义检索替换为词嵌入模型，并围绕该方向提出一个可投稿 NeurIPS 的研究级方案；同时梳理整体系统的可优化点与执行计划。

---

## 一、研究议题（NeurIPS 级别 Idea）

### 题目（暂定）
结构感知的任务图对比嵌入（Graph- and Context-Aware Contrastive Task Embeddings, GCA-CTE）用于层级 Agent 的上下文检索与生成质量最优化

### 核心动机
现有 TF-IDF 在 `app/services/context.py:gather_context()` 的 `tfidf_k` 检索对语义与层级结构均不敏感；而传统通用嵌入忽略了我们系统中“任务图结构（父子/兄弟/依赖）”与“执行收益”的监督信号。我们提出在任务图结构和执行反馈信号约束下学习任务专属嵌入，以最大化下游生成质量与一致性。

### 核心贡献
- 结构对比学习：利用 `dep:requires / dep:refers / sibling / parent-child` 等关系构建多粒度正负样本，学习结构感知的任务嵌入。
- 执行收益对齐：以任务执行的客观指标（如 `tests/test_context.py` 的覆盖度、单次生成成功率、审校通过率、评测分）作为强化或再加权信号，使检索强化对最终输出质量的贡献。
- 动态上下文预算：将 `app/services/context_budget.py:apply_budget()` 中的预算作为软约束融入检索评分（长度惩罚 + 结构优先级），学习“性价比最优”的上下文选择策略。
- 图注意力检索器：在候选上下文构建时引入图注意力（GAT/Graph Transformer），使检索显式聚焦于结构近邻与语义高相似的节点。

### 方法概述
1) 预训练/初始化嵌入：采用开源中文兼容的 `sentence-transformers`（如 bge-m3/bge-large-zh）或 `text-embedding-3-large`（若可用）。
2) 结构对比学习：
   - 正样本：同一链路上的父/子、requires/refers、完成后强相关的兄弟；
   - 难负样本：同一主题但非直接结构相关，或历史上被错选为上下文的节点；
   - 损失：InfoNCE + 结构权重（不同关系赋予不同权重）。
3) 反馈对齐（可选）：对完成质量较高的上下文对给予更高权重（加权对比损失，或离线 RLHF 风格奖励建模）。
4) 检索器融合：
   - 基础相似度：余弦相似 + 长度/预算惩罚；
   - 结构先验：对 `dep:requires/refers`、`sibling`、层级距离近的候选打结构 prior 分；
   - 图注意力：在候选子图上运行 1-2 层 GAT，重排 Top-K。
5) 端到端评价：
   - 回归现有 `tests/test_context.py:test_gather_context_with_tfidf_retrieval` 场景，替换为 `embedding_k`；
   - 统计最终生成质量（coverage、一致性、审阅通过率）和检索命中率（是否召回关键依赖）。

---

## 二、实施计划（分阶段可落地）

- [ ] 阶段 0：代码勘测与切换点位
  - [ ] 定位 TF-IDF 代码路径与接口：`app/services/context.py` 中 TF-IDF 候选与打分逻辑；
  - [ ] 设计新检索接口：`retrieve_similar_tasks(task_id, repo, k, model, use_structure=True)`；
  - [ ] 配置项：在 `gather_context()` 增加 `embedding_k`、`embedding_model`, `use_graph_prior`，并与 `tfidf_k` 互斥。

- [ ] 阶段 1：最小可用替换（MVP）
  - [ ] 选用 `sentence-transformers` 轻量模型本地跑通；
  - [ ] 为任务节点构建文本表示（name + input + output 的可配置拼接）；
  - [ ] 向量化与最近邻检索（FAISS/内存余弦相似度）；
  - [ ] 在 `gather_context()` 分支替换 TF-IDF 为嵌入检索，打通 E2E；
  - [ ] 新增测试：`tests/test_context.py` 增加 `test_gather_context_with_embeddings`。

- [ ] 阶段 2：结构先验与预算融合
  - [ ] 基于任务图（`repo.get_children/get_parents/get_links`）生成候选子图；
  - [ ] 为不同关系打结构 prior（requires>refers>sibling>others），融合到最终分数；
  - [ ] 将 `apply_budget()` 的预算作为长度惩罚项参与 rerank；
  - [ ] 指标：检索命中率↑、平均上下文长度≈稳定、生成一致性↑。

- [ ] 阶段 3：结构对比学习微调
  - [ ] 构造训练对：从历史任务/链路构造正负样本；
  - [ ] 训练脚本与数据管线（`scripts/train_task_embed.py`）；
  - [ ] 产出任务域专属嵌入模型（或 LoRA 适配）；
  - [ ] 线下 A/B 离线评测 + 小规模线上灰度。

- [ ] 阶段 4：图注意力重排与反馈对齐
  - [ ] 在 Top-N 候选子图上跑 1-2 层 GAT/Graph Transformer 进行重排；
  - [ ] 收集执行反馈（通过率、修改次数、审阅分）形成奖励信号；
  - [ ] 将奖励作为样本权重或引入轻量奖励模型做 rerank；
  - [ ] 产出可复现实验与论文草稿结果表。

- [ ] 阶段 5：论文撰写与开源
  - [ ] 论文结构：动机→方法→实验→消融→可视化；
  - [ ] 提供复现脚本与模型权重；
  - [ ] 对比基线：TF-IDF、通用嵌入（无结构先验）、仅结构 prior、仅图重排。

---

## 三、代码改动点清单

- `app/services/context.py`
  - 新增：`embedding_k`, `embedding_model`, `use_graph_prior`, `use_tfidf`（布尔开关）。
  - 新增函数：`build_task_text(task)`, `embed_texts(texts)`, `retrieve_by_embedding(query, candidates, k, structure_prior, budget_penalty)`。
  - 替换 TF-IDF 分支：保留为 fallback，默认走 `embedding_k`。

- `requirements.txt`
  - 增加：`sentence-transformers`, `faiss-cpu`（或 `sklearn` 余弦）；视环境添加。

- `tests/test_context.py`
  - 新增：`test_gather_context_with_embeddings()`，验证能召回与任务最相关的历史节点。

- `app/services/context_budget.py`
  - 暴露预算参数/长度惩罚工具函数，供检索阶段做 rerank。

---

## 四、评测方案与指标

- __检索质量__：
  - 关键依赖召回率（是否召回 `requires/refers` 中的关键节点）；
  - 平均相似度排名位置（MRR / nDCG）。
- __生成质量__：
  - 一致性（跨任务输出互相引用是否对齐）；
  - 审校通过率/测试通过率；
  - 人工偏好打分（小样本）。
- __效率与成本__：
  - 向量化与检索耗时；
  - 上下文长度与调用成本；
  - 内存占用。

---

## 五、其他可优化方向（优先级建议）

- __上下文片段化与去重（高）__：
  - 对长输出分段并做去重/主题聚类，减少冗余，提升预算利用率。

- __层级执行策略（中）__：
  - 在 `debug_bfs.py:debug_bfs_ordering` 基础上，引入“依赖就绪度 + 结构距离 + 历史失败率”的排序键，减少无效尝试。

- __提示词模板自适应（中）__：
  - 根据任务类型（ROOT/COMPOSITE/ATOMIC）与检索上下文种类，自适应选择提示词模板与示例。

- __检索缓存（中）__：
  - 对稳定任务缓存候选与向量，配合任务更新时间做失效；显著降低重复开销。

- __观察者日志与可视化（低）__：
  - 将 `gather_context()` 的检索/重排过程打点到 JSON，并提供简单前端可视化（便于论文展示）。

---

## 六、立即行动项（本周）

- [ ] 落地 MVP：本地嵌入检索替换 TF-IDF 并通过单测；
- [ ] 增加结构 prior 与预算惩罚的 rerank；
- [ ] 产出第一版对比实验（TF-IDF vs Embedding）。

> 备注：如需使用闭源 API（如 OpenAI Embeddings），请在本地通过环境变量注入 API Key，勿硬编码。
