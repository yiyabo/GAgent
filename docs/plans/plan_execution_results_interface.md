# 计划执行结果可视化接口设计（前后端方案）

## 背景与目标
- 诉求：前端需要查看“执行计划/任务”的输出内容（LLM 执行产物），并跟踪执行进度与统计。
- 目标：
  - 后端提供统一的查询接口，返回任务的执行结果与状态聚合。
  - 前端在触发执行后，能通过轮询或事件流及时获取结果并展示。
  - 与现有结构化对话与计划存储方案兼容，无需新增表结构。

## 数据与存储
- 存储位置：每个 plan 的 SQLite 中的 `tasks` 表。
  - `tasks.status`：任务当前状态（`pending|running|completed|failed|skipped`）。
  - `tasks.execution_result`：字符串化 JSON，来自执行 LLM 的标准输出载荷：
    - `status`: `success|failed|skipped`（执行器在持久化前会标准化为任务状态）
    - `content`: 主要执行结果文本
    - `notes`: 附注列表
    - `metadata`: 附加信息（模型名、时长等）
  - `tasks.updated_at`：任务最近更新时间（用于增量拉取）。
- 不新增表；利用现有 `PlanRepository.get_plan_tree()` 载入 tasks 与依赖关系。

## 后端接口设计

1) 获取计划内任务执行结果列表
- 路径：`GET /plans/{plan_id}/results`
- 入参：
  - `only_with_output`（bool，默认 true）：仅返回有 `execution_result` 的任务。
- 返回：
  - `plan_id`: int
  - `total`: 数量
  - `items`: TaskResultItem[]
    - `task_id`: int
    - `name`: string
    - `status`: string（任务状态）
    - `content`: string（从 `execution_result.content` 提取）
    - `notes`: string[]
    - `metadata`: object
    - `raw`: object（原始 JSON 载荷，可选）
- 语义：用于“结果面板”一次性拉取，可配合分页/分段在未来扩展。

2) 获取单任务执行结果
- 路径：`GET /tasks/{task_id}/result`
- 入参：`plan_id`（query）：归属计划 ID
- 返回：TaskResultItem（同上）。
- 语义：列表点击“查看详情”时拉取，或任务节点的详情页使用。

3) 获取计划执行统计汇总
- 路径：`GET /plans/{plan_id}/execution/summary`
- 返回：
  - `plan_id`: int
  - `total_tasks`: 总节点数
  - `completed|failed|skipped|running|pending`: 各状态数量
- 语义：用于进度条、统计卡片等总览视图。

4) 查询聊天动作执行状态（已存在）
- 路径：`GET /chat/actions/{tracking_id}`
- 返回：后台动作（如 `execute_plan`）的状态与结果摘要。
- 建议：当动作为 `execute_plan` 时，`result` 中包含执行的 `ExecutionSummary` 结构（执行器已有），前端在动作完成后切换至结果拉取接口（1/3）。

5) 增量拉取（可选增强）
- 路径：`GET /plans/{plan_id}/results/incremental`
- 入参：`cursor`（ISO 时间戳或数字版本）
- 返回：同 1）但仅包含 `updated_at > cursor` 的任务，并附带 `next_cursor`。
- 语义：在结果较多或高频更新时，前端以较低代价轮询增量。

6) 事件推送（可选增强）
- 路径：`GET /plans/{plan_id}/events`（SSE）或 `/ws/plans/{plan_id}`（WebSocket）
- 事件：`task_status_changed`、`task_result_updated`、`execution_finished` 等。
- 语义：前端接入事件流，实时刷新；网络环境受限可回退为轮询。

## 前端集成流程

- 触发执行：通过结构化对话或按钮触发 `execute_plan`；后端立即返回 `tracking_id`。
- 轮询动作状态：`GET /chat/actions/{tracking_id}`，直到 `status in {completed, failed}`；
  - 若完成：读取 `plan_id`，并跳转/刷新计划视图。
- 拉取结果与统计：
  - `GET /plans/{plan_id}/execution/summary` 显示总体进度；
  - `GET /plans/{plan_id}/results?only_with_output=true` 渲染“结果列表面板”；
  - 点击某行任务，用 `GET /tasks/{task_id}/result?plan_id=...` 展示详情。
