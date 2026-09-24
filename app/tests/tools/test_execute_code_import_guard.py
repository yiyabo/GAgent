"""Kernel import guard: cells must not import the backend packages (app / tool_box).

The guard is a sys.meta_path blocker installed by the kernel runner; it names
gagent_tools as the intended path. Stdlib and third-party scientific packages
are unaffected.
"""

from __future__ import annotations

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module


@pytest.fixture(autouse=True)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(session_id="import-guard", work_dir=str(tmp_path))


def _run(code: str, ctx: ToolContext):
    return kernel_module.run_cell(
        code,
        session_id=str(ctx.session_id),
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )


@pytest.mark.timeout(60)
def test_import_app_is_blocked_with_actionable_error(ctx):
    result = _run("import app", ctx)
    assert result["status"] == "error"
    assert "is not allowed inside execute_code" in result["error"]
    assert "gagent_tools" in result["error"]


@pytest.mark.timeout(60)
def test_import_app_submodule_is_blocked(ctx):
    result = _run("from app.services import tool_schemas", ctx)
    assert result["status"] == "error"
    assert "is not allowed inside execute_code" in result["error"]


@pytest.mark.timeout(60)
def test_import_tool_box_is_blocked(ctx):
    result = _run("import tool_box", ctx)
    assert result["status"] == "error"
    assert "is not allowed inside execute_code" in result["error"]


@pytest.mark.timeout(60)
def test_stdlib_imports_still_work(ctx):
    result = _run("import json, os, sys, math\nprint(json.dumps({'ok': math.sqrt(4)}))", ctx)
    assert result["status"] == "success"
    assert result["output"].strip() == '{"ok": 2.0}'


@pytest.mark.timeout(60)
def test_scientific_stack_imports_still_work(ctx):
    pytest.importorskip("pandas")
    result = _run("import pandas\nprint(pandas.__name__)", ctx)
    assert result["status"] == "success"
    assert result["output"].strip() == "pandas"


@pytest.mark.timeout(60)
def test_gagent_tools_import_still_works(ctx):
    result = _run("import gagent_tools\nprint(hasattr(gagent_tools, '_call'))", ctx)
    assert result["status"] == "success"
    assert result["output"].strip() == "True"


@pytest.mark.timeout(60)
def test_blocked_import_does_not_kill_kernel(ctx):
    blocked = _run("import app", ctx)
    assert blocked["status"] == "error"
    followup = _run("print('kernel alive')", ctx)
    assert followup["status"] == "success"
    assert followup["kernel"]["reused"] is True
