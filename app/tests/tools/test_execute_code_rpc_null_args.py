"""Code-mode RPC must drop ``None`` arguments before dispatch (LOCAL_INFRA §116).

The generated ``gagent_tools`` stubs give every optional parameter ``= None``
and forward the whole property set, so an argument the cell left unset reaches
the host as an explicit ``None``. ``prepare_handler_kwargs`` passes it through
(it is a declared parameter), and a strict handler then crashes — the first
real hit was ``literature_pipeline``'s ``int(max_results)`` from a plan-task
kernel on 2026-10-10. Omitted means omitted: the server strips ``None`` so the
handler's own defaults apply.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tool_box.context import ToolContext
from tool_box.tools import register_tool
from tool_box.tools_impl.execute_code.rpc import KernelRPCServer


@pytest.fixture()
def _register_strict_tool():
    seen = []

    async def strict_fake_handler(text: str, limit: int = 5, tool_context=None):
        seen.append({"text": text, "limit": limit})
        return {"echo": text, "limit": int(limit), "success": True}

    register_tool(
        name="strict_fake",
        description="fake tool with a strict integer default for code-mode tests",
        category="test",
        parameters_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["text"],
        },
        handler=strict_fake_handler,
    )
    return seen


def _server() -> KernelRPCServer:
    kernel = SimpleNamespace(
        rpc_token="secret-token",
        authority=SimpleNamespace(active=True, tool_context=None),
        allowlist=frozenset({"strict_fake"}),
        tool_call_counter=[0],
        max_tool_calls=4,
    )
    return KernelRPCServer(kernel)


@pytest.mark.asyncio()
async def test_dispatch_drops_none_args_so_handler_defaults_apply(_register_strict_tool):
    seen = _register_strict_tool
    result = await _server()._dispatch(
        "strict_fake",
        {"text": "x", "limit": None},
        ToolContext(session_id="s1"),
    )
    assert result["success"] is True
    assert result["limit"] == 5
    assert seen == [{"text": "x", "limit": 5}]


@pytest.mark.asyncio()
async def test_dispatch_keeps_explicit_values(_register_strict_tool):
    seen = _register_strict_tool
    result = await _server()._dispatch(
        "strict_fake", {"text": "y", "limit": 9}, ToolContext(session_id="s1")
    )
    assert result["limit"] == 9
    assert seen == [{"text": "y", "limit": 9}]


@pytest.mark.asyncio()
async def test_dispatch_rejects_non_object_args():
    with pytest.raises(TypeError):
        await _server()._dispatch("strict_fake", ["not", "a", "dict"], ToolContext())
