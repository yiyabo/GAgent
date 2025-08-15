# Future Plan: Context-Aware LLM Task Runner Roadmap

## 0. Overview
- Vision: Build a context-aware, dependency-sensitive task runner where a large goal is decomposed into big tasks and finally into minimal executable units (MEUs). Each MEU executes with the right context, curated from a graph of related tasks and documents, with optional human guidance.
- Key themes: Graph-based context, Human-in-the-loop, Deterministic scheduling, Reproducible runs, Extensible architecture.

## 1. Current State (Baseline)
- Plan grouping via name prefix: `utils.plan_prefix()` and `utils.split_prefix()`.
- Task I/O persisted: `task_inputs`, `task_outputs`.
- Execution: `executor.execute_task()` reads only the task's own input as prompt; no dependency/context assembly.
- Scheduling: `scheduler.bfs_schedule()` orders by `(priority, id)`; no dependency awareness.
- Services: Planning service proposes/approves plans and seeds tasks with prompts.

## 2. Phase 0 (Completed)
- Remove module-level repo wrappers; use `default_repo` instance everywhere.
- Introduce `app/utils.py` and deduplicate helpers (`plan_prefix`, `split_prefix`, `parse_json_obj`).
- Clean caches and improve `.gitignore`. Tests pass. Repo pushed to GitHub.

## 3. Phase 1 — Context Graph Foundation (MVP)
Goal: Introduce a lightweight directed graph of task relationships and expose basic APIs to manage and query links. Enable minimal context assembly before execution.

- Big Tasks
  1) Data model: add `task_links` table (from_id, to_id, kind).
  2) Repository: CRUD for links and basic graph queries.
  3) Service: `context.gather_context(task_id, ...)` to assemble a context bundle.
  4) API: endpoints to add/remove/list links and preview context.
  5) Executor: optionally include the gathered context in prompts.

- Small Tasks
  - DB migration: create `task_links(from_id INTEGER, to_id INTEGER, kind TEXT, PRIMARY KEY(from_id,to_id,kind))`.
  - Repo API (extend `TaskRepository`):
    - `create_link(from_id: int, to_id: int, kind: str) -> None`
    - `delete_link(from_id: int, to_id: int, kind: str) -> None`
    - `list_links(from_id: int | None = None, to_id: int | None = None, kind: str | None = None) -> List[Dict]`
    - `list_dependencies(task_id: int) -> List[Dict]`  (kinds: `requires`, `refers`)
  - `app/services/context.py`:
    - `gather_context(task_id: int, include_plan: bool = True, include_deps: bool = True, k: int = 5, manual: List[int] | None = None) -> Dict`
    - Assemble: plan siblings (short), dependency nodes (full/short), manual nodes.
  - API routes (e.g. `app/main.py` or `app/routes/context.py`):
    - `POST /context/links` (create), `DELETE /context/links` (delete), `GET /context/links/{task_id}` (list linked)
    - `POST /tasks/{task_id}/context/preview` -> returns the assembled bundle
  - `app/executor.py`:
    - Optional flag to include context (`use_context=True`), format context section before the task prompt.
  - Tests: FakeRepo implements link methods; unit tests for gatherer and endpoints.

- Deliverables
  - Minimal graph editing and read APIs; basic context bundle used by executor.
  - No embeddings yet, deterministic behavior.

- Acceptance Criteria
  - Able to link tasks as `requires` or `refers` and verify via APIs.
  - Executor includes a deterministic context section when enabled.
  - Tests: link CRUD, gather_context, and one E2E run using linked context.

## 4. Phase 2 — Context Selection, Budgeting, and Summarization
Goal: Choose the most relevant context within a token budget. Add optional semantic retrieval.

- Big Tasks
  1) Add a token/char budget manager for context assembly.
  2) Add summarization passes for long outputs.
  3) Optional semantic retrieval (TF-IDF baseline, embeddings later).
  4) Cache context snapshots for reproducibility.

- Small Tasks
  - `context.budget.py`: greedy allocator by priority tiers (dependencies > plan siblings > semantic hits > manual extras).
  - Summarizer implementations: simple heuristics first; optional LLM summarizer.
  - TF-IDF retriever across `task_outputs` as a zero-dependency baseline.
  - Optional embeddings layer and index (e.g., `sentence-transformers`).
  - Persist `task_contexts(task_id, compiled_context, created_at)`.

- Deliverables
  - Configurable context policy with budget and summarization.
  - Deterministic selection path (given a fixed seed and no LLM summarization).

- Acceptance Criteria
  - Given a budget, gather_context returns a bundle within limit.
  - Summaries are produced for oversized items; selection order is tested.

## 5. Phase 3 — Dependency-Aware Scheduling
Goal: Only schedule tasks whose `requires` dependencies are satisfied; detect and report cycles.

- Big Tasks
  1) Build DAG from `task_links(kind='requires')`.
  2) Topological scheduling with stable tie-breakers (priority, id).
  3) Cycle detection and reporting; manual override hooks.

- Small Tasks
  - New scheduler: `requires_dag_schedule()`.
  - Integrate into `/run` with a flag (`strategy='dag'|'bfs'`).
  - Tests: DAG order, cycle detection, partial completion.

- Deliverables
  - Deterministic, dependency-aware execution.

- Acceptance Criteria
  - Tasks with unmet dependencies are not scheduled.
  - Cycles are identified with actionable diagnostics.

