"""The official-SDK MCP server exposes only an explicit read-only allowlist."""

from __future__ import annotations

from mcp import Client

from tool_box import mcp_server
from tool_box.tools import get_tool_registry

# Tools that run code, write files, delegate work or reach shells must never be served.
FORBIDDEN = {
    "execute_code",
    "code_executor",
    "delegate_task",
    "terminal_session",
    "file_operations",
    "bio_tools",
    "deliverable_submit",
}


def _connect():
    # Opened inside each test: an async-generator fixture would enter and exit
    # anyio's task group from different tasks.
    return Client(mcp_server.build_server())


async def test_lists_only_allowlisted_read_only_tools():
    async with _connect() as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
        assert set(listed) == set(mcp_server.EXPOSED_TOOLS)
        assert not FORBIDDEN & set(listed)
        for tool in listed.values():
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False


async def test_allowlist_is_a_subset_of_registry_read_only_tools():
    mcp_server.build_server()
    registry = {t.name: t for t in get_tool_registry().list_tools()}
    for name in mcp_server.EXPOSED_TOOLS:
        assert registry[name].is_read_only and not registry[name].is_destructive


async def test_server_internal_arguments_are_not_advertised():
    async with _connect() as client:
        for tool in (await client.list_tools()).tools:
            assert not set(tool.input_schema.get("properties", {})) & mcp_server._HIDDEN_ARGUMENTS


async def test_unlisted_tool_is_refused():
    async with _connect() as client:
        result = await client.call_tool("execute_code", {"code": "print(1)"})
        assert result.is_error
        assert "Unknown tool" in result.content[0].text


async def test_handler_failure_result_sets_is_error():
    async with _connect() as client:
        result = await client.call_tool("load_skill", {"name": "no-such-skill"})
        assert result.is_error
        assert "skill_not_found" in result.content[0].text


async def test_caller_cannot_choose_server_side_arguments(monkeypatch):
    seen = {}

    async def spy(**kwargs):
        seen.update(kwargs)
        return {"success": True}

    # build_server() re-registers every built-in tool, so patch after it.
    server = mcp_server.build_server()
    tool = next(t for t in get_tool_registry().list_tools() if t.name == "sequence_fetch")
    monkeypatch.setattr(tool, "handler", spy)
    async with Client(server) as client:
        await client.call_tool("sequence_fetch", {"accession": "NC_001416.1", "output_name": "x", "session_id": "s"})
    assert seen == {"accession": "NC_001416.1"}


def test_oversized_result_is_truncated():
    text = mcp_server._render("x" * (mcp_server.MAX_RESULT_CHARS + 50))
    assert len(text) < mcp_server.MAX_RESULT_CHARS + 120
    assert "truncated" in text
