以下清单列出了数据库层需要覆盖的关键测试场景，可对照现有 `test/test_plan_repository_*.py`、`test/test_task_contexts.py` 等文件补全/验证。

## 1. 主库（plan_registry.db）

- **计划索引**：`create_plan` 会写入 `plans` 记录并生成 `plan_db_path`；`delete_plan` 会同步删除记录与对应 `plan_{id}.sqlite` 文件。
- **会话表兼容**：`chat_sessions`、`chat_messages` 模式未改动，聊天保存/查询时能读写 metadata。

## 2. 每计划独立数据库（plan_{id}.sqlite）

- **初始化**：`initialize_plan_database` 创建 `tasks`（含 context 字段）、`task_dependencies`、`snapshots`、`plan_meta` 等表并写入元信息。
- **计划读取**：`get_plan_tree`/`list_plans` 可恢复完整 PlanTree，节点包含 `instruction`、`metadata`、`dependencies`、`context_*`、`execution_result` 等字段。
- **任务 CRUD**：
  - `create_task` 根据父节点与 position 正确写入 `path`、`depth`；
  - `update_task` 支持名称、说明、metadata、context、执行结果及依赖更新；
  - `move_task` 重写父节点、路径、位置；
  - `delete_task` 级联删除子树；
  - `get_subgraph` 返回指定节点局部树。
- **依赖管理**：`task_dependencies` 可去重、校验自引用/祖先循环，删除或更新任务时同步清理依赖。
- **执行结果**：`execution_result` 支持结构化 JSON，`PlanRepository` 可解析为内容、备注、metadata（在 `/plans/{id}/results` 中验证）。
- **上下文字段**：`context_combined`、`context_sections`、`context_meta` 与节点同存同取，`test_task_contexts.py` 已覆盖读写。
- **批量持久化**：`upsert_plan_tree` 同步更新主库 metadata、plan 文件任务/依赖，并在提供 `note` 时写入 `snapshots`。

## 3. PlanRepository 辅助逻辑

- `_touch_plan` / `update_plan_metadata` 会刷新主库 `updated_at`，`PlanSummary.task_count` 与 plan 文件保持一致。
- `PlanRepository.list_plans()` 在 SQLite 文件缺失时抛出清晰异常。
- `PlanRepository.get_plan_tree()` 对缺失计划/节点给出 `ValueError` 并被 REST 层转为 404。

## 4. Chat 相关持久化

- `chat_routes._save_message` 将 `metadata` 写入 `chat_messages`；`GET /chat/history` 返回的结构中 `metadata` 可还原 `tool_results` 等信息。
- `recent_tool_results` 在 `chat_routes` 中保持最近 5 条记录，便于 LLM prompt 引用（可通过结构化代理单测验证）。
