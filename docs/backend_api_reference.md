# 后端接口参考手册（PlanTree 版）

> 本文档基于当前 RouterRegistry 注册的路由，列出所有对前端/外部公开的 HTTP 接口。若需新增、废弃接口，请同步更新注册表以保持本清单准确。

## 1. Chat 接口（`/chat`）

| Method & Path | 描述 | 备注 |
| --- | --- | --- |
| `POST /chat/message` | 结构化对话入口。接收用户消息、历史与上下文，驱动 `StructuredChatAgent` 执行计划/任务操作。返回助手回复、动作列表以及 `metadata`（含 `tool_results`、`plan_id` 等）。 | LLM 可以在 `actions` 中调用 `plan_operation`/`task_operation`/`context_request`/`tool_operation`（目前支持 `web_search`、`graph_rag`）。当存在阻塞动作时会立即返回 `tracking_id`。 |
| `GET /chat/actions/{tracking_id}` | 查询异步动作执行状态。 | 响应 `ActionStatusResponse`，会同步返回本轮产生的 `tool_results`。 |
| `GET /chat/history/{session_id}` | 返回指定会话的历史消息。 | 支持 `limit`（默认 50），每条消息包含 `metadata` 以便恢复上下文。 |
| `GET /chat/sessions` | 会话列表，按最近消息时间倒序。 | 响应 `ChatSessionsResponse`，含 `plan_title`、`current_task_*`、`settings.default_search_provider` 以及 `name_source` / `is_user_named`。 |
| `PATCH /chat/sessions/{session_id}` | 更新会话属性（名称、激活状态、绑定计划/任务、默认 Web 搜索 Provider）。 | 请求体参考 `ChatSessionUpdateRequest`；当携带 `name` 时会自动标记 `is_user_named=true`。 |
| `POST /chat/sessions/{session_id}/autotitle` | 生成或刷新指定会话标题。 | 请求体可选 `force`、`strategy`；响应返回新标题及来源（plan/heuristic/default/user 等）。 |
| `POST /chat/sessions/autotitle/bulk` | 批量为未命名会话生成标题。 | 可传 `session_ids` 或 `limit` 走自动选取；同样支持 `force`、`strategy`。 |
| `GET /chat/status` | 返回聊天、分解器、执行器以及 LLM 的配置状态。 | 供前端健康面板使用。 |

## 2. 计划/任务接口（`/plans`, `/tasks`）

| Method & Path | 描述 | 返回结构 |
| --- | --- | --- |
| `GET /plans` | 列出全部计划概览。 | `PlanSummary` 数组。 |
| `GET /plans/{plan_id}/tree` | 获取完整 PlanTree（节点、邻接表、plan metadata）。 | `PlanTree.model_dump()`。 |
| `GET /plans/{plan_id}/subgraph` | 基于节点生成子图并返回 outline。 | `SubgraphResponse`；参数：`node_id`、`max_depth`。 |
| `GET /plans/{plan_id}/results` | 汇总计划内任务执行结果，支持 `only_with_output` 过滤。 | `PlanResultsResponse`（含 `TaskResultItem` 列表）。 |
| `GET /plans/{plan_id}/execution/summary` | 聚合任务状态统计。 | `PlanExecutionSummary`。 |
| `GET /tasks/{task_id}/result` | 获取单个任务的最新执行结果。 | `TaskResultItem`；需携带 `plan_id`。 |
| `POST /tasks/{task_id}/decompose` | 触发单节点分解（PlanDecomposer）。 | 请求体参考 `DecomposeTaskRequest`；返回 `DecomposeTaskResponse`。 |

> Plan/Task 的 CRUD 仍建议通过 `/chat/message` 的结构化动作完成，REST 接口主要用于只读及外部工具接入。

## 3. Memory 接口（`/mcp`）

| Method & Path | 描述 |
| --- | --- |
| `POST /mcp/save_memory` | 保存记忆条目（兼容 Memory MCP 协议）。 |
| `POST /mcp/query_memory` | 查询记忆，支持文本检索/类型过滤。 |
| `GET /mcp/memory/stats` | 返回记忆系统统计数据。 |
| `POST /mcp/memory/auto_save_task` | 可选：任务完成时自动生成记忆。 |

## 4. 系统健康与信息接口

| Method & Path | 描述 | 说明 |
| --- | --- | --- |
| `GET /system/health` | 综合健康检查，返回系统状态与建议。 | `SystemHealthResponse`。 |
| `GET /system/metrics/vector` | 向量存储性能指标。 | 用于监控/调试。 |
| `POST /system/maintenance/optimize` | 触发维护任务（缓存、索引、数据库）。 | 受权限控制。 |
| `GET /system/info` | 返回运行平台、Python 版本、可用 Provider。 | 供仪表盘展示。 |
| `GET /health` | 基础健康检查。 | `{"status": "healthy"}`。 |
| `GET /health/llm?ping=true` | LLM 客户端配置与连通性检查。 | `ping=true` 将执行实时握手。 |

## 5. 维护建议

1. **统一注册**：所有路由均在 `app/routers/registry.py` 注册，请在新增/删除接口时同步更新，保持文档与实际部署一致。
2. **鉴权约定**：如需限制匿名访问，可在注册信息中设置 `allow_anonymous=False` 并在依赖层处理；Plan 相关接口通常要求调用方携带 `plan_id`。
3. **工具调用说明**：LLM 可通过 `tool_operation` 触发 `web_search` 或 `graph_rag`。两者的结果会写入聊天消息的 `metadata.tool_results`，前端可据此渲染翻页卡片或知识图谱。
4. **接口演进**：旧版 `/tasks/*`、`/plans/*` CRUD 已删除，前端如仍引用请迁移至上述新端点或结构化聊天动作。
