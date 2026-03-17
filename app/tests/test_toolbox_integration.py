from __future__ import annotations

import asyncio

from tool_box.integration import ToolBoxIntegration
from tool_box.tools import ToolDefinition, ToolRegistry


def test_toolbox_integration_filters_unsupported_handler_kwargs(monkeypatch) -> None:
    captured = {}

    async def _handler(operation: str, path: str):
        captured["operation"] = operation
        captured["path"] = path
        return {"success": True}

    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
            name="file_operations",
            description="Test tool",
            category="test",
            parameters_schema={},
            handler=_handler,
        )
    )

    monkeypatch.setattr("tool_box.integration.get_tool_registry", lambda: registry)

    result = asyncio.run(
        ToolBoxIntegration().call_tool(
            "file_operations",
            operation="read",
            path="/tmp/demo.txt",
            session_id="session_demo",
        )
    )

    assert result == {"success": True}
    assert captured == {"operation": "read", "path": "/tmp/demo.txt"}
