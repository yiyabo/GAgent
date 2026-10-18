"""Containerised MCP mounts: config, run-args hardening, egress proxy, dial-back transport."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess

import pytest

from tool_box.mcp_mount import parse_entry
from tool_box.mcp_mount import container as container_module
from tool_box.mcp_mount.config import MountConfigError
from tool_box.mcp_mount.egress import EgressGrant, EgressProxy, all_global, host_matches


def _entry(**container):
    return {"transport": "stdio", "command": "srv", "container": {"image": "img:1", **container}}


# ------------------------------------------------------------------- config
def test_container_entry_parses_and_reports_isolation():
    spec = parse_entry("s", _entry(egress=["REST.Uniprot.org", "*.ebi.ac.uk"]))
    assert spec.isolation == "container"
    assert spec.container.egress == ("rest.uniprot.org", "*.ebi.ac.uk")
    assert parse_entry("h", {"transport": "stdio", "command": "x"}).isolation == "host"
    assert parse_entry("r", {"transport": "http", "url": "https://a.example/mcp"}).isolation == "remote"


@pytest.mark.parametrize(
    "bad",
    [
        {"image": "img:latest"},
        {"image": "img"},
        {"image": "img:1", "egress": ["*"]},
        {"image": "img:1", "egress": ["10.0.0.1"]},
        {"image": "img:1", "egress": ["http://x.org"]},
        {"image": "img:1", "memory": "lots"},
        {"image": "img:1", "public_web": True, "egress": ["a.example"]},
        {"image": "img:1", "cap_add": ["ALL"]},
        {"image": "img:1", "privileged": True},
        {"image": "bad image"},
    ],
)
def test_unsafe_container_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_entry("s", {"transport": "stdio", "command": "srv", "container": bad})


def test_container_is_stdio_only_and_excludes_host_cwd():
    with pytest.raises(ValueError):
        parse_entry("s", {"transport": "http", "url": "https://a.example", "container": {"image": "i:1"}})
    with pytest.raises(ValueError):
        parse_entry("s", {**_entry(), "cwd": "/srv"})


def test_digest_pinned_image_is_accepted():
    assert parse_entry("s", _entry(image="repo/img@sha256:" + "a" * 64)).container.image.startswith("repo/img@")


# --------------------------------------------------------------- run-args
def test_run_args_are_hardened_and_carry_no_host_state(monkeypatch):
    monkeypatch.delenv("MCP_CONTAINER_HOST_GATEWAY", raising=False)
    spec = parse_entry("uni", {**_entry(egress=["rest.uniprot.org"], memory="256m", cpus=0.5, pids_limit=64), "env": {"MCP_LOG_LEVEL": "warn"}})
    run = container_module.build_run_spec(spec, name="n", endpoint="tcp://app:1", token="tok", proxy_url="http://mcp:t@app:2")
    from app.ops import sandbox as sandbox_ops

    argv = sandbox_ops.build_sandbox_run_args(run)
    joined = " ".join(argv)
    for required in ("--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only", "--pids-limit=64", "--memory=256m", "--cpus=0.5", "--user 10001:10001"):
        assert required in joined, required
    assert "-v " not in joined and "--privileged" not in joined and "--network host" not in joined
    env = dict(a.split("=", 1) for a, flag in zip(argv[1:], argv) if flag == "-e")
    assert set(env) == {"HOME", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING", "PYTHONUTF8", "MCP_LOG_LEVEL", "GAGENT_MCP_ENDPOINT", "GAGENT_MCP_TOKEN", "HTTPS_PROXY", "https_proxy"}
    assert argv[-2:] == ["img:1", "srv"] or argv[-1] == "srv"


def test_containers_get_a_dead_resolver_so_dns_cannot_carry_data_out(monkeypatch):
    """Docker's embedded resolver forwards outside names even on an internal network."""
    from app.ops import sandbox as sandbox_ops

    monkeypatch.delenv("MCP_CONTAINER_HOST_GATEWAY", raising=False)
    spec = parse_entry("s", _entry(egress=["rest.uniprot.org"]))
    run = container_module.build_run_spec(spec, name="n", endpoint="tcp://a:1", token="t", proxy_url="http://mcp:t@a:2")
    argv = sandbox_ops.build_sandbox_run_args(run)
    assert argv[argv.index("--dns") + 1] == "127.0.0.1"
    monkeypatch.setenv("MCP_CONTAINER_HOST_GATEWAY", "1")
    argv = sandbox_ops.build_sandbox_run_args(container_module.build_run_spec(spec, name="n", endpoint="tcp://a:1", token="t", proxy_url=None))
    assert "--dns" in argv and "--add-host=host.docker.internal:host-gateway" in argv


def test_no_proxy_variables_without_egress():
    spec = parse_entry("s", _entry())
    run = container_module.build_run_spec(spec, name="n", endpoint="tcp://a:1", token="t", proxy_url=None)
    assert not {"HTTPS_PROXY", "https_proxy"} & set(run.env)


