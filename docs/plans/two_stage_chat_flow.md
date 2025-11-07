# 两阶段 StructuredChatAgent 响应方案

本文描述一种改造方向：`/chat/message` 先返回对话 LLM 的原始结果（`llm_reply + actions`），然后再在后台依次执行这些动作（如自动分解计划）。这样前端可以更快呈现对话反馈，同时继续监听后端通知以更新任务树。

## 现状回顾

- `StructuredChatAgent.handle` 会在一个请求里同步完成 **动作解析 → 执行全部动作 → 刷新 PlanTree → 返回结果**。
- 当动作包含 `create_plan` 且开启自动分解时，`PlanDecomposer.run_plan` 会逐个创建任务节点，导致整次请求耗时较长，前端只有在分解完毕后才能拿到响应。

## 目标行为

1. `/chat/message` 快速返回对话 LLM 的 `llm_reply`、`actions`、初始 `metadata`。  
2. 后端在后台执行动作序列（包括自动分解、执行计划等），完成后写入数据库并广播更新。  
3. 前端接收到初始响应后立即渲染，同时根据后端的异步结果刷新 DAG / 计划树。

## 后端改造要点

| 模块 | 改动内容 |
| ---- | -------- |
| `app/routers/chat_routes.py` | - 拆分 `StructuredChatAgent.handle` 为 “解析阶段” 与 “动作执行阶段”。<br>- 增加 `handle_async`：返回 `AgentResult`（含 `tracking_id` & `pending_actions`），同时将动作执行排入 `asyncio.create_task` 或 FastAPI `BackgroundTasks`。<br>- 在响应中标记 `status: "pending"`，并附带 `tracking_id` 用于前端查询。 |
| `app/services/plans/plan_session.py` | - 新增 `persist_partial_result` / `attach_action_run`，记录未完成的动作执行计划。 |
| `app/services/plans/plan_executor.py` & `plan_decomposer.py` | - 无需大改；但执行函数要支持独立调用，以便后台任务驱动。 |
| 新增 `app/services/chat_action_dispatcher.py` | - 管理动作执行队列，封装 “读取计划 → 执行动作 → 写入数据库 → 发布事件”，并在完成后写入状态表。 |
| 数据层 (`app/repository`) | - 新增 `chat_action_runs` 表，保存 `tracking_id`、动作列表、状态、错误、开始/结束时间等。 |
| API 扩展 | - 新增 `GET /chat/action-status/{tracking_id}` 查询执行进度。<br>- 视需要新增 WebSocket/SSE 端点 `ws/chat/actions` 推送执行结果。 |
| 事件广播 | - 在后台动作执行成功后触发现有的 `tasksUpdated` 事件或直接调用现有 PlanTree Outline API，保证前端刷新。 |

### 响应结构建议

```json
{
  "response": "计划已创建，我将开始分解任务。",
  "actions": [
    {"kind": "plan_operation", "name": "create_plan", "status": "completed"},
    {"kind": "task_operation", "name": "decompose_task", "status": "pending"}
  ],
  "metadata": {
    "tracking_id": "act_20250218_abcdef",
    "plan_id": 42,
    "status": "pending"
  }
}
```

后台执行完成后，可通过 `GET /chat/action-status/act_20250218_abcdef` 或 WebSocket 推送：

```json
{
  "tracking_id": "act_20250218_abcdef",
  "status": "completed",
  "plan_id": 42,
  "results": {
    "created_task_count": 12,
    "errors": []
  }
}
```

## 前端改造要点

| 文件 | 修改方向 |
| ---- | -------- |
| `web-ui/src/api/chat.ts` | - `sendMessage` 接口支持新的响应字段（`metadata.status`, `metadata.tracking_id`, `actions[].status`）。<br>- 新增 `getActionStatus(trackingId)` 方法，用于轮询。 |
| `web-ui/src/store/chat.ts` | - `sendMessage` 处理初始响应时，立即渲染 `llm_reply`，将动作列表保存为 “进行中”。<br>- 启动轮询或订阅 WebSocket，当 `status` 变更为 `completed` 时更新状态并触发 DAG 刷新。<br>- `tasksUpdated` 事件触发前可结合 `tracking_id` 校验。 |
| UI 组件（如 `ChatPanel`, `PlanTreeView`） | - 显示动作的执行状态标签（Pending/Running/Completed/Failed）。<br>- 允许用户手动点击 “刷新计划” 或在后台完成后自动刷新。 |

### 前端轮询流程示例

1. 接收到初始响应（状态 `pending`，附带 `tracking_id`）。  
2. 在 store 内创建一个 `pendingAction` 项，并启动 `setInterval` 每 3-5 秒调用 `chatApi.getActionStatus(tracking_id)`。  
3. 一旦返回 `status=completed/failed`，停止轮询，更新聊天消息，调用现有 `fetchPlanTree(plan_id)` 或等待后端的 `tasksUpdated` 事件。  
4. 若轮询超时（例如 120 秒），提示用户“执行超时，可稍后在活动日志查看”。

## 注意事项

- **并发控制**：后台动作执行要保证同一 `plan_id` 的请求串行，避免写入冲突。可以在 `chat_action_runs` 表里加 `plan_id` + `status` 索引；执行时加锁。  
- **失败处理**：若后台执行失败，需把异常信息写回数据库，并在前端显示错误提示。  
- **兼容旧流程**：可用 `query` 参数或配置开关，允许暂时回退到同步模式。  
- **日志与监控**：记录每个 `tracking_id` 的开始/结束时间、耗时、LLM 调用次数，便于排查卡顿。

通过以上改造，用户能在 1 次请求中先看到对话回复，再在数秒内看到任务树逐步更新，实现更流畅的交互体验。
