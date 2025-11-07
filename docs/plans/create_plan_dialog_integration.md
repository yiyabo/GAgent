// 前端对话触发 Create Plan 的一体化方案

# 前端对话触发 Create Plan 的一体化方案

本文给出“通过前端对话创建 Plan 并自动分解”的端到端实现方案：需要的后端接口、前端对接改动、事件驱动刷新机制、错误处理与调试要点。

## 目标
- 用户在聊天输入“帮我创建一个计划…”，后端结构化 LLM 返回 `create_plan` 动作，后台异步执行创建与自动分解；前端立即收到回复并开始轮询进度；PlanTree 更新后前端视图自动刷新。
- 对 LLM 返回 JSON 的强约束与容错：即使 LLM 产出不合规依赖/上下文，也不会让后端中断。

## 总体时序
1) 前端调用 `POST /chat/message` 发送用户消息（附 `session_id`）。
2) 后端 `StructuredChatAgent.get_structured_response` 解析 LLM 的结构化 JSON，返回：
   - `llm_reply.message` 给用户即时反馈；
   - `actions` 列表（含 `plan_operation/create_plan`）状态为 pending；
   - `metadata.tracking_id` 用于轮询。
3) 后端将动作入队 `BackgroundTasks` 异步执行；执行阶段：
   - 创建 plan（主库登记、生成 per‑plan sqlite）；
   - 触发 `PlanDecomposer.run_plan()` 执行 BFS 分解；
   - 写入任务与上下文，过滤无效依赖；
   - 完成后回写 `chat_action_runs` 并绑定会话的 `plan_id`/`plan_title`。
4) 前端轮询 `GET /chat/actions/{tracking_id}`；完成后：
   - 更新本地 `currentPlanId/currentPlanTitle`；
   - 触发窗口级事件 `tasksUpdated`；
   - DAG/Tree 组件监听到事件，调用 `GET /plans/{id}/tree` 重新拉取并渲染。

---

## 后端改造点

已具备的能力与关键位置：
- 结构化两阶段聊天
  - 接口：`app/routers/chat_routes.py: ChatRequest -> ChatResponse`
  - 即时返回 pending 动作并入队后台任务：`/chat/message` 中 400–466 行附近。
  - 后台执行器：`_execute_action_run()` 入口（约 784–872 行）。
- 动作执行器（代理）
  - `StructuredChatAgent._handle_plan_action()` 内的 `create_plan` 会在成功后调用 `_auto_decompose_plan()`；位置约 1160–1260 行。
  - 自动分解器：`app/services/plans/plan_decomposer.py`。
  - 分解提示词已强化，要求严格 JSON：`DecompositionPromptBuilder.build()`。
- 依赖与上下文容错
  - 无效依赖过滤：`app/repository/plan_repository.py:609–621` 仅插入已存在的依赖目标，避免外键失败。
  - `DecompositionChild` 对 `context.sections` 强制为对象数组；提示词中已明确示例。
- 会话与动作持久化
  - `chat_action_runs` 表：`app/database.py`。
  - 动作状态查询：`GET /chat/actions/{tracking_id}` 已实现（见 900+ 行）。
  - 会话绑定 plan：执行成功后 `_set_session_plan_id()` 自动写入。

需确认/补充（如尚未存在）：
- 聊天历史查询接口（前端列表页需要）
- 建议新增：`GET /chat/sessions`（会话摘要列表）与 `GET /chat/history/{session_id}`（该会话消息，当前已提供），若尚未实现请在 `app/routers/chat_routes.py` 同一 Router 下补齐。
- 接口文档
  - Swagger 可在 `http://localhost:9000/docs` 自检接口；请同步更新每个字段含义（tracking_id、actions[].status 等）。

接口契约（前端依赖的关键字段）：
- `POST /chat/message`
  - 请求体：`{ message, session_id, mode?, history?, context? }`
  - 响应体：
    - `response`: LLM 回复给用户的话术
    - `actions`: 按序的动作数组（当后台执行时 `status=pending`）
    - `metadata.tracking_id`: 轮询用的 ID
    - `metadata.plan_id`: 已绑定的 plan（可能为 null，待后台绑定）
- `GET /chat/actions/{tracking_id}`
  - 响应体：`{ status, plan_id, actions[], errors?, result? }`
  - `status` in `pending | running | completed | failed`
- `GET /plans/{id}/tree`：返回 PlanTree

---

## 前端改造点

