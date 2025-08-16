# Generic Plan-Review-Execute Task Runner (FastAPI)

Read this in Chinese: [Future_Plan_cn.md](./Future_Plan_cn.md)

A production-grade Plan‑Review‑Execute agent. It follows Goal → Plan → Review → Execute with observable context orchestration, strict budgeting, and extensible tooling. This README is the English primary version.

- Backend: FastAPI (SQLite ./tasks.db by default; pluggable)
- LLM: external API (requires GLM_API_KEY) or Mock mode
- Models & data: pydantic v2

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

## Vision & Capabilities (Target State)

- Planning & Review
  - Auto-generate executable plans from a goal (configurable sections), allow human review/edit, then persist.
- Context Orchestration
  - Multi-source: dependency graph (links), TF‑IDF retrieval, manual picks; extensible to code/doc indexes.
  - Global dedup with stable priority via `PRIORITY_ORDER` for determinism.
  - Budgeting: total chars (`max_chars`), per-section cap (`per_section_max`), trimming strategy (`truncate`/`sentence`).
  - Snapshots: `save_snapshot` and `label` to persist context snapshots with metadata.
  - Observability: structured debug logs (`CTX_DEBUG`/`CONTEXT_DEBUG`/`BUDGET_DEBUG`) and auditable outputs.
- Executor
  - Idempotent execution, scheduling control, pluggable tools to extend into retrieval, code ops, scripts, etc.
- API & CLI parity
  - `/run` supports `schedule` (`bfs`|`dag`), `use_context`, and `context_options`; CLI mirrors these for automation.
- Storage & portability
  - SQLite by default; can swap to external DB. Mock LLM enables development/CI without external calls.

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
  - GET  /tasks/{task_id}/output – fetch generated output for a task

- Context APIs
  - POST /context/links – create a link { from_id, to_id, kind }
  - DELETE /context/links – delete a link
  - GET /context/links/{task_id} – returns { task_id, inbound, outbound }

  - POST /tasks/{task_id}/context/preview – returns assembled context bundle { sections, combined }
  - GET /tasks/{task_id}/context/snapshots – list context snapshots for a task
  - GET /tasks/{task_id}/context/snapshots/{label} – get a specific snapshot by label

## Global INDEX.md (Root Generator, Phase 4)

- Overview: Generates a project-wide `INDEX.md` that summarizes plans, context budget, dependencies, detailed tasks, and a changelog.
- Sections (in order): Table of Contents, Plans Overview, Context Budget, Dependency Summary, Plans, Changelog.
- Plans Overview columns: Plan, Owner, Stage, Done/Total, Last Updated.
- Dependency Summary: cycle detection and bottleneck nodes (heuristic: indegree × outdegree).
- Context Budget: displays `PRIORITY_ORDER`; note that 'index' is always budgeted first.
- Changelog: last N generations read from `<path>.history.jsonl` (newest first).

CLI

- Preview (no write): `python agent_cli.py --index-preview`
- Export to path (no history): `python agent_cli.py --index-export /path/to/INDEX.md`
- Generate and persist (append history): `python agent_cli.py --index-run-root`
- Respects `GLOBAL_INDEX_PATH` for the default output path. None of these commands call the external LLM; safe offline.

API

- GET `/index` → `{ "path": string, "content": string }` (empty string if the file does not exist)
- PUT `/index` with body `{ "content": string, "path"?: string }` → writes file and returns `{ "ok": true, "path": string, "bytes": number }`

Environment

- `GLOBAL_INDEX_PATH` sets the default INDEX.md location, e.g.:

  ```bash
  export GLOBAL_INDEX_PATH=/tmp/INDEX.md
  ```

## Scheduling (BFS vs DAG)

- Overview
  - BFS (default): execute all pending tasks in a stable order `(priority ASC, id ASC)`. With `title` set, only tasks under that plan prefix `[title]` + space are executed.
  - DAG: build a DAG from `task_links(kind='requires')`; execute in topological order with stable tie‑breaking by priority and ID.
- Dependency semantics
  - `create_link(from_id, to_id, kind='requires')` means: `to_id` depends on `from_id` (edge from → to).
  - `list_dependencies(task_id)` returns upstream (`from_id`) nodes; `requires` before `refers`, each internally ordered by `(priority, id)`.
- Cycle detection
  - DAG scheduling detects cycles (e.g., A→B→C→A) and reports a 400 with `detail.error = "cycle_detected"` plus `nodes`/`edges`/`names` diagnostics.
- Stable ordering
  - For nodes in the same layer, use `(priority ASC, id ASC)` as a deterministic order for reproducibility.
- Scope
  - With `/run` `title`, build DAG only for that plan; otherwise run globally pending tasks.
- How to call (API)
  - Top-level `schedule`: `{ "schedule": "bfs"|"dag" }`; default `bfs`. Cycles return 400.
- How to call (CLI)
  - `--schedule bfs|dag`, e.g.:
  
    ```bash
    conda run -n LLM python agent_cli.py --execute-only --title Demo --schedule dag --use-context
    ```
  

## CLI Examples

