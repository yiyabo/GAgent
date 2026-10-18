"""Standards-compliant MCP server exposing a small, read-only slice of Tool Box.

Unlike ``server.ToolBoxMCPServer`` (a hand-rolled JSON-RPC loop that exposes the
whole registry), this module uses the official MCP SDK and publishes only an
explicit allowlist. Nothing that runs code, writes files, or delegates work is
reachable from here.

Run over stdio (the transport Codex / Claude Code / VS Code launch locally)::

    python -m tool_box.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict, Optional

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from .call_utils import prepare_handler_kwargs
from .tool_registry import register_all_tools
from .tools import ToolDefinition, get_tool_registry

logger = logging.getLogger(__name__)

SERVER_NAME = "gagent-toolbox"

# Explicit allowlist. A tool is exposed only if it is named here AND the registry
# marks it read-only and non-destructive; both conditions must hold so a later
# metadata change cannot silently widen the surface.
EXPOSED_TOOLS = frozenset({"sequence_fetch", "load_skill"})

# Arguments that name server-side state; a remote caller must not choose them.
_HIDDEN_ARGUMENTS = frozenset({"session_id", "tool_context", "output_name", "persist"})

# Arguments the server fixes for every call, whatever the caller sends. A caller
# over MCP gets results inline and never causes a write on this machine.
_FORCED_ARGUMENTS: Dict[str, Dict[str, Any]] = {"sequence_fetch": {"persist": False}}

MAX_RESULT_CHARS = 30_000


def _exposed(registry_tools) -> Dict[str, ToolDefinition]:
    return {
        t.name: t
        for t in registry_tools
        if t.name in EXPOSED_TOOLS and t.is_read_only and not t.is_destructive
    }


def _public_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Drop server-internal arguments from a tool's JSON schema."""
    public = dict(schema or {"type": "object"})
    props = {k: v for k, v in (public.get("properties") or {}).items() if k not in _HIDDEN_ARGUMENTS}
    public["properties"] = props
    required = [r for r in public.get("required", []) if r in props]
    if required:
        public["required"] = required
    else:
        public.pop("required", None)
    return public


def _render(result: Any) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n...[truncated, {len(text) - MAX_RESULT_CHARS} chars omitted]"
    return text


def _failed(result: Any) -> bool:
    return isinstance(result, dict) and result.get("success") is False


async def _list_tools(ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]) -> ListToolsResult:
    tools = [
        Tool(
            name=t.name,
            description=t.description,
            input_schema=_public_schema(t.parameters_schema),
            annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True),
        )
        for t in _exposed(get_tool_registry().list_tools()).values()
    ]
    return ListToolsResult(tools=tools)


async def _call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    tool = _exposed(get_tool_registry().list_tools()).get(params.name)
    if tool is None:
        return CallToolResult(content=[TextContent(type="text", text=f"Unknown tool: {params.name}")], is_error=True)
    arguments = {k: v for k, v in (params.arguments or {}).items() if k not in _HIDDEN_ARGUMENTS}
    arguments.update(_FORCED_ARGUMENTS.get(params.name, {}))
    try:
        result = await tool.handler(**prepare_handler_kwargs(tool.handler, arguments))
    except Exception as exc:  # the protocol needs an error result, not a crashed server
        logger.exception("MCP tool %s failed", params.name)
        return CallToolResult(content=[TextContent(type="text", text=f"{params.name} failed: {exc}")], is_error=True)
    return CallToolResult(content=[TextContent(type="text", text=_render(result))], is_error=_failed(result))


def build_server() -> Server:
    register_all_tools()
    return Server(SERVER_NAME, on_list_tools=_list_tools, on_call_tool=_call_tool)


async def serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    # stdout is the protocol channel; all logging must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
