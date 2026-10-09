"""literature_pipeline must survive the framework ``tool_context`` kwarg (LOCAL_INFRA §116).

``UnifiedToolExecutor`` hands every handler ``tool_context=ToolContext(...)``
and ``prepare_handler_kwargs`` only strips it for handlers *without*
``**kwargs``. The 2026-09-29 metering wrapper has ``**kwargs`` and forwarded
the object straight into ``_literature_pipeline_handler_impl`` (which takes
its scope as explicit session_id / task_id / ancestor_chain), so every
production call since then raised ``TypeError: unexpected keyword argument
'tool_context'`` — first real hit 2026-10-09 by plan #183.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from tool_box.call_utils import prepare_handler_kwargs
from tool_box.context import ToolContext
from tool_box.tools_impl import invocation_meter
from tool_box.tools_impl import literature_pipeline as lp


@pytest.fixture
def _impl_spy(monkeypatch: pytest.MonkeyPatch):
    calls: List[Dict[str, Any]] = []
    metered: List[Dict[str, Any]] = []

    async def _fake_impl(query: str, **kwargs: Any) -> Dict[str, Any]:
        calls.append({"query": query, **kwargs})
        return {"tool": "literature_pipeline", "success": True, "query": query}

    monkeypatch.setattr(lp, "_literature_pipeline_handler_impl", _fake_impl)
    monkeypatch.setattr(
        invocation_meter, "record_tool_invocation", lambda **kw: metered.append(kw)
    )
    return calls, metered


def test_wrapper_drops_tool_context_and_forwards_the_rest(_impl_spy) -> None:
    calls, metered = _impl_spy
    ctx = ToolContext(session_id="s1", plan_id=183, task_id=2)
    result = asyncio.run(
        lp.literature_pipeline_handler(
            "phage depolymerase", tool_context=ctx, max_results=3, download_pdfs=False
        )
    )
    assert result["success"] is True
    assert calls == [
        {"query": "phage depolymerase", "max_results": 3, "download_pdfs": False}
    ]
    assert metered and metered[0]["tool_name"] == "literature_pipeline"
    assert metered[0]["call_status"] == "ok"


def test_prepare_handler_kwargs_cannot_strip_it_for_a_kwargs_wrapper() -> None:
    """Pin the routing fact the wrapper has to compensate for."""
    kwargs = {"query": "q", "tool_context": ToolContext()}
    assert "tool_context" in prepare_handler_kwargs(lp.literature_pipeline_handler, kwargs)
    assert "tool_context" not in prepare_handler_kwargs(
        lp._literature_pipeline_handler_impl, kwargs
    )


def test_registered_handler_survives_the_executor_hop(_impl_spy) -> None:
    """End to end through the integration layer, exactly as the executor calls it."""
    calls, _ = _impl_spy
    from tool_box.integration import ToolBoxIntegration

    result = asyncio.run(
        ToolBoxIntegration().call_tool(
            "literature_pipeline",
            tool_context=ToolContext(session_id="s1"),
            query="phage depolymerase",
            max_results=2,
        )
    )
    assert result["success"] is True
    assert calls[-1] == {"query": "phage depolymerase", "max_results": 2}


# ---------------------------------------------------------------------------
# Output-root guard: production mounts APP_RUNTIME_ROOT outside /app, so the
# session default dir is not under the project tree. The guard must accept the
# configured runtime root or every chat-lane call dies with
# out_dir_outside_project (observed 2026-09-26 and 2026-10-10).
# ---------------------------------------------------------------------------


def test_runtime_root_outside_project_is_an_allowed_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    runtime_root = tmp_path / "mounted_runtime"
    runtime_root.mkdir()
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    assert lp._runtime_root() == runtime_root.resolve()
    assert lp._is_allowed_output_dir(runtime_root / "session_x" / "tool_outputs" / "lit")
    assert lp._is_allowed_output_dir(lp._PROJECT_ROOT / "runtime" / "lit_reviews" / "x")
    assert not lp._is_allowed_output_dir(tmp_path / "elsewhere")


def test_runtime_root_falls_back_to_in_tree_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_RUNTIME_ROOT", raising=False)
    assert lp._runtime_root() == lp._RUNTIME_DIR.resolve()
    assert lp._is_allowed_output_dir(lp._RUNTIME_DIR / "lit_reviews" / "pack")
