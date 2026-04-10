"""Tests for QwenSessionDriver — agent-driven Docker container execution."""

import asyncio
import os
import pytest
from unittest.mock import MagicMock

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

