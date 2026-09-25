# Unit Test Conventions

`app/tests/unit` is reserved for low-level contracts that do not exercise plan
graphs, chat orchestration, or tool execution pipelines.

## What belongs here

- Pure service logic
- Small request/auth/session helpers
- Resource, protocol, and storage primitives
- LLM client behaviors such as timeout configuration
- Suite structure guardrails

## What does not belong here

- Anything that calls `PlanExecutor`, full-plan routes, todo/job APIs, or dependency planners
- Anything that exercises chat routing, DeepThink, or action execution
- Anything that invokes tool handlers, interpreters, terminal/code executor, phagescope, or manuscript/literature pipelines

## Filename pattern

Use `test_<prefix>_<behavior>.py`, where `<prefix>` is one of:

- `auth`
- `session`
- `memory`
- `context`
- `protocol`
- `resource`
- `semantic`
- `audit`
- `upload`
- `llm`
- `executor`
- `realtime`
- `command`
- `layout`
- `artifact`
- `compliance`
- `conversation`
- `log`
- `platform`
- `project`
- `qwen`
- `route`
- `sqlite`
- `sso`

`test_layout_conventions.py::_ALLOWED_UNIT_PREFIXES` is the enforcing source of
truth; update both together.

Examples:

- `test_session_paths.py`
- `test_memory_isolation.py`
- `test_llm_timeout.py`
- `test_layout_conventions.py`
