"""Cancelling an in-flight sub-agent delegation: the CLI process must really die.

A chat run's stop signal reaches the delegation through the thread-safe cancel
token (``app/services/cancellation.py``).  These tests drive the real
``code_executor_handler`` against a *real* long-running CLI process (and a real
grandchild), assert the whole process group is gone, and pin the result
semantics: a cancelled delegation is neither a success nor a task failure.

The process-liveness helpers mirror ``app/tests/tools/test_execute_code_kernel.py``:
a killed group member is either reaped (the probe sees ESRCH) or an unreaped
zombie, and both mean the process body is gone.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services import cancellation
from app.services.cancellation import CancelToken
from tool_box.tools_impl import code_executor as code_executor_module
from tool_box.tools_impl import code_executor_qwen as code_executor_qwen_module
from tool_box.tools_impl.code_executor_backend import (
    _is_qwen_container_infrastructure_error,
    _is_qwen_no_output_timeout,
)
from tool_box.tools_impl.code_executor_qwen import (
    DELEGATION_CANCELLED_MARKER,
    DELEGATION_CANCELLED_NOTE,
)

# The bound the implementation promises: once the token is set, the CLI receives
# a termination signal and is gone well inside five seconds.
CANCEL_LATENCY_BOUND_SECONDS = 5.0
_LIVENESS_TIMEOUT_SECONDS = 5.0

_CLI_SCRIPT = """
import os, subprocess, sys, time
open({cli_pid_file!r}, "w").write(str(os.getpid()))
{child_spawn}
time.sleep({sleep_seconds})
"""

_CHILD_SPAWN = """
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
open({child_pid_file!r}, "w").write(str(child.pid))
"""


# --- process liveness helpers (mirrors the execute_code kernel test) ------------


def _process_state(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        stat = ""
    if stat:
        tail = stat.rpartition(")")[2].strip()
        return tail.split(" ")[0] if tail else ""
    try:
        probe = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return probe.stdout.strip()[:1]


def _is_dead_or_zombie(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return _process_state(pid) == "Z"


def _assert_process_dead(pid: int, *, label: str) -> None:
    deadline = time.monotonic() + _LIVENESS_TIMEOUT_SECONDS
    while not _is_dead_or_zombie(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _is_dead_or_zombie(pid), (
        f"{label} pid {pid} is still runnable after the cancel "
        f"(state={_process_state(pid)!r})"
    )


def _read_pid(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


# --- harness ------------------------------------------------------------------


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
    with_grandchild: bool = True,
    sleep_seconds: float = 120.0,
) -> list[dict]:
    """Point code_executor at a live python CLI process instead of ``qwen``.

    Same switches as ``test_code_executor_qwen_watchdog``; only the spawned
    process changes, and every spawn kwarg is recorded so the tests can assert
    the CLI is started as its own session leader.
    """
    monkeypatch.setenv("QWEN_CODE_MODEL", "test-model")
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: ("qwen_code", "qwen_primary", "code task routed to qwen_code primary lane"),
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

    spawns: list[dict] = []
    real_create_subprocess_exec = asyncio.create_subprocess_exec
    cli_pid_file = tmp_path / "cli.pid"
    child_pid_file = tmp_path / "child.pid"
    child_spawn = (
        _CHILD_SPAWN.format(child_pid_file=str(child_pid_file))
        if with_grandchild
        else ""
    )
    script = _CLI_SCRIPT.format(
        cli_pid_file=str(cli_pid_file),
        child_spawn=child_spawn,
        sleep_seconds=sleep_seconds,
    )

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


def _cancel_when_cli_is_running(
    token: CancelToken,
    tmp_path: Path,
    record: dict,
) -> threading.Thread:
    """Set the token from a separate thread once the CLI and its child exist."""

    def _run() -> None:
        cli_pid_file = tmp_path / "cli.pid"
        child_pid_file = tmp_path / "child.pid"
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if cli_pid_file.exists() and child_pid_file.exists():
                break
            time.sleep(0.02)
        record["cli_pid"] = _read_pid(cli_pid_file)
        record["child_pid"] = _read_pid(child_pid_file)
        record["set_at"] = time.monotonic()
        token.set("chat_run_cancelled")
        while not _is_dead_or_zombie(record["cli_pid"]) and time.monotonic() < deadline:
            time.sleep(0.02)
        record["cli_dead_at"] = time.monotonic()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _fetch_usage_rows(ledger_db: Path) -> list[dict]:
    import sqlite3

    with sqlite3.connect(ledger_db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM llm_usage_log")]


async def _run_delegation() -> dict:
    # The standalone (unscoped) delegation shape: mirrors what
    # ``UnifiedToolExecutor._normalize_params`` derives for a context without
    # plan_id / task_id (the shape delegate_task drives).
    return await code_executor_module.code_executor_handler(
        task="produce a report",
        allowed_tools="Bash,Write,Read",
        session_id="session-x",
        plan_id=None,
        task_id=None,
        require_task_context=False,
    )


# --- cancellation end to end ---------------------------------------------------


async def test_cancelled_delegation_kills_the_cli_process_group_and_reports_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    spawns = _install_cli_harness(monkeypatch, tmp_path)
    token = CancelToken()
    cancellation.set_cancel_token(token)
    record: dict = {}
    cancel_thread = _cancel_when_cli_is_running(token, tmp_path, record)

    result = await _run_delegation()
    cancel_thread.join(timeout=15.0)

    assert record.get("set_at"), "the CLI never reached its long-running phase"
    # 1. The process really died, and so did the child it spawned: the group kill
    #    reaches beyond the CLI binary itself.
    _assert_process_dead(record["cli_pid"], label="CLI")
    _assert_process_dead(record["child_pid"], label="CLI child")
    # 2. Within the promised bound of the token being set.
    assert record["cli_dead_at"] - record["set_at"] <= CANCEL_LATENCY_BOUND_SECONDS
    # 3. The CLI is spawned as its own session leader so a group signal can never
    #    reach the server that delegated.
    assert spawns and spawns[0]["start_new_session"] is True
    # 4. No retry after a cancellation.
    assert len(spawns) == 1
    # 5. The payload says "cancelled" — not success, not a task failure, and not a
    #    retryable infrastructure failure.
    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["execution_status"] == "cancelled"
    assert result["failure_kind"] == "cancelled"
    assert result["error_category"] == "cancelled"
    assert DELEGATION_CANCELLED_MARKER in result["stderr"]
    assert "runtime_failure" not in result
    assert result.get("fallback_used") is not True
    assert result["error"] and "cancelled" in result["error"].lower()
    # 6. The partial spend is still booked, with a distinguishable status.
    rows = _fetch_usage_rows(ledger_db)
    assert rows and rows[0]["call_status"] == "cancelled"


async def test_without_a_cancel_token_the_cli_runs_to_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    """Control: no token bound ⇒ the pre-existing path, unchanged."""
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    sleep_seconds = 0.8
    spawns = _install_cli_harness(
        monkeypatch, tmp_path, with_grandchild=False, sleep_seconds=sleep_seconds
    )

    assert cancellation.current_cancel_token() is None
    started_at = time.monotonic()
    result = await _run_delegation()
    elapsed = time.monotonic() - started_at

    assert spawns and spawns[0]["start_new_session"] is True
    assert len(spawns) == 1
    assert elapsed >= sleep_seconds
    assert "cancelled" not in result
    assert result["success"] is True
    assert result["execution_status"] == "completed"
    assert result["failure_kind"] is None
    assert "error_category" not in result
    rows = _fetch_usage_rows(ledger_db)
    assert rows and rows[0]["call_status"] == "ok"


async def test_token_already_set_before_the_delegation_starts_terminates_the_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ledger_db: Path,
) -> None:
    """A stop request that lands before the delegation still ends the CLI."""
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    spawns = _install_cli_harness(monkeypatch, tmp_path)
    token = CancelToken()
    token.set("chat_run_cancelled")
    cancellation.set_cancel_token(token)

    result = await _run_delegation()

    assert spawns, "the delegation must still have attempted the CLI"
    _assert_process_dead(spawns[0]["pid"], label="CLI")
    assert len(spawns) == 1
    assert result["cancelled"] is True
    assert result["success"] is False
    assert result["execution_status"] == "cancelled"


# --- the kill mechanism itself --------------------------------------------------


async def test_terminate_cli_process_group_kills_the_leader_and_its_child(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "leader.pid"
    child_file = tmp_path / "member.pid"
    script = _CLI_SCRIPT.format(
        cli_pid_file=str(pid_file),
        child_spawn=_CHILD_SPAWN.format(child_pid_file=str(child_file)),
        sleep_seconds=120.0,
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10.0
    while not (pid_file.exists() and child_file.exists()) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    leader_pid = _read_pid(pid_file)
    member_pid = _read_pid(child_file)

    await code_executor_qwen_module._terminate_cli_process_group(
        process, cli_label="Test CLI"
    )

    _assert_process_dead(leader_pid, label="leader")
    _assert_process_dead(member_pid, label="group member")


async def test_terminate_cli_process_group_never_signals_our_own_group() -> None:
    """Safety: a child sharing our process group must not take the server down."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    child_pid = process.pid
    assert os.getpgid(child_pid) == os.getpgrp()

    await code_executor_qwen_module._terminate_cli_process_group(
        process, cli_label="Same-group CLI"
    )

    _assert_process_dead(child_pid, label="same-group child")
    # This test process shares that group and is obviously still alive.
    assert os.kill(os.getpid(), 0) is None


