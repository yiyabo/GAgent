from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager

import pytest
from fastapi.testclient import TestClient

from app.config.database_config import reset_database_config
from app.database_pool import close_connection_pool
from app.llm import reset_default_client
from app.services.foundation.settings import get_settings


def _reset_test_singletons() -> None:
    close_connection_pool()
    reset_database_config()
    reset_default_client()
    get_settings.cache_clear()


@pytest.fixture
def isolated_app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {
        "db_root": tmp_path / "db",
        "runtime_root": tmp_path / "runtime",
        "info_root": tmp_path / "information_sessions",
        "workspace_root": tmp_path / "workspaces",
        "audit_root": tmp_path / "terminal_audit",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

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
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    monkeypatch.setenv("QWEN_API_URL", "https://example.com/v1/chat/completions")
    monkeypatch.setenv("QWEN_MODEL", "qwen-test")
    # Keep existing integration tests stable unless they explicitly opt in
    # to local account auth.
    monkeypatch.setenv("AUTH_MODE", "proxy")

    _reset_test_singletons()
    yield paths
    _reset_test_singletons()


@pytest.fixture
def app_client_factory(
    isolated_app_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., ContextManager[TestClient]]]:
    _ = isolated_app_env

    import app.main as app_main
    import app.repository.plan_storage as plan_storage

    async def _noop_initialize_toolbox() -> None:
        return None

    monkeypatch.setattr(app_main, "setup_logging", lambda: None)
    monkeypatch.setattr(app_main, "initialize_toolbox", _noop_initialize_toolbox)
    monkeypatch.setattr(app_main, "fix_stale_jobs_on_startup", lambda: 0)
    monkeypatch.setattr(plan_storage, "get_running_phagescope_trackings", lambda: [])

    @contextmanager
    def _build_client(
        *,
        raise_server_exceptions: bool = True,
    ) -> Iterator[TestClient]:
        _reset_test_singletons()
        app = app_main.create_app()
        with TestClient(
            app,
            raise_server_exceptions=raise_server_exceptions,
        ) as client:
            yield client
        _reset_test_singletons()

    yield _build_client


@pytest.fixture
def isolated_terminal_manager(monkeypatch: pytest.MonkeyPatch):
    import app.routers.terminal_routes as terminal_routes
    import app.services.terminal as terminal_package
    import app.services.terminal.session_manager as session_manager_module

    manager = session_manager_module.TerminalSessionManager()
    monkeypatch.setattr(session_manager_module, "terminal_session_manager", manager)
    monkeypatch.setattr(terminal_package, "terminal_session_manager", manager)
    monkeypatch.setattr(terminal_routes, "terminal_session_manager", manager)
    yield manager

    async def _close_all() -> None:
        rows = await manager.list_sessions()
        for row in rows:
            await manager.close_session(row["terminal_id"])

    asyncio.run(_close_all())


@pytest.fixture
def mock_llm_chat(monkeypatch: pytest.MonkeyPatch):
    """Mock the LLM to return a fixed plain-text reply without network calls.

    Usage::

        def test_something(mock_llm_chat, app_client_factory):
            mock_llm_chat("Hello from mock LLM")
            with app_client_factory() as client:
                resp = client.post("/chat/message", json={...})

    The mock patches ``StructuredChatAgent.get_structured_response`` so that
    the entire routing / middleware / DB pipeline runs for real — only the
    LLM call itself is replaced.
    """
    from app.services.llm.structured_response import LLMReply, LLMStructuredResponse

    def _set(reply_text: str = "Mock LLM response"):
        async def _fake_get_structured_response(self, user_message: str):
            return LLMStructuredResponse(
                llm_reply=LLMReply(message=reply_text),
                actions=[],
            )

        monkeypatch.setattr(
            "app.routers.chat.agent.StructuredChatAgent.get_structured_response",
            _fake_get_structured_response,
        )

    return _set
