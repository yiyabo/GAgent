"""Kernel lifecycle tests for execute_code: echo, cross-cell state, reset, kill contract."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module

# A process-group member killed by the timeout path ends up in one of two shapes:
# reaped (the probe raises ESRCH) or an unreaped zombie (a container PID 1 with no
# child reaper never calls waitpid, so the entry lingers). Both are the same
# kernel fact — the process body is gone — so the assertion accepts either and
# still fails on a runnable grandchild.
_GROUP_MEMBER_EXIT_TIMEOUT = 5.0


def _process_state(pid: int) -> str:
    """Best-effort single-letter process state; "" when it cannot be observed."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        stat = ""
    if stat:
        # Field 2 (comm) may contain spaces and parens; state follows the last ')'.
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
    """True once *pid* no longer runs: reaped, or a zombie awaiting reaping."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # still alive, just not ours to signal
    return _process_state(pid) == "Z"


def _assert_process_group_member_dead(pid: int) -> None:
    """Wait briefly, then require the process body to be gone."""
    deadline = time.monotonic() + _GROUP_MEMBER_EXIT_TIMEOUT
    while not _is_dead_or_zombie(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _is_dead_or_zombie(pid), (
        f"timeout kill missed part of the process group: pid {pid} is still "
        f"runnable (state={_process_state(pid)!r})"
    )


@pytest.fixture(autouse=True)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(session_id="kernel-test", work_dir=str(tmp_path))


def _run(code: str, ctx: ToolContext, reset: bool = False):
    return kernel_module.run_cell(
        code,
        session_id=str(ctx.session_id),
        work_dir=ctx.work_dir,
        reset=reset,
        tool_context=ctx,
    )


@pytest.mark.timeout(60)
def test_echo_cell_returns_stdout_and_kernel_metadata(ctx):
    result = _run("print('hello kernel')", ctx)
    assert result["status"] == "success"
    assert result["success"] is True
    assert result["output"].strip() == "hello kernel"
    assert result["exit_code"] == 0
    kernel = result["kernel"]
    assert kernel["reused"] is False
    assert kernel["execution_count"] == 1
    assert kernel["state_reset"] is False


@pytest.mark.timeout(60)
def test_variables_survive_across_cells(ctx):
    first = _run("shared_state = 41", ctx)
    assert first["status"] == "success"
    second = _run("print(shared_state + 1)", ctx)
    assert second["status"] == "success"
    assert second["output"].strip() == "42"
    assert second["kernel"]["reused"] is True
    assert second["kernel"]["execution_count"] == 2


@pytest.mark.timeout(60)
def test_reset_rebuilds_kernel_and_drops_state(ctx):
    _run("doomed_var = 1", ctx)
    result = _run("print(doomed_var)", ctx, reset=True)
    assert result["status"] == "error"
    assert "NameError" in result["error"]
    assert result["kernel"]["state_reset"] is True
    assert result["kernel"]["reused"] is False
    assert result["kernel"]["execution_count"] == 1


@pytest.mark.timeout(60)
def test_cell_exception_reports_traceback_without_killing_kernel(ctx):
    bad = _run("raise ValueError('boom')", ctx)
    assert bad["status"] == "error"
    assert bad["success"] is False
    assert "ValueError: boom" in bad["error"]
    # Kernel survives a plain cell exception.
    followup = _run("print('still alive')", ctx)
    assert followup["status"] == "success"
    assert followup["kernel"]["reused"] is True


@pytest.mark.timeout(60)
def test_timeout_kills_whole_process_group_and_reports_state_loss(
    ctx, tmp_path, monkeypatch
):
    monkeypatch.setenv("CODE_MODE_CELL_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("CODE_MODE_KILL_GRACE_SECONDS", "0.5")
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    result = _run(code, ctx)
    assert result["status"] == "timeout"
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "state was lost" in result["error"]

    grandchild_pid = int(pid_file.read_text().strip())
    # Equivalent, platform-independent form of "the whole process group is gone":
    # a normal init reaps the orphaned grandchild (the probe sees ESRCH), while a
    # container whose PID 1 has no child reaper leaves it a zombie (the probe
    # still resolves the entry). Either way the process body must be dead; a
    # survivable grandchild still fails the assertion.
    _assert_process_group_member_dead(grandchild_pid)

    # Next call starts a fresh kernel (state did not carry over) — and it must
    # say so: `state_reset` used to come back False right after a state-losing
    # kill, contradicting the kernel metadata contract in the tool description.
    followup = _run("print('fresh start')", ctx)
    assert followup["status"] == "success"
    assert followup["kernel"]["reused"] is False
    assert followup["kernel"]["execution_count"] == 1
    assert followup["kernel"]["state_reset"] is True

    # The marker is consumed: the call after that reuses a live kernel again.
    settled = _run("print('reused now')", ctx)
    assert settled["kernel"]["reused"] is True
    assert settled["kernel"]["state_reset"] is False


@pytest.mark.timeout(60)
def test_kernel_exits_when_the_host_dies(ctx):
    """Host death must not leave an orphan kernel behind.

    The kernel runs in its own session (start_new_session=True), so it never
    sees the host's signals; it learns the host is gone from EOF on the
    parent-liveness pipe. Closing the write end here stands in for the host
    being SIGKILLed — the case where the app's atexit path cannot run.
    """
    assert _run("x = 41", ctx)["status"] == "success"

    with kernel_module._REGISTRY_LOCK:
        live = [
            kernel
            for key, kernel in kernel_module._KERNELS.items()
            if key[0] == str(ctx.session_id)
        ]
    assert len(live) == 1, "expected exactly one kernel for the session"
    kernel = live[0]
    proc = kernel.proc
    assert proc is not None and proc.poll() is None
    assert kernel.parent_fd_w is not None

    os.close(kernel.parent_fd_w)
    kernel.parent_fd_w = None

    assert proc.wait(timeout=10) == 0

    # The session keeps working: the next cell respawns a fresh kernel.
    followup = _run("print('after host death')", ctx)
    assert followup["status"] == "success"
    assert followup["kernel"]["reused"] is False
    assert followup["kernel"]["state_reset"] is True


@pytest.mark.timeout(60)
def test_runner_and_stubs_are_written_into_kernel_dir(ctx):
    from tool_box.tools_impl.execute_code.config import resolve_scratch_dir

    result = _run("print('spawned')", ctx)
    assert result["status"] == "success"
    kernel_dirs = list((resolve_scratch_dir(ctx.work_dir) / "kernels").glob("*"))
    assert kernel_dirs, "expected a kernel staging dir under the session scratch dir"
    assert (kernel_dirs[0] / "gagent_kernel_runner.py").is_file()
    assert (kernel_dirs[0] / "gagent_tools.py").is_file()


@pytest.mark.timeout(60)
def test_separate_sessions_get_separate_kernels(tmp_path):
    ctx_a = ToolContext(session_id="session-a", work_dir=str(tmp_path))
    ctx_b = ToolContext(session_id="session-b", work_dir=str(tmp_path))
    _run("marker = 'a'", ctx_a)
    other = kernel_module.run_cell(
        "print('marker' in dir())",
        session_id="session-b",
        work_dir=ctx_b.work_dir,
        reset=False,
        tool_context=ctx_b,
    )
    assert other["output"].strip() == "False"
    same = kernel_module.run_cell(
        "print(marker)",
        session_id="session-a",
        work_dir=ctx_a.work_dir,
        reset=False,
        tool_context=ctx_a,
    )
    assert same["output"].strip() == "a"