1) API 封装
- `web-ui/src/api/chat.ts`（或现有 Chat Api 模块）
  - `sendMessage(payload: ChatRequest)` -> `ChatResponse`
  - `getActionStatus(trackingId: string)` -> `{ status, plan_id, actions, ... }`
  - `getSessions()`/`updateSession()`（如已存在保持契约一致）
- `web-ui/src/api/planTree.ts` 已有 `getPlanTree(planId)`。

2) Store 与流程（以现有 `useChatStore` 为例）
- 发送消息后，如果响应 `metadata.tracking_id` 存在：
  - 在 UI 上标记为“正在执行…”，并启动轮询 `getActionStatus(trackingId)`；
  - 轮询策略：
    - 间隔 2–3s，最长 120s（按要求“轮询增加到 120 秒”）；
    - `completed/failed` 即停止；失败时在对话中插入错误摘要。
  - 成功后：
    - 从 `statusResponse.plan_id` 取到最终绑定的 Plan；
    - 更新本地 `currentPlanId/currentPlanTitle`；
    - `window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: { plan_id } }))`；
    - 拉取并渲染 PlanTree 视图（由组件监听事件触发）。
- 切换/创建会话：
  - 去掉“会话创建时立刻 PATCH 后端”的早期逻辑（避免 404）。
  - 第一次 `POST /chat/message` 携带新 `session_id` 时，后端会自动插入一条 `chat_sessions` 记录（已实现）。

3) 可视化组件（DAG/Tree）
- 已监听 `tasksUpdated` 事件并在触发时调用 `getPlanTree`（参见：
  - `web-ui/src/components/dag/TreeVisualization.tsx:256` 与 `:260`
  - `web-ui/src/components/dag/DAGVisualization.tsx` 类似逻辑）
- 无需额外修改，只需确保 `currentPlanId` 在计划绑定完成后被更新。

4) UI 反馈
- 在聊天气泡中渲染 `actions` 概览（状态 `pending`），并提示“后台执行中，可稍候刷新”。
- 成功后替换为 `completed` 动作列表与结果摘要；失败则显示 `errors`。

---

## 错误处理与健壮性
- 依赖 ID 可能无效：后端已在 `_replace_dependencies` 过滤不存在的依赖，避免外键失败。
- `context.sections` 非法：已加强提示词；如仍返回字符串数组，可在后端解析层（`DecompositionChild.from_payload`）增加兜底把字符串包装成 `{title, content}` 对象。
- 自动分解失败：后端仍会完成计划创建并绑定会话；前端显示错误摘要并允许后续“手动分解”动作。
- 轮询超时（>120s）：前端提示用户稍后在计划视图刷新或再次尝试。

---

## 配置与对齐
- 确认运行环境一致：前端/脚本/后端使用同一 `DB_ROOT`（否则会出现“脚本看见、前端看不见”的错位）。
- LLM 配置：
  - 主对话 LLM：`LLM_PROVIDER/*`；
  - 分解 LLM：`DECOMP_*`；
  - 执行 LLM（如有）：`PLAN_EXECUTOR_*`。
- 开发模式建议开启后端 `--reload`，保证热更新后新逻辑立即生效。

---

## 手动验证流程
1) 重启后端（确保加载最新代码），打开 `http://localhost:9000/docs` 自检接口。
2) 前端启动后，打开聊天，发送“帮我创建…计划”。
3) 观察后端日志：应看到 `[CHAT][ASYNC] queued` → `[START]` → `create_plan` → `Auto decomposition ...` → `[DONE]`。
4) 前端应先看到 pending 动作与 `tracking_id`，随后轮询完成后任务树自动刷新显示根节点/子任务。
5) 失败场景：
   - 若 LLM 返回不合规 `sections` 或非法依赖，不应中断；计划至少被创建并绑定。

---

## 任务清单（落地步骤）
- 后端
- [ ] 确认/补齐 `GET /chat/sessions` 与 `GET /chat/history/{session_id}`。
  - [ ] 保持 `/chat/message` 与 `/chat/actions/{tracking_id}` 契约稳定。
  - [ ] 已合入：分解提示词严格化、无效依赖过滤。
- 前端
  - [ ] chatApi: `sendMessage`/`getActionStatus`/`getSessions`/`updateSession`。
  - [ ] useChatStore: 按 `tracking_id` 轮询（最长 120s），完成后更新 `currentPlanId` 并派发 `tasksUpdated`。
  - [ ] 去掉会话创建时的预先 PATCH；改为首次发消息由后端创建。
  - [ ] 聊天气泡展示 pending/完成/失败三态。

如需我直接提交上述前后端改动代码，请告知优先级与具体分支/提交策略。
