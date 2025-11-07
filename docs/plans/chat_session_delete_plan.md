# Chat Session 删除能力改造方案

## 背景与目标

- 目前后端仅提供会话查询 (`GET /chat/sessions`) 与更新 (`PATCH /chat/sessions/{id}`) 接口，无法从系统中删除无用会话。
- 前端会话列表存在“已归档”状态，但本质仍留在数据库；缺少真正删除入口导致历史记录持续累积，影响用户体验与数据库体量。
- 目标：新增安全的会话删除流程，允许前端显式删除指定 session，并联动清理关联聊天记录。

## 方案概述

1. **后端提供删除接口**  
   - 路径：`DELETE /chat/sessions/{session_id}`。  
   - 功能：删除 `chat_sessions` 记录，并触发 `chat_messages` 的外键级联；`chat_action_runs` 中引用该会话的记录保留但 `session_id` 置空（沿用现有外键策略）。  
   - 行为：若会话不存在，返回 404；默认硬删除，可追加 `?archive=true` 参数实现软删除（设置 `is_active=0`）以满足潜在审计需求。
   - 审计日志：记录删除动作（INFO 级别），包含 session id、执行人（若有鉴权信息可扩展）。

2. **前端整合删除能力**  
   - API 封装：在 `web-ui/src/api/chat.ts` 中新增 `deleteSession(sessionId: string, options?: { archive?: boolean })`.  
   - Store：`useChatStore` 增加 `deleteSession` 方法；删除当前选中会话时，需自动切换到最新活跃会话或启动新会话。  
   - UI：在会话列表项的更多操作中添加“删除会话”按钮（可附确认弹窗）；若仅支持软删除则显示“归档/恢复”。  
   - 删除成功后触发 `tasksUpdated` 事件（plan_id 为空）驱动 DAG/UI 清理。

3. **一致性与同步**  
   - 删除成功后需要：
     - 清理本地 `messages`、`sessions` 列表以及 `SessionStorage` 中的缓存；
     - 若会话绑定了 plan，则通知右侧 DAG/抽屉重置上下文；
     - 若前端抽屉显示该会话的任务，应自动关闭并刷新计划树。

## 详细实施步骤

### 1. 后端改造

| 文件 | 修改点 |
| ---- | ------ |
| `app/routers/chat_routes.py` | 新增 `DELETE /chat/sessions/{session_id}` 路由；内部封装 `_delete_chat_session`；支持 `archive` 查询参数。|
| `app/services/session_context.py` (若后续复用) | 可添加 `delete_session(session_id: str, *, archive: bool = False)` 公共方法，供其他服务引用。|
| `app/database.py` | 无需结构改动；确认外键 `ON DELETE CASCADE` 已生效。|

接口约定：
```python
@router.delete("/sessions/{session_id}", status_code=204)
async def delete_chat_session(session_id: str, archive: bool = Query(False)):
    """
    archive=False -> 删除 chat_sessions 记录（硬删除）。
    archive=True  -> 更新 is_active=0, 保留历史记录（软删除）。
    """
```

### 2. 前端改造

| 文件 | 修改点 |
| ---- | ------ |
| `web-ui/src/api/chat.ts` | 增加 `deleteSession` 方法。|
| `web-ui/src/store/chat.ts` | 新增 `deleteSession` action：调用 API、更新 `sessions`/`messages`/`currentSession`、清空相关上下文并触发 `tasksUpdated`。|
| `web-ui/src/components/layout/ChatSidebar.tsx` | 在会话项操作菜单新增“删除”按钮，搭配二次确认；根据设计可同时提供归档选项。|
| `web-ui/src/components/layout/ChatLayout.tsx` 等 | 如当前会话被删除，需要关闭聊天面板或自动切换到其他会话。|

### 3. 测试计划

- **后端**：在 `test/test_chat_sessions_routes.py` 新增用例  
  - 删除存在的 session -> 204，确认 `chat_sessions` 与 `chat_messages` 均被清除。  
  - 删除不存在的 session -> 404。  
  - 参数 `archive=true` -> `is_active=0`，记录仍在。
- **前端**：暂以手动和 Cypress 待定；最低要求编写组件/Store 单元测试（若现有框架支持）或在 MR 中附 `npm run build`、`npm run lint` 结果。

### 4. 回滚与兼容

- 新增接口向后兼容，不影响旧版前端；上线时需同步前端版本以避免孤立删除按钮。
- 若软删除模式在未来需要恢复功能，可直接复用 `PATCH /chat/sessions/{id}` 设置 `is_active=1`。

## 注意事项

- 删除操作需谨慎处理可能的并发：建议使用单条 `DELETE`/`UPDATE` 语句并根据 `rowcount` 判定成功与否。
- UI 层必须提示操作不可恢复（硬删除场景），并禁止在后台请求返回前重复点击。
- 若未来有审计需求，软删除参数可默认打开；当前建议默认硬删除，配合确认弹窗确保明确性。

---

完成以上步骤后，系统即可完整支持会话删除，消除冗余会话并避免历史垃圾数据堆积。
