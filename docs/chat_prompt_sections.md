# LLM 提示词结构速览

本文整理 `StructuredChatAgent` 生成提示词的固定结构，便于前后端协同检查或在调试时快速定位各段内容。实现参考 `app/routers/chat_routes.py`（`_build_prompt` 及相关辅助函数）。

## 核心段落

1. **系统头部**
   - 固定开场语：`You are an AI assistant...`
   - 注入会话模式、Session/Conversation ID。
   - 说明 Plan 绑定状态：若已绑定显示 `当前绑定 Plan ID: #`，未绑定则提示继续澄清需求。

2. **Extra Context**
   - 将 `extra_context` 以 JSON 格式写入，包含默认搜索源、最近动作等元信息。

3. **对话历史**
   - `self._format_history()` 输出最近 10 条消息，格式 `role: content`，帮助 LLM 回顾上下文。

4. **Plan 概览与目录**
   - 已绑定 Plan：调用 `PlanSession.outline(max_depth=4, max_nodes=60)` 生成结构化摘要。
   - 未绑定 Plan：列出前 10 个候选计划（`summaries_for_prompt`），便于用户选取。

5. **Schema & Action Catalog**
   - 内嵌 `LLMStructuredResponse` 的 JSON Schema，要求响应字段完整合法。
   - 列出允许动作：包括 `system_operation`、`tool_operation`、`plan_operation`、`task_operation` 等，是否包含 `execute_plan`/`rerun_task` 等取决于是否已绑定 Plan。

6. **Guidelines（操作准则）**
   - 通用规则：严格 JSON 输出、动作按顺序、禁止虚构字段等。

- 已绑定 Plan 时额外强调：
  - 执行前确认依赖。
  - 用户指令包含“执行/运行/重跑”时必须给出对应动作；缺少任务 ID 可先调用 `task_operation.show_tasks`。
  - 只有在用户明确提出需要联网搜索或知识库检索时才使用 `web_search` / `graph_rag`，否则先结合现有信息回答或继续澄清。
  - 完成检索后，要继续安排或执行请求，避免只停留在准备阶段。
  - 当需要在现有兄弟节点之间插入任务时，可提供 `anchor_task_id` + `anchor_position`（`before/after/first_child/last_child`）；找不到锚点则退回默认追加到末尾。
- 未绑定 Plan 时的重点是澄清需求和避免主动改动计划。

7. **用户消息 & 收尾**
   - 附上原始用户输入。

  - 以 `Respond with the JSON object now.` 收束，提醒只返回 JSON 结构。

## 相关模块

| 功能 | 位置 | 说明 |
| --- | --- | --- |
| 提示词拼装 | `app/routers/chat_routes.py::_build_prompt` | 主逻辑，按上述顺序拼接文本 |
| Schema 定义 | `app/services/llm/structured_response.py` | 规定 `llm_reply` / `actions` 字段形态 |
| Plan 会话封装 | `app/services/plans/plan_session.py` | 提供 `outline`、计划列表等辅助数据 |

此结构确保 LLM 在响应时拥有计划上下文、历史与明确的动作约束，也便于未来扩展或调试。***