# ------------------------------------------------------------ egress rules
def test_host_matching_is_strict():
    assert host_matches("rest.uniprot.org", ("rest.uniprot.org",))
    assert not host_matches("rest.uniprot.org.evil.com", ("rest.uniprot.org",))
    assert not host_matches("evilrest.uniprot.org", ("rest.uniprot.org",))
    assert host_matches("www.ebi.ac.uk", ("*.ebi.ac.uk",))
    assert not host_matches("ebi.ac.uk", ("*.ebi.ac.uk",))
    assert not host_matches("xebi.ac.uk", ("*.ebi.ac.uk",))


@pytest.mark.parametrize("addrs,expected", [(["93.184.216.34"], True), (["127.0.0.1"], False), (["10.1.2.3"], False), (["169.254.169.254"], False), (["::1"], False), (["::ffff:127.0.0.1"], False), (["93.184.216.34", "10.0.0.1"], False), ([], False), (["not-an-ip"], False)])
def test_all_global(addrs, expected):
    assert all_global(addrs) is expected


async def _connect(proxy, token, target, method="CONNECT"):
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    auth = base64.b64encode(f"mcp:{token}".encode()).decode()
    writer.write(f"{method} {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
    await writer.drain()
    try:
        return (await asyncio.wait_for(reader.readline(), 5)).decode().strip(), reader, writer
    except asyncio.TimeoutError:
        return "<timeout>", reader, writer


async def _status(proxy, token, target, method="CONNECT"):
    line, _r, writer = await _connect(proxy, token, target, method)
    writer.close()
    return line


@pytest.fixture
async def echo_upstream():
    """A local TCP echo standing in for an allowed HTTPS host."""

    async def handler(reader, writer):
        data = await reader.read(100)
        writer.write(b"echo:" + data)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def test_egress_proxy_denies_everything_not_granted():
    async def resolver(host, port):
        return {"rest.uniprot.org": ["93.184.216.34"], "example.com": ["93.184.216.34"], "rebind.example.com": ["93.184.216.34", "10.0.0.5"], "internal.example.com": ["10.0.0.5"]}.get(host, [])

    async def refuse(*_a, **_k):
        raise ConnectionRefusedError()

    proxy = EgressProxy(resolver=resolver, dialer=refuse)  # nothing may really connect in this test
    await proxy.start(bind="127.0.0.1", advertise="127.0.0.1")
    try:
        narrow = proxy.register(EgressGrant("narrow", hosts=("rest.uniprot.org",)))
        web = proxy.register(EgressGrant("web", public_web=True))
        assert (await _status(proxy, "wrong", "rest.uniprot.org:443")).startswith("HTTP/1.1 407")
        for target in ("example.com:443", "rest.uniprot.org:80", "rest.uniprot.org:22", "rest.uniprot.org.evil.com:443", "evilrest.uniprot.org:443", "1.1.1.1:443", "127.0.0.1:443", "2130706433:443", "0x7f.1:443", "[::1]:443", "localhost:443", "169.254.169.254:443"):
            assert (await _status(proxy, narrow, target)).startswith("HTTP/1.1 403"), target
        for target in ("1.1.1.1:443", "127.0.0.1:443", "localhost:443", "internal.example.com:443", "rebind.example.com:443", "169.254.169.254:443"):
            assert (await _status(proxy, web, target)).startswith("HTTP/1.1 403"), target
        assert (await _status(proxy, narrow, "http://rest.uniprot.org/", method="GET")).startswith("HTTP/1.1 405")
        # an allowed target reaches the dial step (which this test makes fail with 502)
        assert (await _status(proxy, narrow, "rest.uniprot.org:443")).startswith("HTTP/1.1 502")
        assert {reason for _l, _t, reason in proxy.denied} >= {"host not in allowlist", "port not allowed", "not a plain host name"}
    finally:
        await proxy.stop()


async def test_egress_proxy_tunnels_an_allowed_host(echo_upstream):
    async def resolver(host, port):
        return ["93.184.216.34"]

    async def redirect(_address, _port):
        return await asyncio.open_connection("127.0.0.1", echo_upstream)

    proxy = EgressProxy(resolver=resolver, dialer=redirect)
    await proxy.start(bind="127.0.0.1", advertise="127.0.0.1")
    try:
        token = proxy.register(EgressGrant("narrow", hosts=("rest.uniprot.org",)))
        line, reader, writer = await _connect(proxy, token, "rest.uniprot.org:443")
        assert line.startswith("HTTP/1.1 200")
        await reader.readuntil(b"\r\n")  # blank line ending the 200 response head
        writer.write(b"hello")
        await writer.drain()
        assert b"echo:hello" in await asyncio.wait_for(reader.read(100), 5)
        writer.close()
        assert proxy.allowed["narrow"] == 1
        proxy.revoke(token)
        assert (await _status(proxy, token, "rest.uniprot.org:443")).startswith("HTTP/1.1 407")
    finally:
        await proxy.stop()


# ------------------------------------------------------- dial-back transport
async def test_dial_back_rejects_wrong_token_and_accepts_right_one():
    dial = container_module._DialBack("secret-token")
    port = await dial.start("127.0.0.1")
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b'{"token":"nope"}\n')
        await w.drain()
        assert await asyncio.wait_for(r.read(10), 5) == b""  # closed on us
        with pytest.raises(container_module.ContainerStartError):
            await dial.connection(0.3)
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        w2.write(b'{"token":"secret-token"}\n')
        await w2.drain()
        assert await dial.connection(5)
        w2.close()
    finally:
        await dial.close()