# --- result semantics ----------------------------------------------------------


def test_cancel_marker_is_not_mistaken_for_an_infrastructure_failure() -> None:
    assert _is_qwen_no_output_timeout(DELEGATION_CANCELLED_NOTE) is False
    assert _is_qwen_container_infrastructure_error(DELEGATION_CANCELLED_NOTE, "") is False


def _delegation_spec():
    from app.services.plans.task_delegate_executor import TaskDelegationSpec

    return TaskDelegationSpec(
        task_name="Audit the pipeline",
        task_instruction="Audit it",
        task_prompt="Audit it",
        executor_backend="qwen_code",
    )


def test_cancelled_payload_maps_to_a_distinct_delegation_status() -> None:
    from app.services.plans.task_delegate_executor import CodeAgentTaskDelegateExecutor

    payload = {
        "success": False,
        "result": {
            "success": False,
            "cancelled": True,
            "execution_status": "cancelled",
            "failure_kind": "cancelled",
            "error_category": "cancelled",
            "error_summary": "Sub-agent delegation was cancelled by user request.",
        },
    }

    result = CodeAgentTaskDelegateExecutor._to_delegation_result(
        _delegation_spec(), payload
    )

    assert result.status == "cancelled"
    assert result.metadata["cancelled"] is True
    assert result.metadata["failure_kind"] == "cancelled"
    assert result.metadata["error_category"] == "cancelled"
    assert result.summary


