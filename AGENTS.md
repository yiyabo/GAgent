# Repository Guidelines

## Project Structure & Module Organization
- Backend lives in `app/`; `app/main.py` exposes the FastAPI entry point while schedulers, repositories, and services sit in dedicated subpackages.
- CLI automations mirror backend logic under `cli/`. Vue 3 + Vite frontend is in `frontend/` with static assets in `public/` and components in `src/`.
- Tests reside in `tests/` plus `test_conversational_agent.py`; docs and examples live in `docs/` and `examples/`.
- SQLite caches such as `tasks.db` and `evaluation_cache.db` store run history—keep them locally but exclude them from release bundles.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: create and enter an isolated Python 3.10+ environment.
- `pip install -r requirements.txt`: install FastAPI, Pydantic, and evaluation dependencies.
- `python -m uvicorn app.main:app --reload --port 8000`: serve the API with hot reload.
- `npm install --prefix frontend && npm run dev --prefix frontend`: install UI dependencies and boot the dev server at http://localhost:3000.
- `pytest`: execute the backend test matrix; async fixtures are configured in `pytest.ini`.
- `./start_test.sh`: run the mocked full stack, honoring `GLM_API_KEY` or `LLM_MOCK=1`.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and descriptive `snake_case` functions.
- Use `PascalCase` for Pydantic models and Vue components, and prefer explicit type hints.
- Favor structured logging through the `logging` module; avoid `print`.
- Default to ASCII; introduce non-ASCII only when a module already requires it.

## Testing Guidelines
- Place unit tests beside their subjects in `tests/test_<feature>.py`; keep integration flows in `test_conversational_agent.py` or `tests/test_evaluation_integration.py`.
- Enable async scenarios with `pytest`'s auto asyncio mode and mock remote LLMs by exporting `LLM_MOCK=1`.
- Update or add snapshots when behavior changes intentionally; fail fast if coverage regresses.

## Commit & Pull Request Guidelines
- Use Conventional Commit prefixes (`feat:`, `fix:`, `chore:`, `refactor:`) with subjects under 72 characters.
- Each commit should include or justify tests and note any environment variables touched.
- PRs must link relevant issues, summarize backend/frontend impacts, report `pytest` and `npm run build` (or equivalent), and capture screenshots for UI-affecting changes.

## Security & Configuration Tips
- Store secrets like `GLM_API_KEY` in your shell profile or a `.env` ignored by git.
- Reset or redact `*-cache.db` artifacts before sharing logs, and monitor rate limits when exercising evaluation pipelines.