## 6. Phase 4 — Root Task and Index Document
Goal: Treat the root task as executable; generate a high-level index (INDEX.md) and global conventions.

- Big Tasks
  1) Add a root plan operation to scaffold project structure and global rules.
  2) Persist `INDEX.md` (as a task output) and mark as global context source.

- Small Tasks
  - Planning prompt templates for root task.
  - `gather_context`: always include INDEX.md at highest priority.
  - API to fetch and update INDEX.md.

- Deliverables
  - Executable root task producing an index document used across the plan.

- Acceptance Criteria
  - Runs that include root context behave consistently with the index rules.

## 7. Phase 5 — Human-in-the-Loop Controls
Goal: Allow users to guide context and dependencies.

- Big Tasks
  1) Manual link management UI hooks (API-level first).
  2) Context preview, prune, and pin.

- Small Tasks
  - `POST /tasks/{id}/context/preview` returns candidate bundle with flags to pin/unpin.
  - `approve_context` endpoint applies manual overrides.

- Deliverables
  - Transparent, auditable context assembly with human adjustments.

- Acceptance Criteria
  - Operators can add/remove references and re-run deterministically.

## 8. Phase 6 — Observability and Run Reproducibility
Goal: Make runs traceable and reproducible.

- Big Tasks
  1) Structured logs for prompt, context bundle, model, and outputs.
  2) Persist run records and context snapshot per execution.

- Small Tasks
  - Add `runs` table storing: task_id, started_at, finished_at, status, used_context_id, model/config.
  - Request/response logging with redactable fields.

- Acceptance Criteria
  - Any output can be traced back to its context and parameters.

## 9. Phase 7 — Quality, Tooling, and CI/CD
Goal: Keep quality high as the system grows.

- Big Tasks
  1) Test coverage expansion (services, scheduler, context selection).
  2) Lint/format (ruff/black), type checks, pre-commit hooks.
  3) GitHub Actions: test on push; optional build/upload artifacts.

- Acceptance Criteria
  - Green CI on main; minimum coverage target (e.g., 80%).

## 10. Data Model (Draft)
- `tasks(id, name, status, priority)`
- `task_inputs(task_id, prompt)`
- `task_outputs(task_id, content)`
- `task_links(from_id, to_id, kind)` — kinds: `requires`, `refers`, `duplicates`, `relates_to`
- Optional: `task_contexts(id, task_id, payload, created_at)`
- Optional: `runs(id, task_id, used_context_id, started_at, finished_at, status, model, config)`

## 11. API Sketch (New/Updated)
- Links
  - POST `/context/links` { from_id, to_id, kind }
  - DELETE `/context/links` { from_id, to_id, kind }
  - GET `/context/links/{task_id}` -> { incoming: [...], outgoing: [...] }
- Context
  - POST `/tasks/{task_id}/context/preview` { options } -> bundle
  - POST `/tasks/{task_id}/context/approve` { pins, excludes } -> lock-in overrides
- Run
  - POST `/run` { title?, strategy?, use_context?, options? }

## 12. Execution Prompt Template (Draft)
```
You will complete the following task:
Task: {task_name}

Context (ordered by priority):
{for each item}
- Source: {task_id or INDEX.md} | Type: {requires|refers|plan|manual}
  Excerpt: {trimmed or summarized content}

Instructions:
- Use information faithfully; cite sources by task id.
- If context conflicts, prioritize requires > plan index > manual > refers.
- Keep output concise and actionable.
```

## 13. Big/Small Task Breakdown by Phase
- Phase 1 (Graph Foundation)
  - Big: DB schema + Repo + Context service + API + Executor integration
  - Small: migration, CRUD link methods, gatherer, endpoints, tests
- Phase 2 (Selection & Budget)
  - Big: Budget manager + Summarizer + (Optional) retriever + Context snapshot
  - Small: TF-IDF baseline, config flags, tests
- Phase 3 (DAG Scheduler)
  - Big: requires-DAG + topo scheduler + cycle detection
  - Small: strategy flag, unit/E2E tests
- Phase 4 (Root & Index)
  - Big: root task flow + INDEX.md integration
  - Small: templates, inclusion policy, tests
- Phase 5 (HITL)
  - Big: preview/approval workflow
  - Small: pin/unpin, manual edits, tests
- Phase 6 (Observability)
  - Big: run records + structured logging
  - Small: redaction, tracing, tests
- Phase 7 (Quality/CI)
  - Big: CI + coverage + hooks

## 14. Migration & Compatibility
- Non-breaking: Phases 1–2 add tables/APIs; default behavior remains current unless enabled.
- Migrations: idempotent `CREATE TABLE IF NOT EXISTS` with versioning table.

## 15. Risks & Mitigations
- Scope creep: lock each phase with clear ACs and tests.
- Token blowup: enforce budgets and summaries.
- Graph misuse: validate kinds and detect cycles early.
- Reproducibility: snapshot contexts and configs per run.

## 16. Rough Timeline (Adjustable)
- Phase 1: 1–2 days
- Phase 2: 2–3 days
- Phase 3: 1–2 days
- Phase 4: 1 day
- Phase 5: 1 day
- Phase 6–7: 1–2 days each

## 17. Open Questions
- Context limits per model? (configurable by provider)
- Embedding provider choice and privacy constraints?
- Human override persistence model (per run vs per task)?
- Multi-plan references weighting?