```bash
# Execute a single plan with context + budgeting
conda run -n LLM python agent_cli.py --execute-only --title Demo \
  --use-context --tfidf-k 2 --tfidf-min-score 0.15 --tfidf-max-candidates 200 \
  --max-chars 1200 --per-section-max 300 --strategy sentence \
  --save-snapshot --label demo-ctx

# Run with context (no title => scheduler runs all pending)
conda run -n LLM python agent_cli.py --use-context --tfidf-k 2 \
  --tfidf-min-score 0.2 --tfidf-max-candidates 100 --max-chars 1200

# DAG scheduling
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

## Configuration & Debugging (Environment)

- LLM
  - `GLM_API_KEY`, `GLM_API_URL` (default `https://open.bigmodel.cn/api/paas/v4/chat/completions`), `GLM_MODEL` (e.g., `glm-4-flash`)
  - `LLM_MOCK=1`: develop/test without external API
  - Retries/backoff: `LLM_RETRIES` (default 2), `LLM_BACKOFF_BASE` (seconds, default 0.5)
- Context retrieval/debug
  - `TFIDF_MAX_CANDIDATES` (default 500), `TFIDF_MIN_SCORE` (default 0.0)
  - `CTX_DEBUG` / `CONTEXT_DEBUG` / `BUDGET_DEBUG` enable structured debug logs

## Documentation
- Chinese overview: [Future_Plan_cn.md](./Future_Plan_cn.md)
- Future Plan (English): [Future_Plan.md](./Future_Plan.md)
- Phase 3 (Chinese): [Phase3_cn.md](./Phase3_cn.md)

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

## How It Works (End-to-End)

- **1) Propose a plan**
  - `app/services/planning.py` → `propose_plan_service(payload)` builds an LLM prompt, calls `app/llm.py` `LLMClient.chat()`, parses with `app/utils.py` `parse_json_obj()`, and returns `{ title, tasks }` without persisting.
- **2) Approve & persist**
  - `approve_plan_service(plan)` writes tasks to DB with name prefix `app/utils.py` `plan_prefix(title)`; prompts saved via `TaskRepository.upsert_task_input()`.
  - Tasks are grouped by plan prefix `[Title]` + space; listing uses `TaskRepository.list_plan_tasks()`.
- **3) Schedule**
  - BFS: `app/scheduler.py` `bfs_schedule()` orders pending tasks by `(priority ASC, id ASC)`.
  - DAG: `requires_dag_order(title?)` builds requires-DAG from `TaskRepository.list_links(kind='requires')`; cycles return diagnostics `{error:"cycle_detected", nodes, edges, names}` where `names` are short via `app/utils.py` `split_prefix()`.
- **4) Execute**
  - `app/executor.py` `execute_task(task, use_context, context_options)` fetches prompt (from `task_inputs` or default), optionally gathers context and applies budget, then calls LLM and persists output.
  - Context: `app/services/context.py` `gather_context()` always includes global `INDEX.md` first (path from `GLOBAL_INDEX_PATH`), then `requires`/`refers` deps, plan siblings, manual items, and optional TF‑IDF retrieval.
  - Budget: `app/services/context_budget.py` `apply_budget(bundle, max_chars, per_section_max, strategy)` respects `PRIORITY_ORDER = ("index","dep:requires","dep:refers","retrieved","sibling","manual")` for deterministic trimming.
  - Snapshots: if requested, `TaskRepository.upsert_task_context()` stores `combined`, `sections`, and `meta` under `task_contexts` with labels.
- **5) Assemble outputs**
  - `GET /plans/{title}/assembled` uses `TaskRepository.list_plan_outputs()` to order sections by `(priority, id)` and join contents.
- **6) Root INDEX.md**
  - `app/services/index_root.py` generates a global `INDEX.md` (plans overview, context budget, dependency summary, plans, changelog). CLI: preview/export/run-root.

## Project Logic & Data Flow

- **Data model**
  - Tables: `tasks`, `task_inputs`, `task_outputs`, `task_links`, `task_contexts` (created on demand by repository helpers).
  - Grouping: task names prefixed with `[Title]` + space using `app/utils.py` `plan_prefix()`; parsing with `split_prefix()`.
- **Flow**
  - Propose → `propose_plan_service()` → LLM → JSON parsed → review/edit → Approve → `approve_plan_service()` → rows in `tasks`/`task_inputs`.
  - Link deps via `/context/links` → records in `task_links`.
  - Schedule (BFS/DAG) → for each task → `execute_task()` → `gather_context()` → `apply_budget()` → `LLMClient.chat()` → `task_outputs`.
  - Optional snapshots: `upsert_task_context()` → `task_contexts` (retrievable via `/tasks/{id}/context/...`).
  - Global index path: `GLOBAL_INDEX_PATH` controls `INDEX.md`; context assembly always prioritizes `'index'`.
- **Determinism & errors**
  - Stable ordering everywhere: scheduler `(priority,id)`, plan outputs `(priority,id)`, budgeting `PRIORITY_ORDER`.
  - DAG cycles: 400 with `detail.error = "cycle_detected"` plus `nodes`/`edges`/`names`.

- **Extensibility**

  - Replace `TaskRepository` and `LLMProvider` via DI. Add new context sources or budgeting strategies without breaking existing flows.

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
