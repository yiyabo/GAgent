# Test Layers

- Default suite: `pytest -q app/tests`
- Unit layer: `pytest -q app/tests/unit`
- Plan layer: `pytest -q app/tests/plan`
- Chat layer: `pytest -q app/tests/chat`
- Tools layer: `pytest -q app/tests/tools`
- Paper layer: `pytest -q app/tests/paper`
- Integration layer: `pytest -q app/tests/integration -m integration`
- Production smoke layer: `pytest -q app/tests/smoke -m prod_smoke`
- E2E layer (real LLM): `pytest -q app/tests/e2e -m external`

## Directory Layout

- `app/tests/unit`: generic service, auth, session, storage, and low-level regressions
- `app/tests/plan`: plan generation, dependency planning, full-plan execution, todo/job state, and recovery
- `app/tests/chat`: chat routing, DeepThink, action execution, cascade behavior, and chat guardrails
- `app/tests/tools`: tool execution, code executor, terminal, phagescope, bio tools, interpreters, and tool I/O
- `app/tests/paper`: literature/manuscript/review-pack and paper-specific pipelines
- `app/tests/integration`: real-app integration coverage
- `app/tests/smoke`: startup and production-oriented smoke checks
- `app/tests/e2e`: end-to-end tests with real LLM calls (marked `external`, run nightly)

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
- `external`: tests that require real external services (LLM API keys, network access); used by the E2E suite in `app/tests/e2e/`

## CI split

- PR: run everything except `prod_smoke` and `external`
- Nightly/manual: run `prod_smoke` with coverage reporting
- Nightly E2E: run `external` with real LLM API keys (`.github/workflows/nightly-e2e.yml`)

## E2E Test Environment Variables

The backend E2E suite (`app/tests/e2e/`) requires real LLM API keys. Tests are automatically skipped when the required key is missing.

### Required (one of, depending on provider)

| Variable | Description |
|----------|-------------|
| `QWEN_API_KEY` | API key for Qwen provider |
| `OPENAI_API_KEY` | API key for OpenAI provider |
| `KIMI_API_KEY` | API key for Kimi provider |
| `PERPLEXITY_API_KEY` | API key for Perplexity provider |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `qwen` | Which LLM provider to use (`qwen`, `openai`, `kimi`, `perplexity`) |
| `QWEN_MODEL` | (provider default) | Model override for Qwen |
| `OPENAI_MODEL` | (provider default) | Model override for OpenAI |
| `KIMI_MODEL` | (provider default) | Model override for Kimi |
| `PERPLEXITY_MODEL` | (provider default) | Model override for Perplexity |
| `E2E_LLM_TIMEOUT` | `120` | Per-LLM-call timeout in seconds |
