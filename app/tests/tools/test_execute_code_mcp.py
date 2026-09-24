"""MCP tools/list gating: execute_code hidden unless CODE_MODE_ENABLED=1.

The tool stays registered in the runtime registry (handler keeps returning a
clean code_mode_disabled error); only MCP discovery is gated.
"""

from __future__ import annotations

import pytest

from tool_box.server import ToolBoxMCPServer
from tool_box.tool_registry import register_all_tools
from tool_box.tools import get_tool_registry


@pytest.fixture()
def _registered():
    register_all_tools()
    yield


async def _list_tool_names(server: ToolBoxMCPServer) -> list:
    response = await server._handle_list_tools()
    return [tool["name"] for tool in response["result"]["tools"]]


@pytest.mark.asyncio()
async def test_tools_list_hides_execute_code_when_disabled(_registered, monkeypatch):
    monkeypatch.delenv("CODE_MODE_ENABLED", raising=False)
    names = await _list_tool_names(ToolBoxMCPServer())
    assert "execute_code" not in names
    # Runtime registration is intentionally preserved: dispatch validation and
    # the handler's code_mode_disabled error path are unaffected.
    assert get_tool_registry().get_tool("execute_code") is not None


@pytest.mark.asyncio()
async def test_tools_list_shows_execute_code_when_enabled(_registered, monkeypatch):
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    names = await _list_tool_names(ToolBoxMCPServer())
    assert "execute_code" in names


@pytest.mark.asyncio()
async def test_tools_list_other_tools_unaffected_by_flag(_registered, monkeypatch):
    monkeypatch.delenv("CODE_MODE_ENABLED", raising=False)
    off_names = await _list_tool_names(ToolBoxMCPServer())
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    on_names = await _list_tool_names(ToolBoxMCPServer())
    assert set(on_names) - set(off_names) == {"execute_code"}
