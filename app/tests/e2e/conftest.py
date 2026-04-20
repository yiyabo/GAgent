"""E2E test fixtures for backend tests that exercise real LLM calls.

Every test in this directory is automatically marked with the ``external``
marker so that ``pytest -m "not external"`` (used in PR CI) skips them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

# Load .env from project root so API keys are available to E2E tests
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

# Cache real env vars BEFORE any test fixture overwrites them
_REAL_ENV: dict[str, str] = {}
for _var in (
    "LLM_PROVIDER", "QWEN_API_KEY", "OPENAI_API_KEY", "KIMI_API_KEY",
    "PERPLEXITY_API_KEY", "QWEN_MODEL", "OPENAI_MODEL", "KIMI_MODEL",
    "PERPLEXITY_MODEL", "QWEN_API_URL", "E2E_LLM_TIMEOUT",
    "DECOMP_MODEL", "PLAN_EXECUTOR_MODEL", "QWEN_VL_MODEL",
    "QWEN_LONG_MODEL", "QWEN_Embedding_MODEL",
):
    _val = os.environ.get(_var)
    if _val:
        _REAL_ENV[_var] = _val

# Apply the 'external' marker to every test collected under app/tests/e2e/
pytestmark = pytest.mark.external

# ---------------------------------------------------------------------------
# Provider → env-var mapping
# ---------------------------------------------------------------------------

_PROVIDER_KEY_MAP: dict[str, str] = {
    "qwen": "QWEN_API_KEY",
    "openai": "OPENAI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}


def _reset_all_singletons() -> None:
    """Reset every cached singleton so the next create_app() reads fresh env."""
    from app.config.database_config import reset_database_config
    from app.config.decomposer_config import get_decomposer_settings
    from app.database_pool import close_connection_pool
    from app.llm import reset_default_client
    from app.services.foundation.settings import get_settings

    close_connection_pool()
    reset_database_config()
    reset_default_client()
    get_settings.cache_clear()
    get_decomposer_settings.cache_clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Build a TestClient wired to the real LLM (not mocked).

    This fixture does NOT use ``isolated_app_env`` or ``app_client_factory``
    from the parent conftest because those fixtures forcibly set fake LLM
    credentials (``QWEN_API_URL=https://example.com/...``).  Instead, we
    set up DB isolation ourselves and restore the real .env values.
    """
    import app.main as app_main
    import app.repository.plan_storage as plan_storage

    # --- Determine provider and skip if API key is missing ----------------
    provider = _REAL_ENV.get("LLM_PROVIDER", "qwen").lower()
    key_var = _PROVIDER_KEY_MAP.get(provider)
    if not key_var:
        pytest.skip(f"Unknown LLM provider '{provider}'")
    api_key = _REAL_ENV.get(key_var)
    if not api_key:
        pytest.skip(f"E2E test skipped: {key_var} is not set (required for '{provider}')")

    # --- DB isolation (same as isolated_app_env but without fake LLM) -----
    paths = {
        "db_root": tmp_path / "db",
        "runtime_root": tmp_path / "runtime",
        "info_root": tmp_path / "information_sessions",
        "workspace_root": tmp_path / "workspaces",
        "audit_root": tmp_path / "terminal_audit",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DB_ROOT", str(paths["db_root"]))
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{(paths['db_root'] / 'main' / 'plan_registry.db').resolve()}",
    )
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(paths["runtime_root"]))
    monkeypatch.setenv("APP_INFO_SESSIONS_ROOT", str(paths["info_root"]))
    monkeypatch.setenv("EXECUTION_WORKSPACES_ROOT", str(paths["workspace_root"]))
    monkeypatch.setenv("TERMINAL_AUDIT_ROOT", str(paths["audit_root"]))
    monkeypatch.setenv("TERMINAL_ENABLED", "true")
    monkeypatch.setenv("AUTH_MODE", "proxy")

    # --- Restore ALL real LLM env vars (the whole point of E2E) -----------
    for var, val in _REAL_ENV.items():
        monkeypatch.setenv(var, val)

    # --- Stub out slow/unnecessary startup tasks -------------------------
    async def _noop_initialize_toolbox() -> None:
        return None

    monkeypatch.setattr(app_main, "setup_logging", lambda: None)
    monkeypatch.setattr(app_main, "initialize_toolbox", _noop_initialize_toolbox)
    monkeypatch.setattr(app_main, "fix_stale_jobs_on_startup", lambda: 0)
    monkeypatch.setattr(plan_storage, "get_running_phagescope_trackings", lambda: [])

    # --- Create the app with real LLM config ------------------------------
    _reset_all_singletons()
    app = app_main.create_app()
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    _reset_all_singletons()


@pytest.fixture
def e2e_llm_timeout() -> int:
    """Return the per-LLM-call timeout in seconds (default 120)."""
    return int(_REAL_ENV.get("E2E_LLM_TIMEOUT", "120"))
