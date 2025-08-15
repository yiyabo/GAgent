# Generic Plan-Review-Execute Task Runner (FastAPI)

A lightweight FastAPI service that turns a high-level goal into an approved plan of tasks, then executes them via an LLM.

- DB: SQLite at ./tasks.db
- LLM: external API via environment (GLM_API_KEY required)
- Models: pydantic v2

## Quickstart

1) Install deps (LLM env)

```bash
conda run -n LLM python -m pip install -r requirements.txt
```

2) Set environment

```bash
export GLM_API_KEY=your_key_here
# or use mock mode (no API key needed)
# export LLM_MOCK=1
# optional
# export GLM_API_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions
# export GLM_MODEL=glm-4-flash
```

3) Run server

```bash
conda run -n LLM python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

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
  - Body optional: { "title"?: string, "use_context"?: boolean } – title filters pending tasks for that plan; use_context includes assembled context in the prompt (default: false).
- GET /plans/{title}/assembled
  - Assemble completed outputs for a plan ordered by priority
- Additional:
  - POST /tasks – create a single pending task (for advanced/manual usage)
  - GET  /tasks – list tasks
  - GET  /tasks/{task_id}/output – fetch generated output for a task

- Context (Phase 1)
  - POST /context/links – create a link { from_id, to_id, kind }
  - DELETE /context/links – delete a link
  - GET /context/links/{task_id} – returns { task_id, inbound, outbound }
  - POST /tasks/{task_id}/context/preview – returns assembled context bundle { sections, combined }

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
- Tasks are grouped by name prefix: `[<title>] `. No schema change needed.
- Legacy, report-specific endpoints have been removed to keep the app generic.

## Documentation
- Phase 1 实施说明（中文）: [Phase1.md](./Phase1.md)
- 未来规划（中文）: [Future_Plan_cn.md](./Future_Plan_cn.md)
- Future Plan (English): [Future_Plan.md](./Future_Plan.md)

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

