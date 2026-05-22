from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def test_code_executor_qwen_no_output_watchdog_returns_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import code_executor as code_executor_module

    monkeypatch.setattr(
        code_executor_module,
        "_resolve_qwen_cli_no_output_timeout_seconds",
        lambda: 0.01,
    )
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_qwen_completed_output_exit_check_seconds",
        lambda: 0.01,
    )
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_qwen_process_kill_wait_seconds",
        lambda: 0.01,
    )
    monkeypatch.setenv("QWEN_CODE_MODEL", "test-model")
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: (
            "qwen_code",
            "qwen_primary",
            "code task routed to qwen_code primary lane",
        ),
    )
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_runtime_session_dir",
        lambda _session_id: tmp_path / "session-x",
    )
    monkeypatch.setattr(
        code_executor_module,
        "_build_execution_spec",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        code_executor_module,
        "_qwen_code_cli_available",
        lambda: True,
    )

    class _Router:
        def get_task_output_dir(self, session_id, task_id, ancestor_chain, create=True):
            out = tmp_path / "raw" / str(session_id) / f"task_{task_id}"
            if create:
                out.mkdir(parents=True, exist_ok=True)
            return out

        def get_tmp_output_dir(self, session_id, run_id, create=True):
            out = tmp_path / "tmp" / str(session_id) / str(run_id)
            if create:
                out.mkdir(parents=True, exist_ok=True)
            return out

    monkeypatch.setattr(code_executor_module, "get_path_router", lambda: _Router())

    class _Driver:
        def get_execution_lock(self, _session_id):
            return asyncio.Lock()

        async def ensure_container(self, *_args, **_kwargs):
            return "qwen-test-container"

    monkeypatch.setattr(
        "app.services.terminal.qwen_session_driver.get_qwen_session_driver",
        lambda: _Driver(),
    )

    class _NeverEndingStream:
        def __init__(self, process):
            self.process = process

        async def read(self, _chunk_size):
            while not self.process.killed:
                await asyncio.sleep(0.001)
            return b""

    class _NeverEndingProcess:
        returncode = None

        def __init__(self):
            self.killed = False
            self.stdout = _NeverEndingStream(self)
            self.stderr = _NeverEndingStream(self)

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode if self.returncode is not None else -9

    created: list[_NeverEndingProcess] = []

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        proc = _NeverEndingProcess()
        created.append(proc)
        return proc

    monkeypatch.setattr(
        code_executor_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="produce a report",
            allowed_tools="Bash,Write,Read",
            session_id="session-x",
            plan_id=1,
            task_id=2,
        )
    )

    assert created and created[0].killed is True
    assert result["success"] is False
    assert result["runtime_failure"] is True
    assert result["error_category"] == "executor_infrastructure"
    assert "qwen_cli_no_output_timeout" in result["stderr"]
