# 数据库结构概览

本项目的持久化分为两部分：主库（plan registry）与每个计划的独立 SQLite 文件。以下文档概述了各表的用途、字段与数据流向，便于后端开发与调试。

## 主库（plan_registry.db）

主库用于管理跨计划的索引、会话信息与聊天记录。由 `app/database.init_db()` 初始化。主要表：

| 表名 | 作用 | 关键字段 |
| --- | --- | --- |
| `plans` | 计划索引与元信息 | `id`、`title`、`description`、`metadata`、`plan_db_path`、`updated_at` |
| `chat_sessions` | 前端会话绑定计划 | `id`、`plan_id`、`name`、`name_source`、`is_user_named`、`is_active`、`created_at/updated_at` |
| `chat_messages` | 聊天消息存档 | `session_id`、`role`、`content`、`metadata`、`created_at` |

所有计划的具体任务树存储在 `plans.plan_db_path` 指向的独立文件中（默认 `data/plans/plan_{id}.sqlite`）。`chat_sessions.name_source` 记录标题来源（`default` / `plan` / `heuristic` / `user` 等），`is_user_named` 用于避免自动重写用户手动命名的会话。

## 单个计划数据库结构

每个计划对应一份 SQLite 文件，负责记录任务树、上下文与快照。创建计划时由 `app/repository/plan_storage.initialize_plan_database()` 建表。

### `plan_meta`

- `key` / `value`：存放标题、描述、metadata、schema 版本等信息。
- 由 `PlanRepository` 在创建/更新计划时写入，PlanSession 加载 PlanTree 时读取。

### `tasks`

每条记录对应一个 `PlanNode`；除了父子关系与路径等结构信息之外，还包含最新上下文字段。

| 字段 | 说明 |
| --- | --- |
| `id` | PlanNode 唯一 ID（主键，自增或由 upsert 指定） |
| `name` / `instruction` | 节点标题与说明 |
| `parent_id` | 父节点 ID，可为空（根节点） |
| `position` | 同级顺序，从 0 开始 |
| `path` | 预计算的“/1/3/7”路径，用于快速遍历子树 |
| `depth` | 节点深度（根为 0） |
| `metadata` | 储存节点扩展属性的 JSON（PlanNode.metadata） |
| `execution_result` | 最近一次任务执行的结果/输出（文本或序列化 JSON） |
| `context_combined` | 最新上下文的完整文本 |
| `context_sections` | 上下文分段信息（JSON 数组） |
| `context_meta` | 上下文元信息（生成方式、统计等） |
| `context_updated_at` | 上下文最近更新时间 |
| `created_at` / `updated_at` | 记录创建与最后修改时间 |

PlanRepository 在加载 `PlanTree` 时会将这些字段映射到 `PlanNode` 模型；调用 `update_task` 时也会同步写回。

### `task_dependencies`

- `task_id` / `depends_on`：记录节点间的依赖关系。
- 由 `PlanRepository.create_task` / `update_task` / `upsert_plan_tree` 维护，并在加载 PlanTree 时写入 `PlanNode.dependencies`。

### `snapshots`

- `snapshot`：`PlanTree.model_dump()` 的 JSON 快照。
- `note`：调用 `upsert_plan_tree(tree, note=…)` 时的备注。
- 可用于调试或历史回溯。

## 读写流程摘要

- **创建计划**：`PlanRepository.create_plan` 在主库写入 plans 记录并创建 `plan_{id}.sqlite`，其中 `tasks` 表初始为空。
- **加载计划**：`get_plan_tree(plan_id)` 读取主库元信息与计划文件中的 tasks + task_dependencies，组合成 `PlanTree`。
- **写入任务**：`create_task`、`update_task`、`move_task` 等直接修改独立 plan 文件的 tasks/依赖字段；上下文使用 `context_*` 列保存。
- **持久化整个树**：`upsert_plan_tree` 会重写 plan 文件的 tasks/依赖内容，并在传入 `note` 时保存快照。
- **对话流程**：`PlanSession` 结合 PlanRepository 操作，为 `chat_routes` 提供加载/刷新/持久化能力。

## 对外接口一览

主要由 `PlanRepository` 提供以下方法：

- 计划管理：`list_plans()`、`get_plan_tree()`、`get_plan_summary()`、`create_plan()`、`delete_plan()`、`upsert_plan_tree()`
- 任务操作：`create_task()`、`update_task()`（支持上下文）、`delete_task()`、`move_task()`、`get_node()`、`subgraph()`

聊天代理可通过 `PlanSession`（`app/services/plans/plan_session.py`）对这些接口进行封装，例如 `bind`, `refresh`, `outline`, `persist_current_tree` 等。

## 示例脚本

`example/` 目录下提供了常用脚本：

- `generate_demo_plan.py`：生成带上下文的示例计划。
- `list_plans.py`：列出主库中注册的计划。
- `show_plan_tree.py`：打印指定 Plan 的节点数据及上下文字段。

运行前请确保 `DB_ROOT` 已配置或通过 `--db-root` 指定目标目录。
