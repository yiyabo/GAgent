"""Tests for QwenSessionDriver — agent-driven Docker container execution."""

import asyncio
import os
from types import SimpleNamespace
import pytest

from app.services.terminal.docker_pty_backend import DockerPTYBackend
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


class TestSharedTerminalReuse:
    @pytest.mark.asyncio
    async def test_ensure_container_reuses_shared_qwen_terminal(self, monkeypatch):
        driver = QwenSessionDriver()
        shared_lock = asyncio.Lock()
        mount_root = os.path.realpath("/tmp/project")
        shared_backend = DockerPTYBackend()
        shared_backend._container_name = "shared-container"
        shared_backend._qwen_session_id = "shared-qwen-session"
        shared_backend._same_path_mount_roots = (mount_root,)
        shared_session = SimpleNamespace(
            terminal_id="term-123",
            session_id="chat-123",
            backend=shared_backend,
            command_lock=shared_lock,
        )

        async def _fake_ensure_qwen_code_session(session_id: str, *, required_paths=None):
            assert session_id == "chat-123"
            assert required_paths
            return shared_session

        monkeypatch.setattr(
            "app.services.terminal.qwen_session_driver.terminal_session_manager.ensure_qwen_code_session",
            _fake_ensure_qwen_code_session,
        )

        container = await driver.ensure_container(
            "chat-123",
            host_work_dir=os.path.join(mount_root, "runtime", "task1"),
            extra_mounts=[(os.path.join(mount_root, "data"), os.path.join(mount_root, "data"))],
        )

        assert container == "shared-container"
        assert driver.get_qwen_session_id("chat-123") == "shared-qwen-session"
        assert driver.get_execution_lock("chat-123") is shared_lock

    @pytest.mark.asyncio
    async def test_cleanup_does_not_remove_shared_terminal_container(self, monkeypatch):
        driver = QwenSessionDriver()
        driver._containers["chat-123"] = "shared-container"
        driver._session_ids["chat-123"] = "shared-qwen-session"
        driver._shared_terminal_ids["chat-123"] = "term-123"
        driver._locks["chat-123"] = asyncio.Lock()

        removed = {"called": False}

        async def _fake_force_remove(_name: str):
            removed["called"] = True

        monkeypatch.setattr(driver, "_force_remove", _fake_force_remove)

        await driver.cleanup("chat-123")

        assert removed["called"] is False

    @pytest.mark.asyncio
    async def test_ensure_container_skips_shared_reuse_when_alias_mount_needed(self, monkeypatch, tmp_path):
        driver = QwenSessionDriver()
        real_tmp = tmp_path / "real-tmp"
        real_tmp.mkdir()
        work_dir = real_tmp / "workspace"

        async def _unexpected_shared_reuse(*_args, **_kwargs):
            raise AssertionError("shared terminal reuse should be skipped for alias mounts")

        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"container-id", b""

        async def _fake_subprocess_exec(*_args, **_kwargs):
            return _Proc()

        async def _fake_check_image(_image: str) -> None:
            return None

        async def _fake_force_remove(_name: str) -> None:
            return None

        monkeypatch.setattr(
            "app.services.terminal.qwen_session_driver.terminal_session_manager.ensure_qwen_code_session",
            _unexpected_shared_reuse,
        )
        monkeypatch.setattr(driver, "_check_image", _fake_check_image)
        monkeypatch.setattr(driver, "_force_remove", _fake_force_remove)
        monkeypatch.setattr(
            "app.services.terminal.qwen_session_driver.DockerPTYBackend._create_identity_mount_files",
            staticmethod(lambda: None),
        )
        monkeypatch.setattr(
            "app.services.terminal.qwen_session_driver.asyncio.create_subprocess_exec",
            _fake_subprocess_exec,
        )

        container = await driver.ensure_container(
            "chat-123",
            host_work_dir=str(work_dir),
            extra_mounts=[(str(real_tmp), "/tmp")],
        )

        assert container == "gagent-qc-agent-chat-123"