- 更新策略：
  - 简单模式：每 2–5s 轮询 `summary` 与 `results`；
  - 增量模式：使用 `results/incremental?cursor=...`；
  - 实时模式：连接 SSE/WS 事件流，推送后对局部行进行乐观更新。

## 前端 UI 建议
- 左侧：计划树（Task Tree）显示任务名与状态点（颜色或图标）。
- 右侧：
  - 顶部统计卡片（总数、已完成、失败、跳过、运行中、待处理）。
  - 结果列表（仅显示有输出的任务，支持过滤/搜索）。
  - 详情抽屉/面板（task 基本信息、依赖、`content/notes/metadata` 展示）。
- 辅助：
  - 过滤器：按状态、关键字、父子范围过滤。
  - 导出：将 `results` 导出为 Markdown/JSON。

## 错误与边界
- 无输出：`only_with_output=true` 时结果为空，前端提示“尚无执行结果”。
- 部分失败：`summary.failed > 0` 时突出警示，支持“重试失败任务”操作（后续可扩展）。
- 循环依赖/非法依赖：已在写入侧过滤；若执行时仍报错，`execute_plan` 的动作状态与错误会在 `/chat/actions/{tracking_id}` 中体现，前端应展示错误信息。
- 大计划：优先使用增量拉取或事件流，避免一次返回过大 payload。

## 安全与权限（按需）
- 最小化返回：默认仅返回内容概要；`raw` 字段仅在详情里请求。
- 限流与鉴权：保护 `/results` 与事件流接口，避免恶意刷新。

## 与现有系统的契合
- 存储：沿用 `tasks.status` 与 `tasks.execution_result` 字段；无数据库迁移。
- 对话：结构化动作执行 `execute_plan` 完成后，前端切换至本方案接口获取结果。
- 兼容：前端若未接入新接口，仍可通过 `/chat/actions/{tracking_id}` 查看结果摘要，但不具备完整结果列表视图。

## 示例载荷（简化）
- GET `/plans/15/execution/summary`
```
{
  "plan_id": 15,
  "total_tasks": 50,
  "completed": 28,
  "failed": 2,
  "skipped": 1,
  "running": 3,
  "pending": 16
}
```
- GET `/plans/15/results?only_with_output=true`
```
{
  "plan_id": 15,
  "total": 3,
  "items": [
    {
      "task_id": 4,
      "name": "Development of Machine Learning Models",
      "status": "completed",
      "content": "Trained RF, XGBoost, GNN; best AUC=0.91",
      "notes": ["k=5 CV"],
      "metadata": {"model": "executor-llm", "duration_sec": 8.2}
    },
    {
      "task_id": 5,
      "name": "Experimental Validation Design",
      "status": "failed",
      "content": "Missing lab access context",
      "notes": ["all attempts failed"]
    }
  ]
}
```
- GET `/tasks/4/result?plan_id=15`
```
{
  "task_id": 4,
  "name": "Development of Machine Learning Models",
  "status": "completed",
  "content": "Trained RF, XGBoost, GNN; best AUC=0.91",
  "notes": ["k=5 CV"],
  "metadata": {"model": "executor-llm", "duration_sec": 8.2},
  "raw": {
    "status": "success",
    "content": "Trained RF, XGBoost, GNN; best AUC=0.91",
    "notes": ["k=5 CV"],
    "metadata": {"model": "executor-llm", "duration_sec": 8.2}
  }
}
```

## 实施步骤（给后端/前端）
- 后端
  - [ ] 新增三个接口：`GET /plans/{id}/results`、`GET /tasks/{task_id}/result`、`GET /plans/{id}/execution/summary`。
  - [ ] 可选：增量拉取与事件流接口。
  - [ ] 在 `execute_plan` 完成后，保证任务的 `status/execution_result` 已持久化（执行器已实现）。
- 前端
  - [ ] 触发执行后轮询 `/chat/actions/{tracking_id}`，动作完成后切换到本接口拉取数据。
  - [ ] 结果展示：计划树 + 结果列表 + 详情抽屉；支持过滤与搜索。
  - [ ] 可选：接入增量拉取或事件流，提升实时性与性能。

## 兼容性与后续拓展
- 短期：前后端先落地 3 个只读接口，快速实现结果可视化。
- 中期：补充增量与事件流，优化大计划性能。
- 长期：引入“重试失败任务”、“按子图执行”、“导出报告”等能力。

