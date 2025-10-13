# Agent 智能体执行蓝图

## 1. 项目现状概览
- **对话入口**：`app/routers/chat_routes.py` 已支持上下文记忆、智能路由、Agent 工作流触发，并返回带摘要的 DAG 元数据。
- **工作流生成**：`/agent/create-workflow` 接口基于 `propose_plan_service` 生成 ROOT/COMPOSITE 结构，目前 COMPOSITE 尚未进一步拆解为 ATOMIC。
- **工具体系**：Tool Box 已注册 `web_search`、`database_query`、`file_operations` 等，智能路由会在专业话题/任务查询时自动触发。
- **数据层**：任务保存在 `data/databases/main/tasks.db` 的 `tasks` 表，现阶段不同 ROOT 任务尚未作强隔离，DAG 更新依旧依赖手动刷新。
- **前端呈现**：DAG 侧栏组件已接收 `dag_preview`、`execution_plan_summary` 等元数据，但仍需实现与会话的实时联动和多会话隔离。

## 2. Agent 功能目标拆解
1. **根任务确认**：与用户对话澄清目标 → 创建单一 ROOT 节点。
2. **阶段拆分**：在同一会话内迭代补充 → 生成若干 COMPOSITE 节点。
3. **最小单元**：继续细化 → 为每个 COMPOSITE 规划多个 ATOMIC，满足“不可再拆”“可跨阶段引用”的约束。
4. **上下文感知**：ATOMIC 执行时允许引用其它 ATOMIC/COMPOSITE 的结果，支持跨任务数据流。
5. **执行闭环**：
   - ATOMIC → 生成原子结果。
   - COMPOSITE → 通过 LLM 对各 ATOMIC 结果做智能拼接、对齐格式。
   - ROOT → 再次由 LLM 汇总所有 COMPOSITE，产出生效的最终交付物。
6. **工具调用策略**：结合 Tool Box 提供的能力（搜索、数据库、文件操作等），让 Agent 能在规划/执行阶段自动获取外部信息或持久化中间状态。

## 3. 工具调用与上下文感知
| 能力 | 现状 | TODO |
| --- | --- | --- |
| `web_search` | 已在智能路由与 Agent 工作流前置检索中使用 | 在 ATOMIC 执行阶段嵌入自适应搜索提示，并缓存引用信息 |
| `database_query` | 主要用于查询/插入任务 | 建立 **按 ROOT 隔离的任务表或视图**，保证 CRUD 只影响当前会话；支持原子任务的增删与字段更新 |
| `file_operations` | 暂未启用 | 规划报告落盘、执行日志归档 |
| 上下文缓存 | 依赖 `context_messages`、`metadata` | 为 ATOMIC/COMPOSITE 注入 `session_id`，并扩展数据库表结构保存上下文引用链 |

## 4. 工作流设计草图
```
ROOT (session_id)
├─ COMPOSITE A
│   ├─ ATOMIC A1 (可引用其它 COMPOSITE)
│   └─ ATOMIC A2
├─ COMPOSITE B
│   ├─ ATOMIC B1
│   └─ ATOMIC B2
└─ ...
```
- **任务存储结构**：建议在 `tasks` 表新增 `root_id`/`session_id` 字段或建立联结表，保证任务隔离；对 ATOMIC 增加 `context_refs` 字段记录依赖。
- **DAG 更新策略**：
  1. 用户修改任何 COMPOSITE/ATOMIC → 更新数据库记录。
  2. 立刻重算 DAG（Server 端生成树形 JSON），并通过 WebSocket 或轮询同步给前端。
  3. 维护版本号，确保历史状态可回溯。

## 5. 执行流程提案
1. **会话阶段**：
   - 使用智能路由或专门的 Planner 模式，引导用户逐步收集需求。
   - 确认 ROOT 后，将会话 ID 写入任务表。
2. **拆分阶段**：
   - 调用 LLM 生成初稿，允许用户逐条修改。
   - 针对每个 COMPOSITE 调用 LLM 继续拆解成 ATOMIC，或由用户手动补充。
3. **执行阶段**：
   - 调度引擎按拓扑排序执行 ATOMIC，必要时触发工具。
   - 每次完成后写入结果表，并触发上层自动拼接提示。
4. **交付阶段**：
   - 所有 COMPOSITE 完成 → 触发 ROOT 拼接提示。
   - 自动生成总结报告，并可调用 `file_operations` 导出。

## 6. 关键技术问题
- **任务隔离**：确保每个 ROOT/会话独立，不互相污染数据。
- **DAG 实时性**：需要事件驱动或订阅机制；当前前端仍需手动刷新。
- **上下文引用管理**：如何追踪 ATOMIC 之间的依赖、保证拼接顺序正确。
- **工具用例扩展**：设计基于执行阶段的工具选择策略，避免滥用。

## 7. 下一步讨论议题
1. 任务表结构调整方案（字段设计、迁移策略、兼容旧数据）。
2. DAG 实时推送机制：WebSocket vs. 服务端 SSE vs. 定时轮询。
3. ATOMIC 执行器：是纯 LLM 方案还是允许脚本化插件？
4. 拼接逻辑的提示词设计与结果校验流程。
5. Tool Box 扩展：是否需要新增本地知识库、向量检索等能力。

请确认上述蓝图，接下来我们可以按优先级拆分开发任务。EOF
