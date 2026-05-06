# BACKEND TESTS

## OVERVIEW
Pytest suite covers backend units, chat routing/protocol, plan DAG execution, tools, integration, smoke, and external-service boundaries.

## STRUCTURE
```
app/tests/
├── chat/          # request routing, DeepThink protocol, fallback policy
├── plan/          # DAG execution, artifacts, status resolver, deliverables
├── tools/         # tool executor, PhageScope, file/security semantics
├── unit/          # focused service/repository/auth/session tests
├── integration/   # real app integration with isolated local deps
└── conftest.py    # isolated env, TestClient, terminal manager, mock LLM fixtures
```

## WHERE TO LOOK
| Change Area | Tests |
|-------------|-------|
| Routing/tier changes | `chat/test_request_tier_routing.py` |
| DeepThink protocol | `chat/test_deep_think_strict_protocol.py` |
| No fallback guarantees | `chat/test_no_fallback_policy.py` |
| Plan dependencies/artifacts | `plan/test_plan_executor_deps.py` |
| Task verification | `tools/test_task_verification.py` |
| Tool execution semantics | `tools/test_execution_semantics_regressions.py` |
| File operation safety | `tools/test_file_operations_security.py` |

## CONVENTIONS
- `pytest.ini`: `testpaths=app/tests`, `asyncio_mode=auto`, `--strict-markers`.
- Markers: `integration`, `prod_smoke`, `external`, `timeout`, `asyncio`.
- PhageScope tests must mock external API calls; never hit the live API.
- Use isolated fixtures rather than real workspace state for DB/filesystem tests.
- Naming: `test_<area>_<behavior>.py` and `test_<action>_<result>()`.

## COMMANDS
```bash
pytest app/tests/ -v
pytest -m "not external" -v
pytest app/tests/chat/test_request_tier_routing.py -v
pytest app/tests/plan/test_plan_executor_deps.py -v
```

## ANTI-PATTERNS
- Do not weaken tests to match fallback behavior; fix the behavior.
- Do not add external-service dependencies to unit tests.
- Do not reuse `runtime/` or real plan DB state unless the test explicitly covers migration/forensics.
