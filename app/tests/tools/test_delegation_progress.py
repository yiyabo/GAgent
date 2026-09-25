"""Delegation progress: the CLI lane must be visible in the parent's stream.

A delegated qwen/claude run can take tens of minutes; before S3c only the *local*
lane reported through ``ToolContext.on_progress``, so the activity stream stayed
blank for the whole delegation.  These tests pin the three reporting moments
(start / heartbeat / terminal), the interval gate that keeps the 0.25s cancel
poll from becoming an event every 0.25s, the cross-thread delivery the
``delegate_task`` → ``execute_sync`` → ``asyncio.run`` chain needs, and the
fail-open rule: a broken progress channel never changes the delegation's result.

The CLI harness mirrors ``test_delegation_cancellation.py``: the real handler is
driven against a real long-running python process instead of ``qwen``.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.services import cancellation
from app.services.cancellation import CancelToken
from tool_box.tools_impl import code_executor as code_executor_module
from tool_box.tools_impl.delegation_progress import (
    DelegationProgressReporter,
    build_delegation_progress,
    delegation_progress_enabled,
    format_duration,
)

_CLI_SCRIPT = """
import sys, time
print("fake-cli: working")
sys.stdout.flush()
time.sleep({sleep_seconds})
print("fake-cli: done")
"""


@pytest.fixture
def ledger_db(tmp_path: Path):
    """Keep the delegation's usage row out of the repository DB."""
    from app.database_pool import close_connection_pool, initialize_connection_pool

    initialize_connection_pool(db_path=str(tmp_path / "ledger.db"))
    try:
        yield tmp_path / "ledger.db"
    finally:
        close_connection_pool()


@pytest.fixture(autouse=True)
def _clean_token_context():
    cancellation.set_cancel_token(None)
    yield
    cancellation.set_cancel_token(None)


def _install_cli_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    sleep_seconds: float = 0.6,
    exit_code: int = 0,
) -> List[Dict[str, Any]]:
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
    monkeypatch.setattr(code_executor_module, "_build_execution_spec", lambda *_a, **_k: {})
    monkeypatch.setattr(code_executor_module, "_qwen_code_cli_available", lambda: True)

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
            return None

    monkeypatch.setattr(
        "app.services.terminal.qwen_session_driver.get_qwen_session_driver",
        lambda: _Driver(),
    )

    spawns: List[Dict[str, Any]] = []
    real_create_subprocess_exec = asyncio.create_subprocess_exec
    script = _CLI_SCRIPT.format(sleep_seconds=sleep_seconds)
    if exit_code:
        script += f"raise SystemExit({int(exit_code)})\n"

    async def _spawn(*_args, **kwargs):
        process = await real_create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            cwd=kwargs.get("cwd"),
            env=kwargs.get("env"),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            start_new_session=bool(kwargs.get("start_new_session", False)),
        )
        spawns.append({**kwargs, "pid": process.pid})
        return process

    monkeypatch.setattr(code_executor_module.asyncio, "create_subprocess_exec", _spawn)
    return spawns


async def _run_delegation(on_progress=None) -> Dict[str, Any]:
    from tool_box.context import ToolContext

    context = ToolContext(
        session_id="session-x",
        on_progress=on_progress,
        on_progress_loop=asyncio.get_running_loop() if on_progress else None,
    )
    return await code_executor_module.code_executor_handler(
        task="produce a report",
        allowed_tools="Bash,Write,Read",
        session_id="session-x",
        plan_id=None,
        task_id=None,
        require_task_context=False,
        tool_context=context,
    )


def _stages(payloads: List[Dict[str, Any]]) -> List[str]:
    return [str(payload.get("stage")) for payload in payloads]


# --- the three reporting moments through the real CLI lane ----------------------


