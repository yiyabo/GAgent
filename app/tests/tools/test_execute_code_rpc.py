"""RPC bridge tests: token fail-closed, allowlist/budget enforcement, authority, e2e dispatch."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tool_box.context import ToolContext
from tool_box.tools import register_tool
from tool_box.tools_impl.execute_code import kernel as kernel_module
from tool_box.tools_impl.execute_code.rpc import KernelRPCServer


def _decode(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8").strip())


def _fake_kernel(**overrides) -> SimpleNamespace:
    kernel = SimpleNamespace(
        rpc_token="secret-token",
        authority=SimpleNamespace(active=True, tool_context=None),
        allowlist=frozenset({"echo_fake"}),
        tool_call_counter=[0],
        max_tool_calls=2,
    )
    for key, value in overrides.items():
        setattr(kernel, key, value)
    return kernel


@pytest.fixture()
def _register_echo_tool():
    async def echo_fake_handler(text: str, tool_context=None):
        return {
            "echo": text,
            "session": getattr(tool_context, "session_id", None),
            "success": True,
        }

    register_tool(
        name="echo_fake",
        description="fake echo tool for code-mode tests",
        category="test",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=echo_fake_handler,
    )
    yield


# --- token -----------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rpc_rejects_wrong_token():
    server = KernelRPCServer(_fake_kernel())
    response = _decode(await server._handle_request(
        {"id": 1, "tool": "echo_fake", "args": {"text": "x"}, "token": "nope"}
    ))
    assert response["error"] == "Unauthorized RPC request"


@pytest.mark.asyncio()
async def test_rpc_rejects_empty_request_token():
    server = KernelRPCServer(_fake_kernel())
    response = _decode(await server._handle_request(
        {"id": 1, "tool": "echo_fake", "args": {}, "token": ""}
    ))
    assert response["error"] == "Unauthorized RPC request"


@pytest.mark.asyncio()
async def test_rpc_empty_server_token_fails_closed():
    # Even a matching empty request token must not authenticate.
    server = KernelRPCServer(_fake_kernel(rpc_token=""))
    response = _decode(await server._handle_request(
        {"id": 1, "tool": "echo_fake", "args": {}, "token": ""}
    ))
    assert response["error"] == "Unauthorized RPC request"


# --- authority --------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rpc_rejects_when_no_cell_bound():
    server = KernelRPCServer(_fake_kernel(authority=None))
    response = _decode(await server._handle_request(
        {"id": 1, "tool": "echo_fake", "args": {"text": "x"}, "token": "secret-token"}
    ))
    assert "No active execute_code cell" in response["error"]


@pytest.mark.asyncio()
async def test_rpc_rejects_after_cell_settled():
    retired = SimpleNamespace(active=False, tool_context=None)
    server = KernelRPCServer(_fake_kernel(authority=retired))
    response = _decode(await server._handle_request(
        {"id": 1, "tool": "echo_fake", "args": {"text": "x"}, "token": "secret-token"}
    ))
    assert "No active execute_code cell" in response["error"]


# --- allowlist / budget -------------------------------------------------------


@pytest.mark.asyncio()
async def test_rpc_enforces_allowlist_without_consuming_budget():
    kernel = _fake_kernel()
    server = KernelRPCServer(kernel)
    response = _decode(await server._handle_request(
        {"id": 1, "tool": "terminal_session", "args": {}, "token": "secret-token"}
    ))
    assert "not available in execute_code" in response["error"]
    assert "echo_fake" in response["error"]
    assert kernel.tool_call_counter[0] == 0  # refusals are free


@pytest.mark.asyncio()
async def test_rpc_enforces_per_cell_budget(_register_echo_tool):
    kernel = _fake_kernel(max_tool_calls=1)
    server = KernelRPCServer(kernel)
    first = _decode(await server._handle_request(
        {"id": 1, "tool": "echo_fake", "args": {"text": "a"}, "token": "secret-token"}
    ))
    assert first["result"]["echo"] == "a"
    assert kernel.tool_call_counter[0] == 1
    second = _decode(await server._handle_request(
        {"id": 2, "tool": "echo_fake", "args": {"text": "b"}, "token": "secret-token"}
    ))
    assert "Tool call limit reached (1)" in second["error"]
    assert kernel.tool_call_counter[0] == 1  # budget refusal is free
    assert second["id"] == 2


# --- end-to-end through a live kernel -----------------------------------------


@pytest.fixture(autouse=True)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.mark.timeout(60)
def test_end_to_end_dispatch_of_registered_tool(tmp_path, _register_echo_tool, monkeypatch):
    monkeypatch.setenv("CODE_MODE_ALLOWED_TOOLS", "echo_fake")
    ctx = ToolContext(session_id="rpc-e2e", work_dir=str(tmp_path))
    code = (
        "from gagent_tools import echo_fake\n"
        "res = echo_fake(text='hello rpc')\n"
        "print(type(res).__name__, res['echo'], res['session'])\n"
    )
    result = kernel_module.run_cell(
        code,
        session_id="rpc-e2e",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert result["status"] == "success", result.get("error")
    assert result["output"].strip() == "dict hello rpc rpc-e2e"
    assert result["tool_calls_made"] == 1


@pytest.mark.timeout(60)
def test_end_to_end_allowlist_refusal_reaches_cell(tmp_path, _register_echo_tool, monkeypatch):
    monkeypatch.setenv("CODE_MODE_ALLOWED_TOOLS", "echo_fake")
    ctx = ToolContext(session_id="rpc-e2e-blocked", work_dir=str(tmp_path))
    # The stub module only exposes echo_fake, so call the transport directly
    # to verify the server-side allowlist refuses a different tool name.
    code = (
        "import gagent_tools\n"
        "print(gagent_tools._call('terminal_session', {'operation': 'list'}))\n"
    )
    result = kernel_module.run_cell(
        code,
        session_id="rpc-e2e-blocked",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert result["status"] == "success", result.get("error")
    assert "not available in execute_code" in result["output"]
