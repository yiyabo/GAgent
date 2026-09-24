# BACKEND PACKAGE

## OVERVIEW
FastAPI backend organized by router/service/repository boundaries, with SQLite persistence and async service calls.

## STRUCTURE
```
app/
├── main.py              # FastAPI app + lifespan startup/shutdown
├── routers/             # HTTP/SSE/WebSocket API surfaces
├── services/            # Business logic, LLM orchestration, plans, tools, memory
├── repository/          # Raw SQL data access
├── config/              # Executor and runtime settings wrappers
├── middleware/          # Proxy/auth middleware
├── tests/               # Pytest suite rooted by pytest.ini
└── database_pool.py     # SQLite connection pool
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| App lifecycle | `main.py` | Initializes toolbox, shared LLM clients, realtime bus, stale job recovery. |
| New route | `routers/` + `routers/__init__.py` | Add module and ensure registration side effect. |
| Config | `services/foundation/settings.py` | Pydantic settings, `.env`, selected env aliases. |
| LLM client | `llm.py` | Multi-provider client and shared pools. |
| Principal/session context | `services/request_principal.py`, `services/session_context.py` | Owner/session propagation. |
| Content moderation audit | `services/moderation/` | Log-only sensitive-keyword scanning (TC260 library) on chat input and LLM output; audit JSONL in `logs/moderation.log`; toggle via `MODERATION_ENABLED`. |
| DeepThink orchestration | `services/deep_think_agent.py` + `services/deep_think/` | Facade agent class + split modules: `models` (dataclasses), `text_utils` (pure fns/env knobs), `guards` (loop guards), `synthesis` (fallback/synthesis), `protocol` (strict JSON parse), `prompts` (prompt builders), `dispatch` (tool exec/result shaping), `controller` (thin `think()` orchestration + `_native_*` stage helpers + `_NativeCycleState`), `gating` (re-export facade), `gating_probe` (probe/follow-through), `gating_finalize` (claims/finalize answers), `gating_payloads` (tool-result payloads), `gating_truth` (truth barriers), `gating_plans` (plan contract/dataset gates). Methods stay as thin wrappers on `DeepThinkAgent`. Late-bound `_dta()` namespace is kept ONLY for facade-defined display/LLM-error helpers (detect_reasoning_language, _localized_text, is_process_only_answer, sanitize_reasoning_text, build_user_visible_step, _default_deepthink_summary, _describe_exception, _classify_llm_provider_error, _build_llm_unavailable_final_answer), class-attribute reads (`_dta().DeepThinkAgent.*`), and knobs tests monkeypatch in that namespace (currently `_time_budget_*_seconds`); pure env knobs that tests do not patch are imported directly from `text_utils` (migrated: `_failure_signature_*`, `_progress_free_*`, `_default_max_consecutive_llm_failures`, `_default_fallback_timeout_seconds`, `_default_synthesis_{timeout_seconds,max_tokens}`). |
| Deliverables publishing | `services/deliverables/` | `publisher.py` is a thin facade: `DeliverablePublisher` composes `policy`/`manifest`/`file_ops`/`submit_payload`/`manuscript` mixins (siblings in the same package) and re-exports every original name, so `artifacts/projector.py` and the plan/executor import sites are unchanged. Keep `publisher.py` under 700 lines; `MANUSCRIPT_PDF_STEMS` in `policy.py` has a manual sync contract with `scripts/archive/repair_deliverable_pdf_layout.py`. |
| Database | `repository/`, `database_pool.py` | Raw SQL; row factory returns dict-like rows. |

## CONVENTIONS
- Router functions should remain thin; push business rules into `app/services/` and persistence into `app/repository/`.
- Service code should use `get_settings()` for env/config rather than direct env reads.
- Async service calls should be awaited; blocking or sync bridges are local exceptions only.
- Startup side effects belong in `main.py` lifespan or explicit scripts, not import-time surprises outside router registration.
- Tests live under `app/tests` and use shared isolation fixtures from `app/tests/conftest.py`.

## ANTI-PATTERNS
- Do not add ORM patterns beside the raw SQL repositories.
- Do not silently swallow route/tool failures with generic fallback success text.
- Do not change routing, DeepThink, or plan execution without targeted tests from `app/tests/chat`, `app/tests/plan`, or `app/tests/tools`.
- Do not read secrets into logs; `.env` contains live provider keys.
