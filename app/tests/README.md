# Test Layers

- Default suite: `pytest -q app/tests`
- Unit layer: `pytest -q app/tests/unit`
- Plan layer: `pytest -q app/tests/plan`
- Chat layer: `pytest -q app/tests/chat`
- Tools layer: `pytest -q app/tests/tools`
- Paper layer: `pytest -q app/tests/paper`
- Integration layer: `pytest -q app/tests/integration -m integration`
- Production smoke layer: `pytest -q app/tests/smoke -m prod_smoke`

## Directory Layout

- `app/tests/unit`: generic service, auth, session, storage, and low-level regressions
- `app/tests/plan`: plan generation, dependency planning, full-plan execution, todo/job state, and recovery
- `app/tests/chat`: chat routing, DeepThink, action execution, cascade behavior, and chat guardrails
- `app/tests/tools`: tool execution, code executor, terminal, phagescope, bio tools, interpreters, and tool I/O
- `app/tests/paper`: literature/manuscript/review-pack and paper-specific pipelines
- `app/tests/integration`: real-app integration coverage
- `app/tests/smoke`: startup and production-oriented smoke checks

Legacy/reference test suites remain outside the default Python test entrypoint:

- `reference/GAgent/...`
- `execute_memory/A-mem-main/tests/...`

Manual verification scripts are no longer treated as pytest tests:

- `scripts/run_amem_integration_check.py`
- `tool_box/bio_tools/run_bio_tools_complete.py`
- `scripts/dev_tmp/*.py`

## Naming And Placement Rules

All Python tests must use the file pattern `test_<area>_<behavior>.py`.

- Put a test in `unit/` only when it exercises a single module or a tightly-scoped service contract with no routing/decomposition/tool orchestration.
- Move a test to `plan/` as soon as it touches task graphs, dependency planning, todo lists, plan jobs, or full-plan execution.
- Move a test to `chat/` as soon as it touches chat routing, DeepThink, action execution, follow-through, or conversation/session execution semantics.
- Move a test to `tools/` as soon as it touches tool handlers, code executor, terminal, interpreters, phagescope, bio tools, or artifact/path verification around tool outputs.
- Move a test to `paper/` as soon as it touches literature, manuscript, review-pack, or paper-specific evidence/release contracts.
- Keep `integration/` and `smoke/` for end-to-end app lifecycles only; do not place narrow unit-style assertions there.

### Unit Naming

`unit/` should stay small and boring. Use the first token after `test_` as the ownership/domain prefix:

- `auth_*`: auth and request identity
- `session_*`: session identity, paths, lifecycle, titles
- `memory_*`: memory store, embeddings, isolation
- `context_*`: context window management and compaction
- `protocol_*`: protocol serialization contracts
- `resource_*`: resource/process limiting
- `semantic_*`: semantic classifier and intent scoring
- `audit_*`: audit logging
- `upload_*`: upload/session storage
- `llm_*`: low-level model client and timeout behavior
- `executor_*`: small executor config/behavior tests that do not involve plan graphs or tools
- `realtime_*`: realtime bus primitives
- `command_*`: command filtering/safety classification
- `layout_*`: meta-tests that enforce test-suite structure

If a test file needs a prefix outside this list, first ask whether it belongs in `unit/` at all.

## Markers

- `integration`: real `create_app()` tests with isolated local database/filesystem dependencies
- `prod_smoke`: production-oriented startup, HTTP, and WebSocket smoke coverage
- `external`: reserved for future tests that require staging or real external services

## CI split

- PR: run everything except `prod_smoke` and `external`
- Nightly/manual: run `prod_smoke` with coverage reporting
