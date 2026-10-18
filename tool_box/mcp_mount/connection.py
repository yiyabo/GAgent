"""One long-lived connection to one external MCP server.

A single task owns the connection for its whole life. The SDK's transports use
anyio task groups, which must be entered and exited from the same task, so the
connection cannot be opened in one request and closed in another. Callers talk
to the owner task through ``call`` and ``stop``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from mcp import Client, StdioServerParameters

from .config import MountSpec

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SEC = 30.0


def _transport_for(spec: MountSpec) -> Any:
    if spec.transport == "stdio":
        # The SDK passes only a short allowlist of variables (PATH, HOME, ...) plus
        # what is listed here. Our own secrets are therefore never inherited.
        return StdioServerParameters(
            command=spec.command or "",
            args=list(spec.args),
            env=dict(spec.env),
            cwd=spec.cwd,
        )
    return spec.url


def _http_client(spec: MountSpec) -> Optional[Any]:
    """An httpx2 client carrying the bearer token, when one is configured."""
    token_var = spec.bearer_token_env
    if not token_var:
        return None
    token = os.environ.get(token_var)
    if not token:
        raise RuntimeError(f"environment variable {token_var} is not set")
    import httpx2

    return httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=spec.timeout_sec),
    )


class MountConnection:
    def __init__(self, spec: MountSpec, transport: Any = None) -> None:
        self.spec = spec
        self._transport = transport  # tests inject an in-process server here
        self.tools: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self._client: Optional[Client] = None
        self._ready: "asyncio.Future[bool]" = asyncio.get_running_loop().create_future()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._client is not None and not self._stop.is_set() and self._task is not None and not self._task.done()

    async def start(self) -> bool:
        self._task = asyncio.create_task(self._run(), name=f"mcp-mount:{self.spec.name}")
        try:
            return await asyncio.wait_for(asyncio.shield(self._ready), timeout=CONNECT_TIMEOUT_SEC + 5)
        except asyncio.TimeoutError:
            self.error = f"timed out connecting after {CONNECT_TIMEOUT_SEC:.0f}s"
            await self.stop()
            return False

    async def _run(self) -> None:
        http_client = None
        try:
            transport = self._transport if self._transport is not None else _transport_for(self.spec)
            http_client = _http_client(self.spec) if self.spec.transport == "http" else None
            if http_client is not None:
                from mcp.client.streamable_http import streamable_http_client

                transport = streamable_http_client(self.spec.url, http_client=http_client)
            # No sampling / elicitation / roots callbacks: the SDK then refuses those
            # server-to-client requests, so an external tool cannot spend our model quota.
            async with Client(transport, read_timeout_seconds=self.spec.timeout_sec) as client:
                listed = await asyncio.wait_for(client.list_tools(), timeout=CONNECT_TIMEOUT_SEC)
                self.tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": dict(t.input_schema or {"type": "object"}),
                    }
                    for t in listed.tools
                ]
                self._client = client
                if not self._ready.done():
                    self._ready.set_result(True)
                await self._stop.wait()
        except BaseException as exc:  # includes cancellation: we must always settle _ready
            self.error = f"{type(exc).__name__}: {exc}"
            logger.warning("MCP mount %s unavailable: %s", self.spec.name, self.error)
            if not self._ready.done():
                self._ready.set_result(False)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self._client = None
            if http_client is not None:
                await http_client.aclose()

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Any:
        client = self._client
        if client is None or not self.connected:
            raise ConnectionError(f"mount '{self.spec.name}' is not connected")
        return await asyncio.wait_for(client.call_tool(tool, arguments), timeout=self.spec.timeout_sec)

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            task.cancel()
            try:
                await task
            except BaseException:
                pass
