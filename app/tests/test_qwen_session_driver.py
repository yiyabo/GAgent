"""Tests for QwenSessionDriver — agent-driven Docker container execution."""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.services.terminal.qwen_session_driver import (
    QwenSessionDriver,
    get_qwen_session_driver,
    _sanitise_container_suffix,
    _DEFAULT_IMAGE,
)


# ---------------------------------------------------------------------------
# _sanitise_container_suffix
# ---------------------------------------------------------------------------

class TestSanitiseContainerSuffix:
    def test_simple_id(self):
        assert _sanitise_container_suffix("session123") == "session123"

    def test_special_chars_replaced(self):
        result = _sanitise_container_suffix("session_1775!@#abc")
        assert "-" in result or result.isalnum()
        # No special chars remain
        import re
        assert re.fullmatch(r"[a-zA-Z0-9-]+", result)

    def test_long_id_truncated(self):
        long_id = "a" * 80
        assert len(_sanitise_container_suffix(long_id)) <= 40

    def test_empty_id_gets_default(self):
        assert _sanitise_container_suffix("") == "default"

    def test_all_special_chars(self):
        assert _sanitise_container_suffix("!!!") == "default"


# ---------------------------------------------------------------------------
# _build_env
# ---------------------------------------------------------------------------

class TestBuildEnv:
    def test_includes_term(self):
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert env["TERM"] == "xterm-256color"

    def test_includes_home(self):
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert env["HOME"] == "/tmp/gagent_home"

    def test_picks_up_qwen_api_key(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-test-key-123")
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert env["OPENAI_API_KEY"] == "sk-test-key-123"

    def test_no_api_key_when_unset(self, monkeypatch):
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert "OPENAI_API_KEY" not in env

    def test_base_url_from_qwen_code_base_url(self, monkeypatch):
        monkeypatch.setenv("QWEN_CODE_BASE_URL", "https://custom.example.com/v1")
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert env["OPENAI_BASE_URL"] == "https://custom.example.com/v1"

    def test_base_url_default(self, monkeypatch):
        monkeypatch.delenv("QWEN_CODE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert "dashscope" in env["OPENAI_BASE_URL"]

    def test_model_env(self, monkeypatch):
        monkeypatch.setenv("QWEN_CODE_MODEL", "qwen-coder-plus")
        driver = QwenSessionDriver()
        env = driver._build_env()
        assert env["QWEN_CODE_MODEL"] == "qwen-coder-plus"


# ---------------------------------------------------------------------------
# get_qwen_session_driver singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_returns_same_instance(self):
        # Reset the module-level singleton
        import app.services.terminal.qwen_session_driver as mod
        mod._driver = None
        d1 = get_qwen_session_driver()
        d2 = get_qwen_session_driver()
        assert d1 is d2
        mod._driver = None  # cleanup


# ---------------------------------------------------------------------------
# Session ownership fields (session_manager integration)
# ---------------------------------------------------------------------------

class TestSessionOwnershipFields:
    def test_terminal_session_has_owner_fields(self):
        from app.services.terminal.session_manager import TerminalSession
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(TerminalSession)}
        assert "owner" in field_names
        assert "owner_lease_expires" in field_names
        assert "busy" in field_names

    def test_default_owner_is_none(self):
        from app.services.terminal.session_manager import TerminalSession
        from datetime import datetime, timezone
        session = TerminalSession(
            session_id="test",
            terminal_id="t1",
            mode="sandbox",
            backend=MagicMock(),
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            state="active",
            env={},
            cwd="/tmp",
            audit_logger=MagicMock(),
        )
        assert session.owner == "none"
        assert session.owner_lease_expires is None
        assert session.busy is False


# ---------------------------------------------------------------------------
# Code executor Docker integration flag
# ---------------------------------------------------------------------------

class TestCodeExecutorDockerFlag:
    """Verify that code_executor has the Docker container integration point."""

    def test_docker_container_name_variable_exists_in_source(self):
        """The code_executor should reference _docker_container_name."""
        import inspect
        from tool_box.tools_impl.code_executor import code_executor_handler
        source = inspect.getsource(code_executor_handler)
        assert "_docker_container_name" in source
        assert "qwen_session_driver" in source

    def test_rebuild_cli_command_wraps_docker(self):
        """Verify _rebuild_cli_command concept handles docker wrapping."""
        # This is a structural test — the actual _rebuild_cli_command is a closure
        # inside code_executor_handler, so we verify it exists in source
        import inspect
        from tool_box.tools_impl.code_executor import code_executor_handler
        source = inspect.getsource(code_executor_handler)
        assert "docker" in source
        assert "exec" in source
        assert "_docker_container_name" in source
