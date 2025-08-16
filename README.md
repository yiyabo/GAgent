# Generic Plan-Review-Execute Task Runner (FastAPI)

一个面向生产的 Plan‑Review‑Execute 智能体服务。以“目标 → 计划 → 审阅 → 执行”为主线，提供可观测的上下文编排、预算管理与可扩展的工具能力。本文档以“最终理想形态”为准，当前实现将持续对齐。

- 后端：FastAPI（默认存储 SQLite: ./tasks.db，可插拔）
- LLM：外部 API（需 GLM_API_KEY）与 Mock 模式
- 模型与数据：pydantic v2

## Quickstart

1. Install deps (LLM env)

```bash
conda run -n LLM python -m pip install -r requirements.txt
```

1. Set environment

```bash
export GLM_API_KEY=your_key_here
# or use mock mode (no API key needed)
# export LLM_MOCK=1
# optional
# export GLM_API_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions
```

1. Run server

```bash
conda run -n LLM python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## 愿景与目标能力（最终理想形态）

- 规划与审阅
  - 基于目标自动生成可执行计划（sections 可调），支持人类审阅与修改后入库。
- 上下文编排（Context Orchestration）
  - 多源融合：依赖图（links）、TF‑IDF 检索、手动选择，未来可扩展至代码/文档索引。
  - 全局去重与稳定优先级：按 `PRIORITY_ORDER` 排序，保证确定性。
  - 预算管理：总字数（max_chars）、分段上限（per_section_max）、裁剪策略（truncate/sentence）。
  - 快照留存：`save_snapshot` 与 `label` 追踪每次执行的上下文快照与元数据。
  - 可观测性：结构化调试日志（`CTX_DEBUG`/`CONTEXT_DEBUG`/`BUDGET_DEBUG`）与可审计输出。
- 执行器
  - 幂等执行、顺序/调度控制、可插拔工具（Tooling）以扩展到检索、拉取代码、执行脚本等。
- API 与 CLI 一致性
  - `/run` 接收 `schedule`（`bfs`|`dag`）与 `use_context`、`context_options`；CLI 提供等效参数开关（`--schedule`）便于批处理与自动化。
  - 存储与可移植性
  - 默认 SQLite，可平滑替换为外部数据库；Mock LLM 便于在开发/CI 环境运行。

## Core Endpoints

- POST /plans/propose
  - Body: { "goal": string, "title"?: string, "sections"?: number, "style"?: string, "notes"?: string }
  - Reply: { "title": string, "tasks": [ { name, prompt, priority } ] }
- POST /plans/approve
  - Body: plan JSON from /plans/propose (you can edit before approving)
  - Effect: persists tasks as pending with name prefix "[title] "
- GET /plans
  - List existing plan titles inferred from task name prefixes
- GET /plans/{title}/tasks
  - List tasks for a plan (id, name, short_name, status, priority)
- POST /run
  - Body optional:
    - `{ "title"?: string, "schedule"?: "bfs"|"dag", "use_context"?: boolean, "context_options"?: { "include_deps"?: boolean, "include_plan"?: boolean, "tfidf_k"?: number, "tfidf_min_score"?: number, "tfidf_max_candidates"?: number, "max_chars"?: number, "per_section_max"?: number, "strategy"?: "truncate"|"sentence", "save_snapshot"?: boolean, "label"?: string } }`
    - 默认 `bfs`；`dag` 可用。若检测到依赖环（cycle），返回 400，`detail.error = "cycle_detected"`，并包含 `nodes`/`edges`/`names`。
    - Example:

      ```json
      {
        "title": "Gene Editing Whitepaper",
        "schedule": "dag",
        "use_context": true,
        "context_options": {
          "include_deps": true,
          "include_plan": true,
          "tfidf_k": 2,
          "tfidf_min_score": 0.15,
          "tfidf_max_candidates": 200,
          "max_chars": 1200,
          "per_section_max": 300,
          "strategy": "sentence",
          "save_snapshot": true,
          "label": "exp-ctx"
        }
      }
      ```

    - 错误（DAG 检测到环）

      ```json
      {
        "detail": {
          "error": "cycle_detected",
          "nodes": [1, 2, 3],
          "edges": [{"from": 1, "to": 2}, {"from": 2, "to": 3}, {"from": 3, "to": 1}],
          "names": {"1": "A", "2": "B", "3": "C"}
        }
      }
      ```

- GET /plans/{title}/assembled
  - Assemble completed outputs for a plan ordered by priority
- Additional:
  - POST /tasks – create a single pending task (for advanced/manual usage)
  - GET  /tasks – list tasks
  - GET  /tasks/{task_id}/output – fetch generated output for a task

- Context APIs
  - POST /context/links – create a link { from_id, to_id, kind }
  - DELETE /context/links – delete a link
  - GET /context/links/{task_id} – returns { task_id, inbound, outbound }
  - POST /tasks/{task_id}/context/preview – returns assembled context bundle { sections, combined }
  - GET /tasks/{task_id}/context/snapshots – list context snapshots for a task
  - GET /tasks/{task_id}/context/snapshots/{label} – get a specific snapshot by label

## 调度策略（BFS vs DAG）

- **概览**
  - **BFS（默认）**：按 `priority ASC, id ASC` 的稳定顺序执行所有 pending 任务；当传入 `title` 时，仅对该计划前缀（`[title] `）下的 pending 任务执行。
  - **DAG（已支持）**：从 `task_links(kind='requires')` 构建有向无环图；按拓扑顺序执行，保持优先级与 ID 的稳定性作为并列项的确定性 tie-breaker。
- **依赖语义**
  - `create_link(from_id, to_id, kind='requires')` 表示：`to_id` 任务“依赖”`from_id`（即边 from → to）。
  - 依赖收集接口 `list_dependencies(task_id)` 会返回指向该任务的上游（`from_id`）节点，先 `requires` 再 `refers`，且各自内部按 `(priority, id)` 排序。
- **环检测**
  - DAG 调度会检测环（例如 A→B→C→A），并报告包含环的任务集合与边信息，以便人工处理或提供覆盖钩子。
- **稳定次序**
  - 对于同一层（无依赖或已满足依赖）的节点，使用 `(priority ASC, id ASC)` 作为确定性顺序，保证可复现。
- **作用域**
  - 当 `/run` 传入 `title` 时，仅在该计划的任务子集内构建 DAG 并调度；否则对全局 pending 任务执行。
- **调用方式（API）**
  - 顶层 `schedule` 字段：`{"schedule":"bfs"|"dag"}`；未提供时默认 `bfs`。若检测到环返回 400。
- **调用方式（CLI）**
  - `--schedule bfs|dag`，例如：

    ```bash
    conda run -n LLM python agent_cli.py --execute-only --title Demo --schedule dag --use-context
    ```


## CLI 用法示例

```bash
# 仅执行某个计划并启用上下文与预算
conda run -n LLM python agent_cli.py --execute-only --title Demo \
  --use-context --tfidf-k 2 --tfidf-min-score 0.15 --tfidf-max-candidates 200 \
  --max-chars 1200 --per-section-max 300 --strategy sentence \
  --save-snapshot --label demo-ctx

