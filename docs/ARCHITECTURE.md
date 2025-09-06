# 系统架构（Architecture）

本文档描述系统的分层设计、核心数据流、关键组件和存储布局，帮助快速理解与扩展。

## 分层与职责

```
app/services/
  foundation/   # 配置、日志、类型化参数（SSOT）
  llm/          # LLM 统一服务与响应缓存
  embeddings/   # 嵌入服务、批处理、缓存（线程安全）
  context/      # 上下文组装、语义检索、结构先验
  evaluation/   # 评估器、多专家/对抗、监督与缓存
  planning/     # 计划生成与递归分解（含工具感知）
  memory/       # 记忆子系统（MCP 集成）
  legacy/       # 低频/实验/过渡模块（后续清退）
```

- foundation: 单一事实来源（SSOT），集中配置与日志；避免在子模块直接读取环境变量。
- llm: 统一 LLM 服务（重试/回退/解析）与 LLM 响应缓存（L1+L2）。
- embeddings: 向量服务、批处理与线程安全缓存，供检索/上下文使用。
- context: 基于依赖/同计划/层级与语义检索的上下文拼装、预算裁剪。
- evaluation: 多模式评估（LLM/多专家/对抗/元认知）与监督/缓存。
- planning: 计划生成、递归分解、工具感知分解（与 Tool Box 协同）。
- memory: Memory‑MCP 集成，保存/查询语义记忆与进化。

## 核心数据流（ASCII）

```
+----------------------+     +-------------------+     +----------------------+
|     Client (CLI/UX)  | --> |   FastAPI app     | --> |     Scheduler        |
|  curl / script / UI  |     |  app/main.py      |     |  BFS / DAG / postord |
+----------+-----------+     +-----+-------------+     +----------+-----------+
           | CLI/REST         CRUD | Tasks/Plans          | execution order
           v                        v                     v
+----------+-----------+     +-----+-------------+     +----------+-----------+
| Planning / Decompose | <--> |   Repository     | <--> | Executors / LLM      |
| services/planning    |     | SQLite (tasks,   |     | execution/enhanced   |
+----------------------+     | outputs, eval)   |     +----------+-----------+
                                  |     ^                      |
                                  |     |                      |
                         +--------+-----+--------+     +------+--------------+
                         |   Context Builder     |     |  Evaluation System  |
                         | services/context      |     | services/evaluation |
                         +----------+------------+     +----------+----------+
                                    |                           |
                                    | embeddings/similarity     |
                                    v                           |
                         +----------+------------+              |
                         |  Embeddings Service   | <------------+
                         | services/embeddings   |
                         +-----------------------+
```

## 存储与缓存（DatabaseConfig）

- 主库：`data/databases/main/tasks.db`（任务/输入/输出/上下文快照/链接/评估记录与配置）
- 缓存：`data/databases/cache/*_cache.db`（LLM/Embedding/Evaluation 等）
- 临时与备份：`data/databases/{temp,backups}`

DatabaseConfig 负责目录创建、迁移与路径规范；业务侧不直接硬编码路径。

## 评估与监督

- 评估器：`services/evaluation/{llm,expert,adversarial,meta,phage}_evaluator.py`
- 缓存：`services/evaluation/evaluation_cache.py`（L1+SQLite；键含内容/上下文/方法/配置）
- 监督：`services/evaluation/evaluation_supervisor.py`（质量监控、阈值校准、告警）

## 上下文与检索

- 语义检索：`services/context/retrieval.py`（embeddings 相似度 + 结构先验 rerank）
- 预算裁剪：`services/context/context_budget.py`（sentence/truncate 策略）
- 全局索引：`services/context/index_root.py`（始终纳入 INDEX.md）

## 与 Tool Box 协作

执行器根据任务与上下文判断是否启用工具（信息工具丰富上下文、产出工具落地结果），实现“最小充分工具增强”；避免无谓的成本与延迟。

## 扩展建议

- 评估插件化：在 evaluation 子包下增加自定义评估器与权重组合。
- 检索适配：embeddings/检索器可替换为外部向量库（Faiss/PGVector/Cloud）。
- 统一指标：将监督指标暴露到 Prometheus/Grafana 以增强可观测性。

