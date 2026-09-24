"""Explicit code-mode kernel shutdown hook (app lifespan finally path)."""

from __future__ import annotations

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module


@pytest.fixture(autouse=True)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.mark.timeout(60)
def test_shutdown_disposes_processes_threads_and_registry(tmp_path):
    ctx = ToolContext(session_id="shutdown-test", work_dir=str(tmp_path))
    result = kernel_module.run_cell(
        "print('alive')",
        session_id="shutdown-test",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert result["status"] == "success"
    assert kernel_module._KERNELS, "expected a registered kernel"

    kernel = next(iter(kernel_module._KERNELS.values()))
    proc = kernel.proc
    rpc_thread = kernel.rpc_server._thread
    assert proc is not None and proc.poll() is None
    assert rpc_thread is not None and rpc_thread.is_alive()

    kernel_module.shutdown_code_mode_kernels()

    assert kernel_module._KERNELS == {}
    assert proc.poll() is not None  # process group killed
    assert not rpc_thread.is_alive()  # RPC server thread stopped
    assert kernel.rpc_server is None

    # Idempotent: a second call is a no-op and never raises.
    kernel_module.shutdown_code_mode_kernels()
    assert kernel_module._KERNELS == {}


@pytest.mark.timeout(60)
def test_shutdown_then_fresh_kernel_on_next_cell(tmp_path):
    ctx = ToolContext(session_id="shutdown-restart", work_dir=str(tmp_path))
    first = kernel_module.run_cell(
        "kept = 7",
        session_id="shutdown-restart",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert first["status"] == "success"
    kernel_module.shutdown_code_mode_kernels()
    second = kernel_module.run_cell(
        "print('restarted')",
        session_id="shutdown-restart",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert second["status"] == "success"
    assert second["kernel"]["reused"] is False
    assert second["kernel"]["execution_count"] == 1
