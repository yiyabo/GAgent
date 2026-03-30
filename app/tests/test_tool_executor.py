from __future__ import annotations

import asyncio

import tool_box

from app.services.deliverables.publisher import PublishReport
from app.services.execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor


def test_tool_executor_ignores_empty_deliverable_report(monkeypatch) -> None:
    async def _fake_execute_tool(_tool_name: str, **_kwargs):
        return {"success": True, "operation": "read"}

    class _Publisher:
        def publish_from_tool_result(self, **_kwargs):
            return None

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    executor = UnifiedToolExecutor()
    monkeypatch.setattr(executor, "_deliverable_publisher", _Publisher())

    result = asyncio.run(
        executor.execute(
            "file_operations",
            {"operation": "read", "path": "/tmp/example.txt"},
            context=ToolExecutionContext(session_id="session_demo"),
        )
    )

    assert result["success"] is True
    assert "deliverables" not in result
    assert "deliverable_error" not in result


def test_tool_executor_uses_deliverable_submit_publish_summary(monkeypatch) -> None:
    async def _fake_execute_tool(_tool_name: str, **_kwargs):
        return {
            "success": True,
            "tool": "deliverable_submit",
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": "/tmp/plot.png", "module": "image_tabular"}],
            },
        }

    class _Publisher:
        def publish_from_tool_result(self, **_kwargs):
            return PublishReport(
                version_id="v1",
                published_files_count=3,
                published_modules=["image_tabular"],
                manifest_path="/tmp/manifest.json",
                paper_status={},
                submit_artifacts_requested=2,
                submit_artifacts_published=1,
                submit_artifacts_skipped=1,
                warnings=["artifact[1] skipped: path '/tmp/missing.png' does not exist"],
            )

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    executor = UnifiedToolExecutor()
    monkeypatch.setattr(executor, "_deliverable_publisher", _Publisher())

    result = asyncio.run(
        executor.execute(
            "deliverable_submit",
            {"artifacts": [{"path": "/tmp/plot.png", "module": "image_tabular"}]},
            context=ToolExecutionContext(session_id="session_demo"),
        )
    )

    assert result["success"] is True
    assert result["summary"].startswith("Deliverable submit published 1 artifact(s); skipped 1 with warnings")
    deliverables = result.get("deliverables") or {}
    assert deliverables["submit_artifacts_requested"] == 2
    assert deliverables["submit_artifacts_published"] == 1
    assert deliverables["submit_artifacts_skipped"] == 1
    assert deliverables["warnings"] == [
        "artifact[1] skipped: path '/tmp/missing.png' does not exist"
    ]


def test_tool_executor_phagescope_normalizes_task_id_and_operation(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_execute_tool(tool_name: str, **kwargs):
        captured["tool_name"] = tool_name
        captured["kwargs"] = dict(kwargs)
        return {"success": True, "action": kwargs.get("action")}

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    executor = UnifiedToolExecutor()
    asyncio.run(
        executor.execute(
            "phagescope",
            {"task_id": "38619", "operation": "task_detail"},
            context=ToolExecutionContext(session_id="session_demo"),
        )
    )

    assert captured["tool_name"] == "phagescope"
    kw = captured["kwargs"]
    assert kw.get("taskid") == "38619"
    assert kw.get("action") == "task_detail"
    assert "task_id" not in kw
    assert "operation" not in kw
