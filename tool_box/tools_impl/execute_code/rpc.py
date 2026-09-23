"""Host-side tool RPC server for execute_code kernels.

One asyncio server per kernel on a dedicated thread with its own event loop
(the kernel outlives individual request loops, so it must not be bound to the
caller's loop). Binds 127.0.0.1:0; newline-delimited JSON protocol:

    request  {"id", "tool", "args", "token"}
    response {"id", "result"} | {"id", "error"}

Enforcement lives HERE, not in the stubs: per-spawn token (compare_digest,
empty token fails closed), tool allowlist, per-cell call budget (refusals are
free), 300s per-call timeout. Dispatch goes through the canonical tool path
(``tool_box.execute_tool`` → async handler + prepare_handler_kwargs) with the
ToolContext of the currently bound cell; a settled cell retires its binding
and late requests are refused.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
from typing import Any, Dict, Optional

from . import config

logger = logging.getLogger(__name__)

_MAX_LINE_BYTES = 16 * 1024 * 1024


def _token_ok(request_token: Any, rpc_token: str) -> bool:
    """Constant-time check; an empty server token fails closed."""
    return bool(rpc_token) and secrets.compare_digest(
        str(request_token or "").encode(), rpc_token.encode()
    )


class KernelRPCServer:
    """Serves tool RPC for one kernel's whole life (start/stop with the kernel)."""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel  # duck-typed SessionKernel (avoids kernel<->rpc import cycle)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port = 0

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> str:
        """Spawn the serving thread; returns the ``tcp://127.0.0.1:<port>`` endpoint."""
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(ready,), daemon=True, name="gagent-code-mode-rpc"
        )
        self._thread.start()
        if not ready.wait(timeout=10):
            raise RuntimeError("code-mode RPC server failed to start")
        return f"tcp://127.0.0.1:{self._port}"

    def _run(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._server = loop.run_until_complete(
                asyncio.start_server(self._handle_connection, "127.0.0.1", 0)
            )
            self._port = int(self._server.sockets[0].getsockname()[1])
            ready.set()
            loop.run_forever()
        finally:
            try:
                if self._server is not None:
                    self._server.close()
                    loop.run_until_complete(self._server.wait_closed())
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass
            loop.close()

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop, self._thread = None, None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None:
            thread.join(timeout=5)

    # -- request pipeline ------------------------------------------------------

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ConnectionError, asyncio.LimitOverrunError):
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                if len(line) > _MAX_LINE_BYTES:
                    writer.write(self._encode(None, error="RPC request too large"))
                    await writer.drain()
                    continue
                try:
                    request = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    writer.write(self._encode(None, error=f"Invalid RPC request: {exc}"))
                    await writer.drain()
                    continue
                response = await self._handle_request(request)
                writer.write(response)
                await writer.drain()
        except Exception:  # noqa: BLE001 - never let a connection kill the server
            logger.debug("code-mode RPC connection failed", exc_info=True)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _handle_request(self, request: Dict[str, Any]) -> bytes:
        request_id = request.get("id")
        kernel = self._kernel
        if not _token_ok(request.get("token"), kernel.rpc_token):
            return self._encode(request_id, error="Unauthorized RPC request")

        binding = kernel.authority
        if binding is None or not binding.active:
            return self._encode(
                request_id,
                error=(
                    "No active execute_code cell: the cell this kernel call belonged "
                    "to has settled, so its tool authority is retired."
                ),
            )

        tool_name = str(request.get("tool") or "")
        tool_args = request.get("args") or {}
        if tool_name not in kernel.allowlist:
            return self._encode(
                request_id,
                error=(
                    f"Tool '{tool_name}' is not available in execute_code. "
                    f"Available: {', '.join(sorted(kernel.allowlist))}"
                ),
            )
        if kernel.tool_call_counter[0] >= kernel.max_tool_calls:
            return self._encode(
                request_id,
                error=(
                    f"Tool call limit reached ({kernel.max_tool_calls}). "
                    "No more tool calls allowed in this cell."
                ),
            )

        try:
            result = await asyncio.wait_for(
                self._dispatch(tool_name, tool_args, binding.tool_context),
                timeout=config.DEFAULT_TOOL_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            result = {
                "error": f"Tool '{tool_name}' timed out after "
                f"{config.DEFAULT_TOOL_CALL_TIMEOUT_SECONDS}s inside execute_code."
            }
        except Exception as exc:  # noqa: BLE001 - tool errors must not kill the cell
            logger.warning("code-mode tool call %s failed: %s", tool_name, exc)
            result = {"error": str(exc)}
        kernel.tool_call_counter[0] += 1
        return self._encode(request_id, result=result)

    async def _dispatch(
        self, tool_name: str, tool_args: Dict[str, Any], tool_context: Any
    ) -> Any:
        """Canonical tool path: async handler + prepare_handler_kwargs + ToolContext."""
        from tool_box import execute_tool  # lazy: avoids tool_box import cycle

        if not isinstance(tool_args, dict):
            raise TypeError("tool args must be a JSON object")
        return await execute_tool(tool_name, tool_context=tool_context, **tool_args)

    @staticmethod
    def _encode(request_id: Any, result: Any = None, error: Optional[str] = None) -> bytes:
        payload: Dict[str, Any] = {"id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        return (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
