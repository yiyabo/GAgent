"""Tests for DockerPTYBackend and qwen_code terminal mode integration."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.terminal.docker_pty_backend import (
    CONTAINER_HOME,
    CONTAINER_WORKDIR,
    CONTAINER_USERNAME,
    DockerPTYBackend,
    QWEN_CODE_IMAGE,
    QWEN_EXECUTABLE,
    resolve_qwen_container_memory_limit,
)
from app.services.terminal.session_manager import TerminalSessionManager


# ---------------------------------------------------------------------------
# DockerPTYBackend unit tests (no real Docker required)
# ---------------------------------------------------------------------------


class TestBuildContainerEnv:
    """Verify env assembly logic without spawning anything."""

    def test_includes_qwen_api_key(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-test-key")
        env = DockerPTYBackend._build_container_env()
        assert env["OPENAI_API_KEY"] == "sk-test-key"

    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("QWEN_CODE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        env = DockerPTYBackend._build_container_env()
        assert "dashscope.aliyuncs.com" in env["OPENAI_BASE_URL"]

    def test_custom_base_url(self, monkeypatch):
        monkeypatch.setenv("QWEN_CODE_BASE_URL", "http://my-proxy:8080/v1")
        env = DockerPTYBackend._build_container_env()
        assert env["OPENAI_BASE_URL"] == "http://my-proxy:8080/v1"

    def test_extra_env_merged(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "k")
        env = DockerPTYBackend._build_container_env({"MY_VAR": "hello"})
        assert env["MY_VAR"] == "hello"
        assert "OPENAI_API_KEY" in env

    def test_sets_home(self):
        env = DockerPTYBackend._build_container_env()
        assert env["HOME"] == CONTAINER_HOME
        assert env["USER"] == CONTAINER_USERNAME
        assert env["LOGNAME"] == CONTAINER_USERNAME

    def test_model_override(self, monkeypatch):
        monkeypatch.setenv("QWEN_CODE_MODEL", "qwen-coder-plus-latest")
        env = DockerPTYBackend._build_container_env()
        assert env["QWEN_CODE_MODEL"] == "qwen-coder-plus-latest"


class TestBuildQwenExecArgs:
    """Verify the interactive qwen CLI boot command."""

    def test_includes_auth_workspace_and_session(self):
        cmd = DockerPTYBackend.build_qwen_exec_args(
            qwen_session_id="terminal:test session",
        )

        assert cmd[:3] == [QWEN_EXECUTABLE, "--auth-type", "openai"]
        assert "--add-dir" in cmd
        assert CONTAINER_WORKDIR in cmd
        assert "--session-id" in cmd
        UUID(cmd[cmd.index("--session-id") + 1])

    def test_includes_model_override(self, monkeypatch):
        monkeypatch.setenv("QWEN_CODE_MODEL", "qwen3-coder-plus")

        cmd = DockerPTYBackend.build_qwen_exec_args()

        assert "-m" in cmd
        assert "qwen3-coder-plus" in cmd

    def test_includes_extra_dirs_once(self):
        cmd = DockerPTYBackend.build_qwen_exec_args(
            extra_dirs=["/repo", "/repo", "/data"],
        )

        add_dir_values = [
            cmd[idx + 1]
            for idx, token in enumerate(cmd[:-1])
            if token == "--add-dir"
        ]
        assert add_dir_values == [CONTAINER_WORKDIR, "/repo", "/data"]


class TestIdentityMountContents:
    """Verify the generated passwd/group entries for the mapped UID/GID."""

    def test_includes_runner_identity_and_home(self):
        passwd_contents, group_contents = DockerPTYBackend._build_identity_mount_contents(
            uid=501,
            gid=20,
        )

        assert f"{CONTAINER_USERNAME}:x:501:20:{CONTAINER_USERNAME}:{CONTAINER_HOME}:/bin/bash" in passwd_contents
        assert f"{CONTAINER_USERNAME}:x:20:" in group_contents


class TestQwenContainerMemoryLimit:
    """Verify qwen-code Docker memory limits are opt-in."""

    def test_unset_and_unlimited_values_disable_memory_limit(self, monkeypatch):
        monkeypatch.delenv("QWEN_CODE_CONTAINER_MEMORY", raising=False)
        assert resolve_qwen_container_memory_limit() is None
        for value in ("", "0", "none", "null", "unlimited", "host", "off", "false", "no"):
            assert resolve_qwen_container_memory_limit(value) is None

    def test_explicit_value_is_preserved(self):
        assert resolve_qwen_container_memory_limit(" 64g ") == "64g"

    @pytest.mark.asyncio
    async def test_spawn_omits_memory_flag_by_default(self, monkeypatch):
        monkeypatch.delenv("QWEN_CODE_CONTAINER_MEMORY", raising=False)
        captured = {}

        class _Proc:
            returncode = 1

            async def communicate(self):
                return b"", b"create failed"

        async def _fake_create_subprocess_exec(*args, **_kwargs):
            captured["cmd"] = list(args)
            return _Proc()

        backend = DockerPTYBackend()
        monkeypatch.setattr(DockerPTYBackend, "_check_image", AsyncMock())
        monkeypatch.setattr(
            DockerPTYBackend,
            "_build_container_env",
            staticmethod(lambda env=None: {}),
        )
        monkeypatch.setattr(
            DockerPTYBackend,
            "_create_identity_mount_files",
            staticmethod(lambda: (None, None)),
        )
        monkeypatch.setattr(
            "app.services.terminal.docker_pty_backend.asyncio.create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        with pytest.raises(RuntimeError, match="Failed to create container"):
            await backend.spawn(cwd="/tmp")

        assert not any(str(token).startswith("--memory") for token in captured["cmd"])

    @pytest.mark.asyncio
    async def test_spawn_uses_explicit_memory_flag(self, monkeypatch):
        monkeypatch.setenv("QWEN_CODE_CONTAINER_MEMORY", "64g")
        captured = {}

        class _Proc:
            returncode = 1

            async def communicate(self):
                return b"", b"create failed"

        async def _fake_create_subprocess_exec(*args, **_kwargs):
            captured["cmd"] = list(args)
            return _Proc()

        backend = DockerPTYBackend()
        monkeypatch.setattr(DockerPTYBackend, "_check_image", AsyncMock())
        monkeypatch.setattr(
            DockerPTYBackend,
            "_build_container_env",
            staticmethod(lambda env=None: {}),
        )
        monkeypatch.setattr(
            DockerPTYBackend,
            "_create_identity_mount_files",
            staticmethod(lambda: (None, None)),
        )
        monkeypatch.setattr(
            "app.services.terminal.docker_pty_backend.asyncio.create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        with pytest.raises(RuntimeError, match="Failed to create container"):
            await backend.spawn(cwd="/tmp")

        assert "--memory=64g" in captured["cmd"]


class TestDockerPTYBackendProperties:
    """Test basic state properties."""

    def test_initial_state(self):
        backend = DockerPTYBackend()
        assert backend.is_closed is True
        assert backend.pid is None
        assert backend.container_name is None

    @pytest.mark.asyncio
    async def test_write_raises_when_not_running(self):
        backend = DockerPTYBackend()
        with pytest.raises(RuntimeError, match="not running"):
            await backend.write(b"hello")

    @pytest.mark.asyncio
    async def test_spawn_rejects_double_spawn(self):
        backend = DockerPTYBackend()
        backend.child_pid = 12345  # fake
        with pytest.raises(RuntimeError, match="already running"):
            await backend.spawn()

    @pytest.mark.asyncio
    async def test_is_available_returns_bool(self):
        # Just verify it returns a bool and doesn't crash
        result = await DockerPTYBackend.is_available()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_image_exists_returns_bool(self):
        result = await DockerPTYBackend.image_exists("nonexistent-image:v999")
        assert result is False

    def test_covers_path_uses_same_path_mount_roots(self):
        backend = DockerPTYBackend()
        mount_root = os.path.realpath("/tmp/project")
        backend._same_path_mount_roots = (mount_root,)

        assert backend.covers_path(os.path.join(mount_root, "src", "file.py")) is True
        assert backend.covers_path("/tmp/other/file.py") is False


class TestDockerPTYBackendSpawnMocked:
    """Test spawn logic with Docker calls mocked out."""

    @pytest.mark.asyncio
    async def test_spawn_fails_on_missing_image(self):
        backend = DockerPTYBackend()
        # Mock _check_image to raise
        with patch.object(
            DockerPTYBackend,
            "_check_image",
            side_effect=RuntimeError("image not found"),
        ):
            with pytest.raises(RuntimeError, match="image not found"):
                await backend.spawn(cwd="/tmp")

    @pytest.mark.asyncio
    async def test_cleanup_container_is_idempotent(self):
        backend = DockerPTYBackend()
        backend._container_name = None
        await backend._cleanup_container()  # should not raise

    @pytest.mark.asyncio
    async def test_terminate_without_spawn(self):
        """Terminate on a never-spawned backend should be safe."""
        backend = DockerPTYBackend()
        await backend.terminate()  # should not raise
        assert backend.is_closed is True


# ---------------------------------------------------------------------------
# Session manager integration (mode dispatch)
# ---------------------------------------------------------------------------


class TestSessionManagerQwenCodeMode:
    """Verify that session_manager correctly dispatches qwen_code mode."""

    @pytest.mark.asyncio
    async def test_create_session_dispatches_docker_backend(self):
        """create_session(mode='qwen_code') should instantiate DockerPTYBackend."""
        mgr = TerminalSessionManager()

        # Mock DockerPTYBackend.spawn to avoid real Docker
        with patch(
            "app.services.terminal.session_manager.DockerPTYBackend"
        ) as MockBackend:
            async def _pending_read():
                await asyncio.sleep(3600)
                return b""

            mock_instance = MagicMock()
            mock_instance.spawn = AsyncMock()
            mock_instance.read = AsyncMock(side_effect=_pending_read)
            mock_instance.is_closed = False
            MockBackend.return_value = mock_instance
            MockBackend.build_qwen_exec_args.side_effect = DockerPTYBackend.build_qwen_exec_args

            session = await mgr.create_session("test-sid", mode="qwen_code")

            assert session.mode == "qwen_code"
            mock_instance.spawn.assert_awaited_once()
            call_kwargs = mock_instance.spawn.call_args.kwargs
            assert "cwd" in call_kwargs
            assert call_kwargs["exec_args"][:3] == [QWEN_EXECUTABLE, "--auth-type", "openai"]
            assert "--add-dir" in call_kwargs["exec_args"]
            assert CONTAINER_WORKDIR in call_kwargs["exec_args"]
            assert "--session-id" in call_kwargs["exec_args"]
            assert call_kwargs["qwen_session_id"] == "test-sid"
            assert any(
                host_path == container_path
                for host_path, container_path in call_kwargs["extra_mounts"]
            )

            # Cleanup
            session.state = "closed"
            if session.output_task:
                session.output_task.cancel()
                try:
                    await session.output_task
                except (asyncio.CancelledError, Exception):
                    pass
            if mgr._reaper_task:
                mgr._reaper_task.cancel()
                try:
                    await mgr._reaper_task
                except (asyncio.CancelledError, Exception):
                    pass


# ---------------------------------------------------------------------------
# Tool handler mode enum
# ---------------------------------------------------------------------------


class TestTerminalSessionToolModeEnum:
    """Ensure the tool schema advertises qwen_code."""

    def test_tool_schema_includes_qwen_code(self):
        from tool_box.tools_impl import terminal_session as terminal_session_module

        terminal_tool = terminal_session_module.terminal_session_tool
        assert isinstance(terminal_tool, dict)
        parameters_schema = terminal_tool["parameters_schema"]
        assert isinstance(parameters_schema, dict)
        properties = parameters_schema["properties"]
        assert isinstance(properties, dict)
        mode_schema = properties["mode"]
        assert isinstance(mode_schema, dict)
        enum_values = mode_schema["enum"]
        assert isinstance(enum_values, list)
        assert "qwen_code" in enum_values