def test_uncancelled_payload_keeps_the_previous_metadata_shape() -> None:
    from app.services.plans.task_delegate_executor import CodeAgentTaskDelegateExecutor

    payload = {
        "success": True,
        "result": {"success": True, "execution_status": "completed"},
    }

    result = CodeAgentTaskDelegateExecutor._to_delegation_result(
        _delegation_spec(), payload
    )

    assert result.status == "completed"
    assert "cancelled" not in result.metadata


async def test_delegate_task_reports_a_cancelled_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.plans.task_delegate_executor import TaskDelegationResult
    from tool_box.tools_impl import delegate_task as delegate_task_module

    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")

    class _Executor:
        def execute(self, _spec, **_kwargs):
            return TaskDelegationResult(
                status="cancelled",
                summary="",
                metadata={"cancelled": True},
            )

    monkeypatch.setattr(delegate_task_module, "_new_executor", lambda: _Executor())

    payload = await delegate_task_module.delegate_task_handler(goal="Audit the pipeline")

    assert payload["status"] == "cancelled"
    assert payload["success"] is False
    assert payload["summary"] == "Sub-agent delegation was cancelled by user request."
    assert set(payload) == {
        "tool",
        "success",
        "status",
        "summary",
        "artifact_paths",
        "usage",
        "trace_ref",
    }
