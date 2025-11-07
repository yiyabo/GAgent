# Chat Sessions 前端改造方案

目标：让前端会话列表与后端 `/chat/sessions` 数据保持一致，支持后端的会话元数据（计划绑定、最后消息时间等），并兼容现有聊天历史/PlanTree 显示逻辑。

---

## 1. API 层封装

### 1.1 新增会话 API

- `web-ui/src/api/chat.ts` 中添加：
  - `getSessions(params?: { limit?: number; offset?: number; active?: boolean }): Promise<ChatSessionSummary[]>`
  - `updateSession(sessionId: string, payload: Partial<ChatSessionSummary>): Promise<ChatSessionSummary>`
- 封装时复用 `BaseApi.get/post/patch`，序列化查询参数（limit/offset/active）。
- 定义 TypeScript 类型 `ChatSessionSummary`（与后端一致：`id`, `name`, `plan_id`, `plan_title`, `current_task_id`, `current_task_name`, `last_message_at`, `created_at`, `updated_at`, `is_active`）。可放在 `types/index.ts`。

### 1.2 更新聊天状态接口类型

- `/chat/status` 新响应包含 `llm`, `decomposer`, `executor`, `features`, `warnings`。调整 `ChatStatusResponse` 类型，以便前端诊断页正确显示 Provider/Model。

---

## 2. Store 初始化改造（`useChatStore`）

### 2.1 `loadSessions` 方法

- 新增 `loadSessions: () => Promise<void>`：
  1. 调用 `chatApi.getSessions()`；
  2. 将响应转换为前端 `ChatSession`（字段映射：`title = summary.name ?? summary.plan_title ?? '会话 <短 id>`；`session_id` 同 `id`；初始 `messages = []`）；
  3. 写入 `sessions` 状态，更新 `SessionStorage` 中的 `allSessionIds`；
  4. 若当前没有 `currentSession` 且列表非空，选中第一条并设置 `currentPlanId`/`currentPlanTitle`；
  5. 如果接口返回空列表，保留现有 `startNewSession()` 逻辑以便创建首个会话。

### 2.2 启动时加载

- 在应用入口（如 `App.tsx` 或聊天页面）使用 `useEffect` 调用 `loadSessions()`。
- 若 `SessionStorage` 中存在 `current_session_id`，优先定位该会话；没有则默认选中列表第一条。

---

## 3. 会话切换流程

1. ChatSidebar 渲染 `sessions`（使用 `title/name`, `plan_title`, `last_message_at`）。
2. 当用户点击某个会话：
   - 调用 `setCurrentSession(session)`；
   - 同步 `currentPlanId = session.plan_id ?? null`，让 DAG/树视图即时响应；
   - 调用既有的 `loadChatHistory(session.id)` 拉取消息列表。若历史返回的 metadata 提供了更准确的 plan 信息，可覆盖 `currentPlanId/currentPlanTitle`。

---

## 4. 新建、重命名及归档

### 4.1 新建会话

- `startNewSession(title?: string)`：
  1. 生成本地 `sessionId`；
  2. 调用 `addSession` 更新 store；
  3. 立即发送 `updateSession(sessionId, { name: title, is_active: true })`，后端会建档；
  4. 建议同时发首条 `POST /chat/message`（此时 session 会自动写入 chat_sessions），但为了保持一致，更新 API 后仍 patch 一次以确保元数据存在。

### 4.2 重命名/归档

- 在 UI 中提供“编辑会话”功能时，调用 `updateSession(sessionId, { name })` 或 `{ is_active: false }`。
- 更新成功后刷新 store 中对应会话（或重新 GET）。

---

## 5. UI 同步

- ChatSidebar 条目显示建议：标题（`name`）、计划名/状态（`plan_title` + `is_active` 徽章）、最后消息时间（`last_message_at`）。
- 顶部标题、任务树、DAG 图都读取 `currentPlanId`、`currentPlanTitle`，无需再从聊天消息推断。
- 当 `loadChatHistory` 完成后，若 metadata 中映射到不同的 plan，可调用 `_synchronize` 方法更新 `sessions` 中的该条记录，保持列表一致。

---

## 6. 兼容性与缓存

- 迁移期间可能存在仅在本地保存但后端无记录的 session：
  - `loadSessions()` 拉回列表后，对比 `SessionStorage.allSessionIds`，对缺失项调用 `startNewSession` 或直接移除缓存。
  - 若用户刷新后没有选中任何会话，可提示“暂无会话，请先发送消息”并提供创建按钮。
- 建议添加一次性脚本或在首次加载时检测、同步旧数据，避免重复创建。

---

## 时序总结

```
App 启动
 ├─ useEffect → chatStore.loadSessions()
 │    ├─ GET /chat/sessions
 │    ├─ set(sessions)
 │    └─ setCurrentSession(默认)
 └─ 若存在 currentSession → loadChatHistory(session.id)

用户选择会话
 ├─ setCurrentSession(session)
 ├─ setCurrentPlanId(session.plan_id)
 └─ loadChatHistory(session.id)

新建会话
 ├─ startNewSession()
 │    ├─ 生成 id，addSession
 │    ├─ PATCH /chat/sessions/{id} (name/is_active)
 │    └─ setCurrentSession + SessionStorage
 └─ 用户发消息 → POST /chat/message
```

---

## 后续验证

1. 启动后端 + 前端，确认首屏显示会话列表（若为空提示创建）。
2. 创建/切换/重命名/归档会话时观察 `/chat/sessions` 数据是否同步更新。
3. 刷新页面后，能恢复上一会话并载入历史消息，同时 DAG/树视图切换到对应计划。
4. 通过 `npm run lint` / `npm run build` 确认无类型/构建错误。

完成以上改造后，前端会话管理与后端保持一体化，并为后续功能（多计划、归档、实时协同等）打下基础。
