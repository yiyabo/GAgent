# 前端当前接口使用情况速览

> 基于 `web-ui` 代码（`src/api`, `src/services`, `src/store` 等），总结现阶段 React 前端仍在调用的后端接口。便于协调后续改造、清理遗留依赖。

## 1. 已接入的后端接口

| 接口 | 方法 | 前端入口 | 主要用途 |
| --- | --- | --- | --- |
| `/chat/message` | POST | `store/chat.ts:733` | 结构化聊天入口，提交用户消息并触发 LLM 动作。 |
| `/chat/history/{session_id}` | GET | `store/chat.ts:1210` | 恢复会话历史（包含 `metadata.tool_results`）。 |
| `/chat/sessions` / `PATCH /chat/sessions/{id}` | GET / PATCH | `store/chat.ts:356`、`chat.ts:1334` | 列表/更新会话、切换默认 Web Search Provider、手动命名会话（写入 `is_user_named`）。 |
| `POST /chat/sessions/{id}/autotitle` | POST | `store/chat.ts:1244`、`ChatSidebar` 操作菜单 | 自动生成/刷新会话标题，支持强制重命名。 |
| `POST /chat/sessions/autotitle/bulk` | POST | （预留，当前仅后端调用） | 批量补全历史会话标题，前端可在后续设置页挂载。 |
| `/chat/actions/{tracking_id}` | GET | `store/chat.ts:1008` | 轮询异步动作并合并 `tool_results`。 |
| `/plans/{plan_id}/tree` | GET | `api/planTree.ts:33`（被 `ChatMainArea`、任务抽屉等调用） | 读取完整 PlanTree。 |
| `/plans/{plan_id}/subgraph` | GET | `api/planTree.ts:41`（聊天 Request Subgraph） | 获取局部子图。 |
| `/plans/{plan_id}/results` | GET | `api/planTree.ts:58`（执行结果面板） | 拉取计划内任务执行结果。 |
| `/plans/{plan_id}/execution/summary` | GET | `api/planTree.ts:70` | 聚合执行统计。 |
| `/tasks/{task_id}/result` | GET | `api/planTree.ts:66` | 单节点执行结果详情。 |
| `/health`、`/health/llm?ping=true` | GET | `api/client.ts` | 前端启动健康检查。 |
| `/system/health` | GET | `App.tsx:55` | 仪表盘展示综合状态。 |
| `/mcp/save_memory`、`/mcp/query_memory`、`/mcp/memory/stats` | POST / GET | `pages/Memory.tsx`、`store/chat.ts:1294` | 记忆管理。 |

## 2. 待确认/规划中的接口

| 接口 | 现状 | 备注 |
| --- | --- | --- |
| `/tasks/{task_id}/decompose` (POST) | 后端已实现，前端尚未直接调用。 | 若保留“手动分解”功能，可在任务抽屉中挂载。 |
| `/mcp/memory/auto_save_task` | 目前未在前端暴露入口。 | 可与执行结果联动后再启用。 |

## 3. 可移除的旧封装

| 位置 | 说明 |
| --- | --- |
| `api/tasks.ts` / `api/plans.ts` | 大量仍指向已删除的 `/tasks/*`、`/plans/*` 旧接口。建议彻底移除或改写为调用新的 `/plans/...` 与 `/chat/message`。 |
| `services/intentAnalysis.ts` 中的 `/tasks/intelligent-create`、`getSystemStatus()` | 对应接口不存在；若需要自动建任务或系统状态，请改为通过聊天动作或 `/system/health`。 |

## 4. 后续建议

1. **统一入口**：任务/计划修改仍以 `/chat/message` 的结构化动作为主，只读需求使用 `/plans/...`。
2. **精简 API 层**：删去未使用的旧 API，避免误引用；把 `planTree` 相关调用合并到单一服务模块。
3. **Graph RAG 结果展示**：聊天 `ToolResultCard` 已能展示 `graph_rag` 返回的三元组，可根据需要补充子图可视化。
