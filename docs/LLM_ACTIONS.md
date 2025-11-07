# LLM Structured Actions

LLM responses must conform to the JSON schema:

```json
{
  "llm_reply": { "message": "string" },
  "actions": [
    {
      "kind": "plan_operation|task_operation|context_request|system_operation|tool_operation",
      "name": "string",
      "parameters": { "key": "value" },
      "blocking": true,
      "order": 1,
      "retry_policy": { "max_retries": 0, "backoff_sec": 0.0 },
      "metadata": { "key": "value" }
    }
  ]
}
```

- `llm_reply.message` is the only user-facing text.
- Each element in `actions` represents an instruction for the backend to execute; `actions` can be empty when no tooling is required.
- `blocking` defaults to `true` when omitted. Use `false` only when the backend may proceed in parallel.
- `order` begins at `1` and increases sequentially.
- `retry_policy` and `metadata` are optional extensions.
- LLM **must not** fabricate identifiers; only use IDs/names supplied via context.
- Backend auto-fills runtime identifiers (`conversation_id`, `plan_id`, `request_id`, timestamps, etc.).

## Action kinds

| Kind              | Purpose                                                |
| ----------------- | ------------------------------------------------------ |
| `plan_operation`  | Plan lifecycle actions (create, list, execute, delete) |
| `task_operation`  | Task tree CRUD and status updates                      |
| `context_request` | Retrieve additional context from the backend           |
| `system_operation`| System-level prompts (e.g., help)                      |
| `tool_operation`  | Invoke external tools（目前支持 `web_search`、`graph_rag`） |

## Plan-level actions

| Action        | Kind            | Required parameters             | Optional parameters                         | Notes                                                                 |
| ------------- | --------------- | --------------------------------| ------------------------------------------- | --------------------------------------------------------------------- |
| `create_plan` | `plan_operation`| `goal` (string)                 | `title`, `notes`, `sections`, `style`       | Triggers plan generation workflow; typically `blocking: true`.        |
| `list_plans`  | `plan_operation`| —                               | `session_id`, `workflow_id`                 | Enumerates visible plans.                                             |
| `execute_plan`| `plan_operation`| `plan_id` (int)                 | —                                           | Marks plan for execution; backend handles scheduling.                 |
| `delete_plan` | `plan_operation`| `plan_id` (int)                 | —                                           | Permanently deletes plan and tasks.                                   |
| `help`        | `system_operation`| —                             | —                                           | Returns available commands/usage tips.                                |

## Task and context actions

| Action                    | Kind             | Required parameters                          | Optional parameters                                | Notes                                                                                             |
| ------------------------- | ---------------- | -------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `create_task`             | `task_operation` | `task_name` (string)                         | `plan_id`, `parent_id`, `instruction`, `metadata`, `dependencies`, `anchor_task_id`, `anchor_position`, `position`    | Adds task to plan；可选锚点参数支持插入到特定位置。                                              |
| `update_task`             | `task_operation` | `task_id` or `task_name`                     | `name`, `instruction`, `metadata`, `dependencies`   | Modifies task metadata; name-based lookup may require confirmation.                              |
| `update_task_instruction` | `task_operation` | `instruction` (string) and `task_id`/`task_name` | —                                              | Replaces task instruction; backend merges with existing prompt.                                   |
| `move_task`               | `task_operation` | `task_id`/`task_name`                        | `new_parent_id`, `new_parent_name`                 | Reparents task; `new_parent_id: null` moves to root.                                             |
| `delete_task`             | `task_operation` | `task_id` or `task_name`                     | `plan_id`                                          | Deletes task subtree; returns removal summary.                                                    |
| `show_tasks`              | `task_operation` | `plan_id` (int) or bound plan context        | —                                                 | Returns task list and tree for visualization.                                                     |
| `query_status`            | `task_operation` | `plan_id` or `task_id`                       | —                                                 | Provides plan/task status overview; task lookup returns owning plan context.                      |
| `rerun_task`              | `task_operation` | `task_id` (int)                              | —                                                 | Resets task to `pending` and requests re-execution.                                               |
| `decompose_task`          | `task_operation` | Bound plan (implicit)                        | `task_id`, `expand_depth`, `node_budget`          | Invokes the standalone PlanDecomposer LLM to expand tasks in BFS order; omit `task_id` to target plan roots. |
| `request_subgraph`        | `context_request`| `logical_id` or `task_id`                    | `max_depth`                                       | Requests additional graph detail; should be the sole action in the response that issues it.       |
| `web_search`              | `tool_operation`| `query` (string)                              | `max_results`, `locale`, `time_range`, `provider` | Calls configured web search provider并返回摘要、引用链接，结果附在回复 metadata。                                    |
| `graph_rag`               | `tool_operation`| `query` (string)                              | `top_k`, `hops`, `return_subgraph`, `focus_entities` | 查询噬菌体/宿主知识图谱，返回三元组、提示词以及可选子图 JSON；结果同样写入 metadata。                   |

> `anchor_position` 支持 `first_child` / `last_child` / `before` / `after`。当使用 `before` 或 `after` 时，必须同时提供 `anchor_task_id`（且锚点与目标父节点一致）；否则 fallback 到默认的末尾插入。指定绝对 `position` 会跳过锚点计算，主要供调试或自动化脚本使用。旧版字段 `insert_before` / `insert_after` 仍被接受，并分别映射为 `anchor_position=before/after`。

> `tool_operation` 的执行结果会在聊天响应的 `metadata.tool_results` 字段返回，并写入提示上下文的 `recent_tool_results`，方便后续轮次引用。

## Examples

Create a root task:

```json
{
  "llm_reply": { "message": "已为计划 42 创建根任务，可继续细化章节。" },
  "actions": [
    {
      "kind": "task_operation",
      "name": "create_task",
      "parameters": {
        "plan_id": 42,
        "task_name": "Gene Editing Whitepaper - Overview",
        "task_type": "root",
        "instruction": "Compile the latest research milestones and key challenges."
      },
      "blocking": true,
      "order": 1
    }
  ]
}
```

Request additional context before proceeding:

```json
{
  "llm_reply": { "message": "我需要进一步展开任务 17 的细节才能决定下一步。" },
  "actions": [
    {
      "kind": "context_request",
      "name": "request_subgraph",
      "parameters": { "logical_id": 17 },
      "blocking": true,
      "order": 1
    }
  ]
}
```

Call Graph RAG to collect phage–host triples:

```json
{
  "llm_reply": { "message": "已检索噬菌体与宿主互作知识图谱，以下是关键关系摘要。" },
  "actions": [
    {
      "kind": "tool_operation",
      "name": "graph_rag",
      "parameters": {
        "query": "How do T4 phages attach to E. coli?",
        "top_k": 12,
        "hops": 1,
        "return_subgraph": true,
        "focus_entities": ["T4 phage", "E. coli"]
      },
      "blocking": true,
      "order": 1
    }
  ]
}
```

Insert a task before an existing sibling:

```json
{
  "llm_reply": { "message": "将在章节“数据准备”之前补充概述步骤。" },
  "actions": [
    {
      "kind": "task_operation",
      "name": "create_task",
      "parameters": {
        "plan_id": 28,
        "parent_id": 104,
        "task_name": "研究流程概览",
        "anchor_task_id": 215,
        "anchor_position": "before",
        "instruction": "总结后续实验的目标、输入数据和预期产出。"
      },
      "blocking": true,
      "order": 1
    }
  ]
}
```