async def test_cli_delegation_reports_start_heartbeats_and_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    monkeypatch.setenv("DELEGATION_PROGRESS_HEARTBEAT_SECONDS", "0.15")
    monkeypatch.setenv("DELEGATION_CANCEL_POLL_SECONDS", "0.05")
    _install_cli_harness(monkeypatch, tmp_path, sleep_seconds=0.7)

    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    result = await _run_delegation(on_progress)

    stages = _stages(payloads)
    assert result["success"] is True
    assert stages[0] == "started"
    assert stages[-1] == "completed"
    assert stages.count("running") >= 2
    # Progress must never outrun the CLI: no terminal report before another one.
    assert "failed" not in stages

    started = payloads[0]
    assert started["run_id"] == result["run_id"]
    assert started["backend"] == "qwen_code"
    assert started["lane"] == result["execution_lane"]
    assert started["attempt"] == 1
    assert started["phase"] == "primary"
    assert "Qwen Code" in started["message"]
    assert result["run_id"] in started["detail"]
    assert "task: produce a report" in started["detail"]

    heartbeat = next(payload for payload in payloads if payload["stage"] == "running")
    assert heartbeat["run_id"] == result["run_id"]
    assert heartbeat["elapsed_seconds"] >= 0.0
    assert "still running" in heartbeat["message"]

    completed = payloads[-1]
    assert completed["status"] == "completed"
    assert completed["elapsed_seconds"] >= 0.0
    assert completed["produced_files_count"] == result["produced_files_count"]
    assert completed["total_tokens"] is not None
    assert completed["detail"]
    assert "completed in" in completed["message"]


async def test_failed_delegation_reports_failed_and_never_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    # One attempt only: the retry policy would otherwise back off for minutes.
    monkeypatch.setenv("CLAUDE_CODE_MAX_RETRIES", "0")
    monkeypatch.setenv("CLAUDE_CODE_RETRY_BASE_DELAY_S", "0.5")
    _install_cli_harness(monkeypatch, tmp_path, sleep_seconds=0.2, exit_code=1)

    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    result = await _run_delegation(on_progress)

    stages = _stages(payloads)
    assert result["success"] is False
    assert stages[0] == "started"
    assert stages[-1] == "failed"
    assert "completed" not in stages
    failed = payloads[-1]
    assert failed["status"] == "failed"
    assert failed["run_id"] == result["run_id"]
    assert failed["attempt"] == 1
    assert "failed after" in failed["message"]


async def test_cancelled_delegation_reports_cancelled_and_never_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    monkeypatch.setenv("DELEGATION_PROGRESS_HEARTBEAT_SECONDS", "0.1")
    monkeypatch.setenv("DELEGATION_CANCEL_POLL_SECONDS", "0.05")
    _install_cli_harness(monkeypatch, tmp_path, sleep_seconds=120.0)

    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    token = CancelToken()
    cancellation.set_cancel_token(token)

    def _cancel_soon() -> None:
        time.sleep(0.4)
        token.set("chat_run_cancelled")

    canceller = threading.Thread(target=_cancel_soon, daemon=True)
    canceller.start()
    result = await _run_delegation(on_progress)
    canceller.join(timeout=5.0)

    stages = _stages(payloads)
    assert result["cancelled"] is True
    assert stages[-1] == "cancelled"
    assert "failed" not in stages
    cancelled = payloads[-1]
    assert cancelled["run_id"] == result["run_id"]
    assert cancelled["status"] == "cancelled"
    assert "cancelled after" in cancelled["message"]


async def test_progress_reporting_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    monkeypatch.setenv("DELEGATION_PROGRESS_ENABLED", "0")
    _install_cli_harness(monkeypatch, tmp_path, sleep_seconds=0.2)

    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    result = await _run_delegation(on_progress)

    assert payloads == []
    assert result["success"] is True
    assert "cancelled" not in result


async def test_a_failing_progress_callback_never_changes_the_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    """Fail-open: a raising callback is logged, the delegation still succeeds."""
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    monkeypatch.setenv("DELEGATION_PROGRESS_HEARTBEAT_SECONDS", "0.1")
    monkeypatch.setenv("DELEGATION_CANCEL_POLL_SECONDS", "0.05")
    _install_cli_harness(monkeypatch, tmp_path, sleep_seconds=0.3)

    calls: List[str] = []

    async def on_progress(_data: Dict[str, Any]) -> None:
        calls.append("async")
        raise RuntimeError("progress channel is broken")

    result = await _run_delegation(on_progress)

    assert calls, "the callback must actually have been invoked"
    assert result["success"] is True
    assert result["execution_status"] == "completed"


