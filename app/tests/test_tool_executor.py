from __future__ import annotations

import asyncio

import tool_box

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
