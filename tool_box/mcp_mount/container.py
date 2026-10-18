"""Run a stdio MCP server inside a hardened sibling container.

The Docker API proxy deliberately offers no ``attach``/``exec`` (see
``app/ops/docker_proxy.py``), so the host cannot hold pipes into a container.
The transport is therefore inverted, the same way sandbox kernels do it
(``execute_code/cell_channel.py``): the container's entrypoint (``bridge.py``,
baked into the image) dials OUT to a one-shot listener here, ``dup2()``s the
socket onto fds 0 and 1, and ``exec``s the real server. From that point the
server speaks ordinary newline-delimited JSON-RPC on stdin/stdout, and this
module feeds the socket to the SDK exactly as ``stdio_client`` feeds a pipe.

What the container gets (``app/ops/sandbox.py`` profile): all capabilities
dropped, no-new-privileges, read-only root, small tmpfs, pid/memory/cpu limits,
no bind mounts, an environment that is exactly what the mount config lists, and
the internal ``gagent-sandbox`` network, which has no route off the host. Its one
way out is the egress proxy (``egress.py``), limited to the declared hosts.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import shutil
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional, Sequence, Tuple

import anyio
from mcp import types
from mcp.shared.message import SessionMessage

from app.ops import sandbox as sandbox_ops

from .config import MountSpec
from .egress import EgressGrant, get_egress_proxy

logger = logging.getLogger(__name__)

ENV_ENDPOINT = "GAGENT_MCP_ENDPOINT"
ENV_TOKEN = "GAGENT_MCP_TOKEN"
LABEL_MOUNT = "gagent.mcp"
KIND = "mcp"

DIAL_TIMEOUT_SEC = 45.0
START_TIMEOUT_SEC = 90.0
REMOVE_TIMEOUT_SEC = 20.0
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_HANDSHAKE_BYTES = 4096
TMPFS_SIZE = "64m"
WORKDIR = "/tmp"
RUN_AS = "10001:10001"  # also baked into the images; set again so a wrong image cannot run as root

# Hook for tests: replaces the ``docker run`` step. It receives the argv and the
# environment the container would get and must arrange for a peer to dial back.
Launcher = Callable[[Sequence[str], dict], Awaitable[str]]


class ContainerStartError(RuntimeError):
    pass


def host_gateway_enabled() -> bool:
    """Dev boxes (Docker Desktop, colima) reach the host as ``host.docker.internal``."""
    return str(os.getenv("MCP_CONTAINER_HOST_GATEWAY") or "").strip().lower() in {"1", "true", "yes", "on"}


def _listener_addresses() -> Tuple[str, str]:
    from tool_box.tools_impl.execute_code.rpc import _advertise_host, _bind_host  # one answer to "how does a sandbox reach the app"

    bind = _bind_host()
    return bind, _advertise_host(bind)


def _extra_args() -> Tuple[str, ...]:
    """Run flags beyond the shared sandbox profile.

    ``--dns 127.0.0.1``: Docker's embedded resolver answers *external* names even
    on an ``internal`` network (it forwards to the host's resolver), which would
    let a server leak data in DNS queries while having no route out. Pointing the
    resolver at an address with nothing behind it makes outside names fail, while
    Docker's own service names (``app``) still resolve. The egress proxy resolves
    names itself, so allowed hosts keep working.
    """
    args = ["--dns", "127.0.0.1"]
    if host_gateway_enabled():
        args.append("--add-host=host.docker.internal:host-gateway")
    return tuple(args)


def build_run_spec(spec: MountSpec, *, name: str, endpoint: str, token: str, proxy_url: Optional[str]) -> sandbox_ops.SandboxRunSpec:
    assert spec.container is not None
    extra = dict(spec.env)
    extra[ENV_ENDPOINT] = endpoint
    extra[ENV_TOKEN] = token
    if proxy_url:
        extra.update({"HTTPS_PROXY": proxy_url, "https_proxy": proxy_url})
    return sandbox_ops.SandboxRunSpec(
        image=spec.container.image,
        kind=KIND,
        command=[spec.command or "", *spec.args],
        workdir=WORKDIR,
        name=name,
        user=RUN_AS,
        labels={LABEL_MOUNT: spec.name},
        env=sandbox_ops.build_sandbox_env(home=WORKDIR, extra=extra),
        memory=spec.container.memory,
        cpus=spec.container.cpus,
        pids_limit=spec.container.pids_limit,
        tmpfs_size=TMPFS_SIZE,
        extra_args=_extra_args(),
    )


class _DialBack:
    """One-shot listener: accepts the first connection that proves it knows the token."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._server: Optional[asyncio.AbstractServer] = None
        self._conn: "asyncio.Future[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]" = asyncio.get_running_loop().create_future()
        self.port = 0

    async def start(self, bind: str) -> int:
        self._server = await asyncio.start_server(self._accept, bind, 0, limit=MAX_LINE_BYTES)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self.port

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        keep = False
        try:
            line = await asyncio.wait_for(reader.readline(), 10.0)
            if not line or len(line) > MAX_HANDSHAKE_BYTES or not line.endswith(b"\n"):
                return
            try:
                presented = str(json.loads(line.decode("utf-8")).get("token") or "")
            except (ValueError, AttributeError):
                return
            if not hmac.compare_digest(presented.encode(), self._token.encode()) or self._conn.done():
                logger.warning("MCP container dial-back rejected (bad token or duplicate)")
                return
            self._conn.set_result((reader, writer))
            keep = True
            if self._server is not None:  # one container, one connection: stop listening at once
                self._server.close()
        except (asyncio.TimeoutError, ConnectionError, asyncio.LimitOverrunError):
            return
        finally:
            if not keep:
                writer.close()

    async def connection(self, timeout: float) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(asyncio.shield(self._conn), timeout)
        except asyncio.TimeoutError as exc:
            raise ContainerStartError(f"the container did not connect back within {timeout:.0f}s") from exc

    async def close(self) -> None:
        """Stop listening and drop the accepted connection.

        ``wait_closed()`` is deliberately not awaited: since Python 3.12 it also
        waits for every accepted connection, and the dial-back connection is the
        live MCP channel, so waiting would block until the peer hangs up.
        """
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if self._conn.done() and not self._conn.cancelled() and self._conn.exception() is None:
            self._conn.result()[1].close()
        elif not self._conn.done():
            self._conn.cancel()


async def _docker(args: Sequence[str], timeout: float) -> Tuple[int, str]:
    binary = shutil.which("docker")
    if binary is None:
        raise ContainerStartError("the docker CLI is not available on this host")
    proc = await asyncio.create_subprocess_exec(
        binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise ContainerStartError(f"docker {args[0]} timed out after {timeout:.0f}s")
    return proc.returncode or 0, (out or err or b"").decode("utf-8", "replace").strip()


async def _docker_run(argv: Sequence[str], _env: dict) -> str:
    code, text = await _docker(list(argv[1:]), START_TIMEOUT_SEC)  # argv[0] is the binary name
    if code != 0:
        raise ContainerStartError(f"docker run failed: {text[:400]}")
    return text[:64]


async def _remove(name: str) -> None:
    with contextlib.suppress(Exception):
        await _docker(["rm", "-f", name], REMOVE_TIMEOUT_SEC)


@contextlib.asynccontextmanager
async def container_transport(spec: MountSpec, *, launcher: Optional[Launcher] = None) -> AsyncIterator[Any]:
    """Transport for ``mcp.Client``: starts the container, yields (read, write) streams."""
    if spec.container is None:
        raise ContainerStartError(f"mount '{spec.name}' has no container settings")

    token = secrets.token_urlsafe(32)
    name = sandbox_ops.sandbox_name(f"gagent-mcp-{spec.name}", uuid.uuid4().hex[:10])
    bind, advertise = _listener_addresses()
    dial = _DialBack(token)
    proxy = get_egress_proxy()
    egress_token: Optional[str] = None
    started = False
    pumps: List[asyncio.Task] = []
    writer: Optional[asyncio.StreamWriter] = None
    streams: List[Any] = []
    try:
        port = await dial.start(bind)
        proxy_url = None
        if spec.container.egress or spec.container.public_web:
            if not proxy.running:
                await proxy.start(bind=bind, advertise=advertise)
            egress_token = proxy.register(
                EgressGrant(label=spec.name, hosts=spec.container.egress, public_web=spec.container.public_web)
            )
            proxy_url = proxy.proxy_url(egress_token)
        run_spec = build_run_spec(spec, name=name, endpoint=f"tcp://{advertise}:{port}", token=token, proxy_url=proxy_url)
        argv = sandbox_ops.build_sandbox_run_args(run_spec)
        logger.info("MCP mount %s: starting %s image=%s network=%s egress=%s", spec.name, name, run_spec.image, run_spec.resolved_network(), "public-web" if spec.container.public_web else list(spec.container.egress))
        started = True
        await (launcher or _docker_run)(argv, dict(run_spec.env))
        reader, writer = await dial.connection(DIAL_TIMEOUT_SEC)

        read_send, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        write_stream, write_recv = anyio.create_memory_object_stream[SessionMessage](0)
        streams = [read_send, read_stream, write_stream, write_recv]

        async def pump_in() -> None:
            try:
                async with read_send:
                    while True:
                        try:
                            line = await reader.readline()
                        except (asyncio.LimitOverrunError, ValueError):
                            await read_send.send(ValueError("MCP server sent a line over the size limit"))
                            return
                        if not line:
                            return
                        text = line.decode("utf-8", "replace").strip()
                        if not text:
                            continue
                        try:
                            message: SessionMessage | Exception = SessionMessage(types.jsonrpc_message_adapter.validate_json(text, by_name=False))
                        except ValueError as exc:
                            message = exc
                        await read_send.send(message)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, ConnectionError, OSError):
                return

        async def pump_out() -> None:
            try:
                async with write_recv:
                    async for item in write_recv:
                        data = item.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n"
                        writer.write(data.encode("utf-8"))
                        await writer.drain()
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, ConnectionError, OSError):
                with contextlib.suppress(Exception):
                    await read_send.aclose()

        pumps = [asyncio.create_task(pump_in(), name=f"mcp-container-in:{spec.name}"), asyncio.create_task(pump_out(), name=f"mcp-container-out:{spec.name}")]
        yield read_stream, write_stream
    finally:
        for pump in pumps:
            pump.cancel()
        for pump in pumps:
            with contextlib.suppress(BaseException):
                await pump
        for stream in streams:
            with contextlib.suppress(Exception):
                await stream.aclose()
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
        await dial.close()
        if egress_token is not None:
            proxy.revoke(egress_token)
        if started and launcher is None:
            await _remove(name)