# 普通执行并启用上下文（不指定 title 将按调度执行 pending）
conda run -n LLM python agent_cli.py --use-context --tfidf-k 2 \
  --tfidf-min-score 0.2 --tfidf-max-candidates 100 --max-chars 1200

# DAG 调度（已支持）
conda run -n LLM python agent_cli.py --execute-only --title Demo \
  --schedule dag --use-context --tfidf-k 2 --tfidf-min-score 0.1
```

## Example (curl)

```bash
# 1) Propose a plan
curl -s -X POST http://127.0.0.1:8000/plans/propose \
  -H "Content-Type: application/json" \
  -d '{"goal":"Write a short whitepaper on gene editing"}'

# 2) Approve the returned plan (edit as needed), then persist
# Save previous response as plan.json and run:
# curl -s -X POST http://127.0.0.1:8000/plans/approve -H "Content-Type: application/json" --data-binary @plan.json

# 3) Execute just this plan
curl -s -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{"title":"Gene Editing Whitepaper"}'

# 4) Assemble final output
curl -s http://127.0.0.1:8000/plans/Gene%20Editing%20Whitepaper/assembled
```

## Notes
- The service requires GLM_API_KEY; requests to the LLM may fail if unset.
- Tasks are grouped by name prefix: `[<title>]`. No schema change needed.
- Legacy, report-specific endpoints have been removed to keep the app generic.

## 配置与调试（Environment）

- LLM 相关
  - `GLM_API_KEY`, `GLM_API_URL`（默认 `https://open.bigmodel.cn/api/paas/v4/chat/completions`）, `GLM_MODEL`（如 `glm-4-flash`）
  - `LLM_MOCK=1`：开启后可在无外部 API 状态下开发/测试
  - `LLM_RETRIES`（默认 2）, `LLM_BACKOFF_BASE`（秒，默认 0.5）：LLM 客户端重试与指数退避
- 上下文检索/调试
  - `TFIDF_MAX_CANDIDATES`（默认 500）、`TFIDF_MIN_SCORE`（默认 0.0）
  - `CTX_DEBUG`/`CONTEXT_DEBUG`/`BUDGET_DEBUG`：开启结构化调试日志

## Documentation
- 中文概览（理想形态）：见本 README
- 未来规划（中文）: [Future_Plan_cn.md](./Future_Plan_cn.md)
- Future Plan (English): [Future_Plan.md](./Future_Plan.md)
- Phase 3 (中文): [Phase3_cn.md](./Phase3_cn.md)

## Architecture
- **Interfaces** (`app/interfaces/__init__.py`)
  - `LLMProvider` (chat, ping, config)
  - `TaskRepository` (task CRUD/query)
- **LLM client** (`app/llm.py`)
  - Implements `LLMProvider`
  - Supports mock mode via `LLM_MOCK`
- **Repository** (`app/repository/tasks.py`)
  - `SqliteTaskRepository` implements `TaskRepository`
  - Module-level functions delegate to `default_repo` for backward compatibility
- **Services** (`app/services/planning.py`)
  - Business logic for plan propose/approve with dependency injection (DI)
- **Scheduler/Executor** (`app/scheduler.py`, `app/executor.py`)
  - Scheduler queries repository for pending tasks; executor calls LLM via `get_default_client()` and persists outputs
- **API** (`app/main.py`)
  - FastAPI app uses Lifespan to init DB

## Mock Mode (no external LLM)
Enable to develop/test without a real API key. Deterministic outputs; `ping()` always true; `config()` reflects mock.

```bash
export LLM_MOCK=1
# now run the server or tests
```

## Testing
Run tests (uses temp SQLite DB and mock LLM; no external calls):

```bash
conda run -n LLM python -m pip install -U pytest  # if needed
conda run -n LLM python -m pytest -q
```

Coverage (optional):

```bash
conda run -n LLM python -m pip install -U pytest-cov
conda run -n LLM python -m pytest --cov=app --cov-report=term-missing
```

## Lifespan
Startup has migrated from `@app.on_event("startup")` to FastAPI Lifespan for forward compatibility.

