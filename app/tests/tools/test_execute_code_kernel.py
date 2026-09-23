"""Kernel lifecycle tests for execute_code: echo, cross-cell state, reset, kill contract."""

from __future__ import annotations

import os

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module


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
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)

    # Next call starts a fresh kernel (state did not carry over).
    followup = _run("print('fresh start')", ctx)
    assert followup["status"] == "success"
    assert followup["kernel"]["reused"] is False
    assert followup["kernel"]["execution_count"] == 1


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
