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



def test_qwen_truncated_tool_failure_detection() -> None:
    from tool_box.tools_impl import code_executor as code_executor_module

    text = (
        "[ERROR] [CLI_ERRORS] Error executing tool write_file: "
        "Your previous response was truncated due to max_tokens limit, "
        "which produced incomplete file content. The tool call has been rejected "
        "to prevent writing truncated content to the file."
    )

    assert code_executor_module._is_qwen_truncated_tool_failure_text(text) is True
    assert code_executor_module._is_qwen_truncated_tool_failure_text("ordinary stderr") is False


@pytest.mark.asyncio
async def test_detect_qwen_debug_fatal_failure_from_host_log(tmp_path: Path) -> None:
    from tool_box.tools_impl import code_executor as code_executor_module

    debug_log = tmp_path / "qwen-debug.txt"
    debug_log.write_text(
        "[ERROR] [CLI_ERRORS] Error executing tool write_file: "
        "Your previous response was truncated due to max_tokens limit. "
        "The tool call has been rejected to prevent writing truncated content.",
        encoding="utf-8",
    )
    stderr = f"[stderr] Logging to: {debug_log}"

    note = await code_executor_module._detect_qwen_debug_fatal_failure(
        stderr_text=stderr,
        container_name=None,
    )

    assert note is not None
    assert "qwen_tool_call_truncated" in note


def test_recovery_prompt_warns_to_split_large_script_writes(tmp_path: Path) -> None:
    from tool_box.tools_impl.code_executor import _build_search_and_generate_prompt

    prompt = _build_search_and_generate_prompt(
        missing_files=["result.csv"],
        session_dir=tmp_path,
        execution_spec={"task_name": "T", "task_instruction": "Do analysis"},
        is_timeout=True,
    )

    assert "PREVIOUS EXECUTION TIMED OUT OR CLI TOOL CALL FAILED" in prompt
    assert "avoid one huge write_file call" in prompt
    assert "append or edit in focused chunks" in prompt


def test_external_cli_usage_records_task_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_box.tools_impl import code_executor as code_executor_module

    captured = {}

    def _fake_log_llm_usage(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.repository.llm_usage.log_llm_usage", _fake_log_llm_usage)

    usage = code_executor_module._record_external_cli_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=100,
        completion_tokens=25,
        session_id="session_x",
        plan_id=122,
        task_id=14,
        call_purpose="qwen_code_cli_execution",
    )

    assert usage is not None
    assert captured["session_id"] == "session_x"
    assert captured["plan_id"] == 122
    assert captured["task_id"] == 14
    assert captured["call_purpose"] == "qwen_code_cli_execution"
    assert captured["total_tokens"] == 125
    assert captured["estimated_cost"] is not None