async def test_container_transport_round_trip_with_a_fake_launcher(monkeypatch):
    """The transport speaks MCP over the dial-back socket exactly as over a pipe."""
    from mcp import Client

    spec = parse_entry("fake", _entry())
    seen = {}

    async def launcher(argv, env):
        seen["argv"], seen["env"] = list(argv), dict(env)
        endpoint = env["GAGENT_MCP_ENDPOINT"].removeprefix("tcp://")
        host, _, port = endpoint.rpartition(":")
        asyncio.create_task(_fake_server_peer("127.0.0.1", int(port), env["GAGENT_MCP_TOKEN"]))
        return "cid"

    monkeypatch.setattr(container_module, "_listener_addresses", lambda: ("127.0.0.1", "127.0.0.1"))
    async with Client(container_module.container_transport(spec, launcher=launcher)) as client:
        tools = await client.list_tools()
        assert [t.name for t in tools.tools] == ["ping"]
        result = await client.call_tool("ping", {})
        assert result.content[0].text == "pong"
    assert "--read-only" in seen["argv"] and "--cap-drop=ALL" in seen["argv"]


async def _fake_server_peer(host, port, token):
    """Plays the container side: handshake, then a tiny MCP server over the socket."""
    from mcp.server import Server
    from mcp.server.stdio import stdio_server  # noqa: F401  (documenting the intended peer)
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    reader, writer = await asyncio.open_connection(host, port)
    writer.write((json.dumps({"token": token}) + "\n").encode())
    await writer.drain()

    async def list_tools(ctx, params):
        return ListToolsResult(tools=[Tool(name="ping", description="p", input_schema={"type": "object"})])

    async def call_tool(ctx, params):
        return CallToolResult(content=[TextContent(type="text", text="pong")])

    server = Server("fake", on_list_tools=list_tools, on_call_tool=call_tool)
    import anyio
    from mcp import types
    from mcp.shared.message import SessionMessage

    r_send, r_recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    w_send, w_recv = anyio.create_memory_object_stream[SessionMessage](0)

    async def pump_in():
        async with r_send:
            while line := await reader.readline():
                await r_send.send(SessionMessage(types.jsonrpc_message_adapter.validate_json(line.decode(), by_name=False)))

    async def pump_out():
        async with w_recv:
            async for item in w_recv:
                writer.write((item.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n").encode())
                await writer.drain()

    async with anyio.create_task_group() as tg:
        tg.start_soon(pump_in)
        tg.start_soon(pump_out)
        await server.run(r_recv, w_send, server.create_initialization_options())
        tg.cancel_scope.cancel()


async def test_container_that_never_dials_back_fails_cleanly(monkeypatch):
    spec = parse_entry("mute", _entry())

    async def launcher(argv, env):
        return "cid"

    monkeypatch.setattr(container_module, "_listener_addresses", lambda: ("127.0.0.1", "127.0.0.1"))
    monkeypatch.setattr(container_module, "DIAL_TIMEOUT_SEC", 0.5)
    with pytest.raises(container_module.ContainerStartError):
        async with container_module.container_transport(spec, launcher=launcher):
            pass


async def test_missing_docker_is_a_clean_failure(monkeypatch):
    spec = parse_entry("nodocker", _entry())
    monkeypatch.setattr(container_module, "_listener_addresses", lambda: ("127.0.0.1", "127.0.0.1"))
    monkeypatch.setattr(container_module.shutil, "which", lambda _name: None)
    with pytest.raises(container_module.ContainerStartError, match="docker"):
        async with container_module.container_transport(spec):
            pass


# ------------------------------------------------------------ real Docker
def _docker_has(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _docker_has("gagent-mcp-fetch:2026.10.10"), reason="docker or gagent-mcp-fetch image not available")
async def test_real_fetch_container_serves_tools_and_is_removed(monkeypatch):
    from tool_box.mcp_mount.connection import MountConnection

    monkeypatch.setenv("MCP_CONTAINER_HOST_GATEWAY", "1")
    monkeypatch.setenv("CODE_MODE_RPC_BIND", "0.0.0.0")
    monkeypatch.setenv("CODE_MODE_RPC_ADVERTISE", "host.docker.internal")
    monkeypatch.setenv("SANDBOX_NETWORK", "bridge")
    spec = parse_entry("fetch", {"transport": "stdio", "command": "mcp-server-fetch", "container": {"image": "gagent-mcp-fetch:2026.10.10", "public_web": True}})
    conn = MountConnection(spec)
    try:
        assert await conn.start(), conn.error
        assert [t["name"] for t in conn.tools] == ["fetch"]
    finally:
        await conn.stop()
    left = subprocess.run(["docker", "ps", "-aq", "--filter", "label=gagent.mcp=fetch"], capture_output=True, text=True).stdout.split()
    assert left == []
