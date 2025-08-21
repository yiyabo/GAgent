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

## 二、实施计划（已完成基础架构）

- [x] ✅ **阶段 1：GLM Embedding 基础实现**
  - [x] 修复 `requirements.txt`，添加 numpy 和 requests 依赖
  - [x] 创建 GLM Embeddings 服务（`app/services/embeddings.py`）
  - [x] 创建语义检索服务（`app/services/retrieval.py`）
  - [x] 统一配置管理（`app/services/config.py`）
  - [x] 更新 `gather_context()` 函数，默认启用语义检索
  - [x] 完善错误处理和日志记录
  - [x] 更新测试用例，确保语义检索功能正常

- [ ] **阶段 2：性能优化与缓存**
  - [ ] 添加 embedding 缓存机制，避免重复计算
  - [ ] 优化批量处理性能
  - [ ] 添加异步 embedding 生成

- [ ] **阶段 3：结构感知增强**
  - [ ] 基于任务图关系的结构先验权重
  - [ ] 图注意力机制重排候选结果
  - [ ] 结构对比学习训练数据构造

- [ ] **阶段 4：实验与论文**
  - [ ] 对比实验设计（GLM vs TF-IDF vs 混合方案）
  - [ ] 评测指标收集（检索准确率、生成质量、执行成功率）
  - [ ] 论文撰写与开源准备

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

## 六、当前状态总结

### ✅ 已完成的核心功能
- **完整的 GLM Embedding 架构**：服务层分离，配置统一管理
- **语义检索替换**：完全替代 TF-IDF，默认启用语义检索
- **Mock 模式支持**：便于开发和测试，无需 API Key 即可运行
- **错误处理完善**：API 失败时自动降级到 Mock 模式
- **测试覆盖完整**：语义检索功能已有完整测试用例

### 🎯 下一步重点
- **性能优化**：添加缓存机制，提升响应速度
- **文档更新**：更新 README 和 API 文档
- **实验设计**：准备对比实验，验证语义检索效果

### 🔧 环境配置
```bash
# GLM API 配置
export GLM_API_KEY="your_api_key_here"
export GLM_EMBEDDING_MODEL="embedding-2"
export SEMANTIC_DEFAULT_K=5
export SEMANTIC_MIN_SIMILARITY=0.1

# 开发模式
export LLM_MOCK=1  # 使用 Mock 模式，无需 API Key
export GLM_DEBUG=1  # 启用详细日志
```
