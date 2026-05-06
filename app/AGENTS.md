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