async def test_a_sync_progress_callback_is_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    """The contract says async, but the executor tolerates a plain callable."""
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    _install_cli_harness(monkeypatch, tmp_path, sleep_seconds=0.2)

    payloads: List[Dict[str, Any]] = []

    def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    result = await _run_delegation(on_progress)

    assert result["success"] is True
    assert _stages(payloads) == ["started", "completed"]


# --- cross-thread delivery ------------------------------------------------------


async def test_progress_crosses_the_to_thread_hop() -> None:
    """The worker thread's report is delivered on the loop that owns it."""
    owner_thread = threading.get_ident()
    received: List[tuple] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        received.append((threading.get_ident(), data))

    loop = asyncio.get_running_loop()

    def _worker() -> None:
        # The shape delegate_task drives: no running loop in this thread.
        assert threading.get_ident() != owner_thread

        async def _delegate() -> None:
            reporter = DelegationProgressReporter(on_progress, loop=loop, run_id="run-1")
            await reporter.report("started", "delegating")

        asyncio.run(_delegate())

    await asyncio.to_thread(_worker)

    assert [data["stage"] for _, data in received] == ["started"]
    assert all(ident == owner_thread for ident, _ in received)
    assert received[0][1]["run_id"] == "run-1"


async def test_worker_thread_execution_receives_the_progress_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``execute_sync`` keeps the callback + its loop through the real plumbing.

    This is the chain ``delegate_task`` → ``CodeAgentTaskDelegateExecutor`` →
    ``UnifiedToolExecutor.execute_sync`` → ``execute`` → ``ToolContext``: the
    callback the CLI lane reads must survive the worker-thread hop along with the
    loop it may be awaited on.
    """
    import tool_box
    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        UnifiedToolExecutor,
    )

    owner_thread = threading.get_ident()
    received: List[tuple] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        received.append((threading.get_ident(), data))

    async def _fake_execute_tool(tool_name, *, tool_context, **kwargs):  # noqa: ANN001
        reporter = build_delegation_progress(tool_context, run_id="run-x")
        await reporter.report("started", "delegating")
        await reporter.report("completed", "done")
        return {"success": True}

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)
    loop = asyncio.get_running_loop()

    payload = await asyncio.to_thread(
        lambda: UnifiedToolExecutor().execute_sync(
            "code_executor",
            {"task": "do the thing"},
            context=ToolExecutionContext(
                session_id="session-x",
                on_progress=on_progress,
                on_progress_loop=loop,
            ),
        )
    )

    assert payload["success"] is True
    assert [data["stage"] for _, data in received] == ["started", "completed"]
    assert all(ident == owner_thread for ident, _ in received)


async def test_delegate_task_hands_the_progress_channel_to_the_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.plans.task_delegate_executor import TaskDelegationResult
    from tool_box.context import ToolContext
    from tool_box.tools_impl import delegate_task as delegate_task_module

    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    captured: Dict[str, Any] = {}

    class _Recorder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def execute(self, spec, **kwargs):  # noqa: ANN001
            captured["spec"] = spec
            captured["kwargs"] = kwargs
            return TaskDelegationResult(status="completed", summary="done")

    monkeypatch.setattr(
        "app.services.plans.task_delegate_executor.CodeAgentTaskDelegateExecutor",
        _Recorder,
    )

    async def on_progress(_data: Dict[str, Any]) -> None:
        return None

    loop = asyncio.get_running_loop()
    payload = await delegate_task_module.delegate_task_handler(
        goal="Audit the pipeline",
        tool_context=ToolContext(session_id="session-x", on_progress=on_progress),
    )

    assert payload["status"] == "completed"
    assert captured["kwargs"]["on_progress"] is on_progress
    assert captured["kwargs"]["on_progress_loop"] is loop


async def test_delegate_task_without_a_progress_channel_keeps_the_spec_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.plans.task_delegate_executor import TaskDelegationResult
    from tool_box.context import ToolContext
    from tool_box.tools_impl import delegate_task as delegate_task_module

    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    captured: Dict[str, Any] = {}

    class _Recorder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def execute(self, spec, **kwargs):  # noqa: ANN001
            captured["spec"] = spec
            captured["kwargs"] = kwargs
            return TaskDelegationResult(status="completed", summary="done")

    monkeypatch.setattr(
        "app.services.plans.task_delegate_executor.CodeAgentTaskDelegateExecutor",
        _Recorder,
    )

    await delegate_task_module.delegate_task_handler(
        goal="Audit the pipeline",
        tool_context=ToolContext(session_id="session-x"),
    )

    assert captured["kwargs"]["on_progress"] is None
    # No callback ⇒ no loop is captured, and the spec shape is untouched.
    assert captured["kwargs"]["on_progress_loop"] is None
    assert captured["spec"].plan_id is None
    assert captured["spec"].task_id is None


# --- the interval gate (fake clock) ---------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_heartbeat_fires_on_its_interval_not_on_every_poll() -> None:
    clock = _FakeClock()
    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    reporter = DelegationProgressReporter(
        on_progress,
        run_id="run-1",
        heartbeat_seconds=15.0,
        clock=clock,
    )
    assert await reporter.report("started", "delegating") is True

    # The watcher's 0.25s poll for 14 seconds: not one heartbeat.
    for _ in range(56):
        clock.advance(0.25)
        assert await reporter.heartbeat(attempt=1, total_attempts=5, phase="primary") is False
    assert _stages(payloads) == ["started"]

    clock.advance(1.0)  # 15.0s since the last report
    assert await reporter.heartbeat(attempt=1, total_attempts=5, phase="primary") is True
    assert _stages(payloads) == ["started", "running"]
    heartbeat = payloads[-1]
    assert heartbeat["elapsed_seconds"] == 15.0
    assert heartbeat["attempt"] == 1
    assert heartbeat["run_id"] == "run-1"

    # The next interval is measured from the heartbeat, not from the start.
    clock.advance(14.0)
    assert await reporter.heartbeat(attempt=2, total_attempts=5, phase="repair") is False
    clock.advance(1.0)
    assert await reporter.heartbeat(attempt=2, total_attempts=5, phase="repair") is True
    assert payloads[-1]["phase"] == "repair"
    assert payloads[-1]["attempt"] == 2
    assert "phase repair" in payloads[-1]["detail"]


async def test_heartbeat_with_a_zero_interval_is_disabled() -> None:
    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    reporter = DelegationProgressReporter(on_progress, heartbeat_seconds=0.0)
    assert await reporter.report("started", "delegating") is True
    assert await reporter.heartbeat(attempt=1, total_attempts=1) is False
    assert _stages(payloads) == ["started"]


async def test_reporter_without_a_callback_is_inert() -> None:
    reporter = DelegationProgressReporter(None, run_id="run-1")

    assert reporter.enabled is False
    assert await reporter.report("started", "delegating") is False
    assert await reporter.heartbeat(attempt=1, total_attempts=1) is False
    assert build_delegation_progress(None).enabled is False
    assert reporter.elapsed_seconds >= 0.0


async def test_switching_reporting_off_disables_the_reporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELEGATION_PROGRESS_ENABLED", "false")
    assert delegation_progress_enabled() is False

    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    reporter = DelegationProgressReporter(on_progress, heartbeat_seconds=0.0)
    assert await reporter.report("started", "delegating") is False
    assert payloads == []


async def test_report_drops_none_fields_and_collapses_whitespace() -> None:
    payloads: List[Dict[str, Any]] = []

    async def on_progress(data: Dict[str, Any]) -> None:
        payloads.append(data)

    reporter = DelegationProgressReporter(on_progress)
    assert await reporter.report(
        "failed",
        "  Sub-agent   run\nfailed after 3s ",
        detail="  attempt 2/5\n",
        error_category=None,
        status="failed",
    )

    payload = payloads[-1]
    assert payload["message"] == "Sub-agent run failed after 3s"
    assert payload["detail"] == "attempt 2/5"
    assert payload["status"] == "failed"
    assert "error_category" not in payload


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (9.6, "10s"), (59.4, "59s"), (95.0, "1m35s"), (3720.4, "1h02m")],
)
def test_duration_labels_are_compact(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected
