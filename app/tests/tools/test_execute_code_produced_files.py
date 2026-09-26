"""produced_files collection (G1 artifact landing): files a cell writes under
the session workspace are reported back session-relative, so the loop guards,
inline images, and deliverable_submit can resolve them against the session root.
"""

from __future__ import annotations

import os
import time

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module


@pytest.fixture(autouse=True)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(session_id="produced-test", work_dir=str(tmp_path))


def _run(code: str, ctx: ToolContext, reset: bool = False):
    return kernel_module.run_cell(
        code,
        session_id=str(ctx.session_id),
        work_dir=ctx.work_dir,
        reset=reset,
        tool_context=ctx,
    )


@pytest.mark.timeout(60)
def test_cell_created_files_are_reported_session_relative(ctx):
    result = _run(
        "from pathlib import Path\n"
        "Path('results').mkdir(exist_ok=True)\n"
        "Path('results/chart.png').write_bytes(b'png')\n"
        "Path('notes.txt').write_text('hi')\n",
        ctx,
    )
    assert result["success"] is True
    produced = result.get("produced_files") or []
    assert "results/chart.png" in produced
    assert "notes.txt" in produced
    # Paths are session-relative — no absolute prefix may leak to the surfaces.
    assert all(not path.startswith("/") for path in produced)


@pytest.mark.timeout(60)
def test_preexisting_files_and_scratch_are_not_reported(ctx, tmp_path):
    (tmp_path / "old.txt").write_text("old")
    first = _run("print('warm-up')", ctx)
    assert first["success"] is True
    assert "old.txt" not in (first.get("produced_files") or [])

    second = _run("print('quiet cell')", ctx)
    assert second["success"] is True
    produced = second.get("produced_files") or []
    # Nothing the cell did not touch: no pre-existing files, no scratch spills
    # or RPC stubs from the code-mode scratch subtree.
    assert "old.txt" not in produced
    assert all("scratch/" not in path for path in produced)


@pytest.mark.timeout(60)
def test_overwritten_files_count_as_produced(ctx, tmp_path):
    target = tmp_path / "results"
    target.mkdir()
    data_file = target / "data.csv"
    data_file.write_text("a,b\n1,2\n")
    # Backdate so only an actual rewrite crosses the watermark.
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(data_file, ns=(old_ns, old_ns))

    result = _run(
        "from pathlib import Path\n"
        "Path('results/data.csv').write_text('a,b\\n3,4\\n')\n",
        ctx,
    )
    assert result["success"] is True
    assert "results/data.csv" in (result.get("produced_files") or [])


@pytest.mark.timeout(60)
def test_no_workspace_no_collection(tmp_path, monkeypatch):
    # Without a real session work_dir the kernel still runs (fallback scratch,
    # redirected here so the test leaves nothing in the repo), but
    # produced_files collection stays off — cross-session paths would be
    # meaningless to the session's artifact surfaces.
    monkeypatch.setenv("CODE_MODE_SCRATCH_DIR", str(tmp_path / "cm_scratch"))
    ctx = ToolContext(session_id="produced-test-nowd", work_dir="")
    result = kernel_module.run_cell(
        "from pathlib import Path\nPath('x.txt').write_text('1')\nprint('ok')",
        session_id="produced-test-nowd",
        work_dir="",
        reset=False,
        tool_context=ctx,
    )
    assert result["success"] is True
    assert "produced_files" not in result
