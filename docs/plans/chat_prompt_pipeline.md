# 对话提示词拼接流程说明

本文档概述后端在 `/chat/message` 接口中如何组装提示词供 LLM 返回结构化 JSON，以及各步骤涉及的关键模块。

## 1. 触发入口
- 路由：`app/routers/chat_routes.py` 的 `POST /chat/message`
- 创建 `StructuredChatAgent` 时注入：
  - 当前会话 `session_id`
  - `PlanSession`（封装计划树、概览、持久化逻辑）
  - 最新历史消息（最多 10 条）
  - 请求附带的 `context`（task / plan 元数据等）

## 2. 结构化代理核心
`StructuredChatAgent` 位于 `app/routers/chat_routes.py:1023`，主要职责：
1. 拉取计划上下文：`self.plan_session.current_tree()`、`outline(max_depth=4, max_nodes=60)`。
2. 缓存统一 Schema：`schema_as_json()`（定义在 `app/services/llm/structured_response.py`）。
3. 根据当前会话状态和配置，构建 LLM 提示词，再调用 `llm_service.chat_async(...)`。

## 3. 提示词拼装流程
`_build_prompt()`（同文件第 1070 行附近）按如下顺序拼接文本：
1. **系统背景**：模式、会话 ID、plan 绑定情况、额外上下文（JSON 格式）。
2. **对话历史**：最新 10 条消息，格式 `role: content`。
3. **计划信息**：
   - 已绑定 Plan：输出 `PlanSession.outline(...)` 的摘要（`chat_routes.py:1667`）。
   - 未绑定 Plan：附加可用计划目录（`summaries_for_prompt`），提示用户选定（`chat_routes.py:1706` 起）。
4. **Schema 与动作目录**：
   - 将 `schema_json` 原封不动附加，要求响应完全匹配。
   - 列出允许的 `kind/name` 组合，例如 plan_operation、task_operation、context_request、system_operation。
5. **操作准则**：
   - 通用规则：强制 JSON 输出、指令有序、禁止虚构字段等（`chat_routes.py:1728` 起）。
   - 已绑定 Plan 时新增约束（`chat_routes.py:1735` 起），包括：
     - 执行前检查依赖；
     - 用户要求“执行/运行/重跑”任务或计划时必须给出对应动作（`plan_operation.execute_plan` 或 `task_operation.rerun_task`），若缺 task_id 先调用 `task_operation.show_tasks`；
     - 只有当用户明确提出联网检索或知识库查询需求时，才调用 `web_search` / `graph_rag`；其它情况下先利用现有上下文或继续澄清；
     - 完成检索后要继续推进执行，避免只做准备。
   - 未绑定 Plan 时的澄清策略保持不变。
6. **用户消息**：原始输入。
7. **收尾指令**：`"Respond with the JSON object now."`

## 4. LLM 调用与响应处理
1. `_invoke_llm` 调用 `llm_service.chat_async`，强制真实模型（`force_real=True`）。
2. `_strip_code_fence` 去除返回的 Markdown 代码块包装。
3. 使用 `LLMStructuredResponse.model_validate_json` 校验，确保严格符合 Schema。

## 5. 动作执行与持久化
1. `execute_structured` 遍历 `structured.sorted_actions()`，调用 `_execute_action`（创建计划、任务操作、子图查询等）。
2. 若 Plan 有变更，`PlanSession` 会标记 dirty 并在结束时持久化（`self._persist_if_dirty()`）。
3. 生成自然语言回复（`structured.llm_reply.message`）、动作执行结果和建议，返回给路由层写入聊天消息表。

## 6. 关键点总结
- **统一 Schema**：确保前后端解析一致（详见 `app/services/llm/structured_response.py`）。
- **计划上下文**：所有 plan 概要由 `PlanSession` 提供，避免 LLM 冗长输入。
- **动作白名单**：限定 LLM 只能调用后端已实现的动作，降低语义偏差。
- **可靠执行**：对结构化输出做严格 JSON 验证；动作执行失败会在回复中携带错误信息。

## 7. 相关模块索引
| 模块 | 路径 | 说明 |
| ---- | ---- | ---- |
| StructuredChatAgent | `app/routers/chat_routes.py` | 对话核心代理，包含 prompt 和动作执行 |
| LLM Schema | `app/services/llm/structured_response.py` | 定义 llm_reply / actions JSON 模型 |
| PlanSession | `app/services/plans/plan_session.py` | 管理计划树、概览、持久化 |
| LLM Service | `app/services/llm/llm_service.py` | 封装实际 LLM 客户端 |
