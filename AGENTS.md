# AGENTS.md

## Cursor Cloud specific instructions

### Architecture overview

This is the **PhageAgent** AI-Driven Task Orchestration System — a monorepo with:

| Service | Tech | Port | Dev command |
|---------|------|------|-------------|
| Backend API | Python / FastAPI / Uvicorn | 9000 | `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload` |
| Frontend UI | React 18 / TypeScript / Vite / Ant Design | 3000 | `cd web-ui && npm run dev` |
| A-mem (optional) | FastAPI + ChromaDB + sentence-transformers | 8001 | `bash scripts/start_amem.sh` |

### Running the backend without conda

The `start_backend.sh` script tries `conda activate LLM`. In environments without conda, bypass it by setting:

```bash
export BACKEND_PYTHON_BIN=$(which python3)
```

Or run uvicorn directly:

```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload
```

### LLM API keys

The backend requires at least one LLM provider API key to handle chat requests. Default provider is `qwen` (env var `QWEN_API_KEY`). Without it, the backend starts and passes health checks, but chat requests return `RuntimeError: QWEN_API_KEY is not set`. Other providers: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `KIMI_API_KEY`, `XAI_API_KEY`.

### Running tests

- **Backend**: `python3 -m pytest -q app/tests/` (see `app/tests/README.md` for marker details)
- **Frontend**: `cd web-ui && npx vitest run`
- **Type-check**: `cd web-ui && npx tsc --noEmit`

Note: The repo does not include an `.eslintrc` config file, so `npm run lint` will fail with "couldn't find a configuration file".

### Known environment-specific test failures

- `test_real_app_terminal_http_and_websocket_roundtrip` and `test_ssh_backend_connect_read_write_disconnect` may fail due to `os.fork()` deprecation warnings in multi-threaded contexts and missing SSH backends. These are not code bugs.

### Database

SQLite is used (auto-created at `data/databases/main/plan_registry.db`). No external database setup needed.
