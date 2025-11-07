# Plan 对话重构需求（审核稿）

## 会话与 Plan 绑定

- 每次对话 Session 必须绑定一个具体的 Plan：要么新建空白 Plan，要么选取数据库中已有的 Plan。
- 未绑定 Plan 的会话仅允许引导用户选择或创建 Plan，禁止执行 Plan/Task 相关操作。

## Plan 的内存加载

- 对话开始时，将目标 Plan 及所有任务装载为内存中的任务树（包含父子结构、指令、关联信息、上下文、执行结果等字段）。
- 节点数据保留 `status`（`pending`/`running`/`completed`/`failed`/`skipped`）与最新 `execution_result`，用于呈现进度与 LLM 产出；不再维护“优先级”等废弃字段。
- 内存中的 Plan 是整个对话过程中唯一的事实来源；每次动作直接修改内存结构。

## Prompt 构建要求

- 调用 LLM 时，将 Plan 概览与任务树嵌入系统提示。对于大型 Plan，可按深度/节点数限量展示，并允许 LLM 使用 `request_subgraph` 拉取子树。
- 保留结构化响应 Schema（`LLMStructuredResponse`）和动作目录，动作类别包含：
  - `plan_operation`: `create_plan`、`list_plans`、`execute_plan`、`delete_plan`
  - `task_operation`: `create_task`、`update_task`、`update_task_instruction`、`move_task`、`delete_task`、`show_tasks`、`query_status`、`rerun_task`
  - `context_request`: `request_subgraph`（仅当需要额外任务详情时使用，且输出中不可混入其它动作）
  - `system_operation`: `help`
- Prompt 中去除任务优先级描述，但仍允许引用节点 `status`/`execution_result` 以便 LLM 感知当前完成情况。

## Plan 更新与持久化

- 对话中的动作首先作用于内存 Plan；对话结束或触发“保存”指令时，将内存 Plan 树整体写回数据库。
- 建议的数据库结构：
  - 主库 `plans`：Plan 元信息（id、title、owner、plan_db_path、创建/更新时间、metadata 等），同时维持会话与聊天记录。
  - 每个 Plan 拥有独立的 `plan_{id}.sqlite` 文件，内部包含 `tasks`（记录 PlanNode 结构、`status`、`execution_result` 及 context 字段）、`task_dependencies`、`snapshots`、`plan_meta` 等表，专门存储任务树及相关扩展数据。
  - 快照记录写入各自 plan 文件的 `snapshots` 表，便于版本对比与回滚。
- 写回时可采用整表重写或版本号机制，确保原子性并避免并发冲突。

## 会话数据存储

- 继续使用关系型数据库持久化聊天记录：
  - `chat_sessions`：`id`、`plan_id`、`created_at`、`updated_at` 等。
  - `chat_messages`：`session_id`、`plan_id`、`role`（user/assistant/system）、`content`、`created_at`。
- 可选地保留 `metadata` 字段存储结构化附加信息。

## 对话执行流程概述

1. 确认或创建 Plan → 加载任务树到内存。
2. 拼接 Prompt（Plan 概览、任务树、动作目录、上下文、Schema）。
3. 调用 LLM，获取结构化响应（`llm_reply` + `actions`）。
4. 解析 `actions`，逐项更新内存 Plan（自动维护节点状态、上下文与执行结果）。
5. 记录对话消息。
6. 会话结束或保存指令触发时，将 Plan 内存状态写回数据库并更新版本/快照。

## 后续扩展建议

- 引入缓存层（如 Redis）加速 Plan 树加载，并设计一致性策略。
- 将动作执行从占位提示升级为实质的 Plan/Task 增删改 API。
- 增加版本比较、冲突检测、多会话协同等能力。
