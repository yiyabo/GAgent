"""Mount external MCP servers into Tool Box, and unmount them again.

Mounted tools are registered as ``mcp__<server>__<tool>``. They are treated as
untrusted: assumed to have side effects unless the config lists them as
read-only, size-capped, time-limited, and their output is wrapped as data.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from ..tools import get_tool_registry, register_tool, unregister_tool
from .config import MountConfigError, MountSpec, load_mounts, tool_name
from .connection import MountConnection
from .envelope import build_failure, build_result

logger = logging.getLogger(__name__)

CATEGORY = "mcp_mount"
MAX_TOOL_NAME_LEN = 64  # the function-calling limit common to model providers
_BAD_CHARS = re.compile(r"[^A-Za-z0-9_-]")
# Argument names the host attaches to every call; never forwarded to a mounted tool.
_HOST_ARGUMENTS = frozenset({"tool_context"})

ConnectionFactory = Callable[[MountSpec], MountConnection]


def _declared_arguments(schema: Dict[str, Any]) -> Optional[set]:
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        return set(props)
    return None if schema.get("additionalProperties") is True else set()


class MountManager:
    def __init__(self, connection_factory: Optional[ConnectionFactory] = None) -> None:
        self._factory: ConnectionFactory = connection_factory or MountConnection
        self._connections: Dict[str, MountConnection] = {}
        self._specs: Dict[str, MountSpec] = {}
        self._tools: Dict[str, List[str]] = {}  # server -> registered tool names
        self._schemas: Dict[str, Dict[str, Any]] = {}  # registered name -> function schema
        self._rejected: Dict[str, str] = {}
        self._skipped: Dict[str, str] = {}
        # Servers enabled through the extensions catalog. They are mounted in
        # addition to the hand-written file and win a name clash with it.
        self._managed: Dict[str, MountSpec] = {}
        self._path: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ queries
    def tool_names(self) -> List[str]:
        return [name for names in self._tools.values() for name in names]

    def schema_for(self, name: str) -> Optional[Dict[str, Any]]:
        return self._schemas.get(name)

    def status(self) -> List[Dict[str, Any]]:
        rows = []
        for name, spec in self._specs.items():
            conn = self._connections.get(name)
            rows.append(
                {
                    "server": name,
                    "transport": spec.transport,
                    "isolation": spec.isolation,
                    "managed": name in self._managed,
                    "connected": bool(conn and conn.connected),
                    "tools": list(self._tools.get(name, [])),
                    "error": conn.error if conn else None,
                }
            )
        for name, reason in self._rejected.items():
            rows.append({"server": name, "connected": False, "tools": [], "error": f"rejected: {reason}"})
        return rows

    # ----------------------------------------------------------------- lifecycle
    async def load(
        self, path: Optional[str], managed: Optional[Mapping[str, MountSpec]] = None
    ) -> List[Dict[str, Any]]:
        """Apply the mount file (plus managed servers): add new, drop removed, restart changed."""
        async with self._lock:
            return await self._apply(path, managed)

    async def set_managed(self, name: str, spec: Optional[MountSpec]) -> List[Dict[str, Any]]:
        """Enable (``spec``) or disable (``None``) one catalog server without touching the others."""
        async with self._lock:
            managed = dict(self._managed)
            if spec is None:
                managed.pop(name, None)
            else:
                managed[name] = spec
            return await self._apply(self._path, managed)

    def connection_error(self, name: str) -> Optional[str]:
        conn = self._connections.get(name)
        return conn.error if conn is not None else None

    async def _apply(self, path: Optional[str], managed: Optional[Mapping[str, MountSpec]]) -> List[Dict[str, Any]]:
        self._loop = asyncio.get_running_loop()
        if managed is not None:
            self._managed = dict(managed)
        self._path = path
        try:
            specs, self._rejected = load_mounts(path)
            wanted = {s.name: s for s in specs if s.enabled}
        except MountConfigError as exc:
            # A broken file must not silently unmount a working setup, nor stop startup.
            logger.error("MCP mounts: %s; keeping the current file-defined mounts", exc)
            wanted = {n: sp for n, sp in self._specs.items() if n not in self._managed}
        for name, spec in self._managed.items():
            if name in wanted:
                self._rejected[name] = "name is reserved by the extensions catalog"
            wanted[name] = spec

        for name in list(self._specs):
            conn = self._connections.get(name)
            if name not in wanted or wanted[name] != self._specs[name] or not (conn and conn.connected):
                await self._drop(name)
        for name, spec in wanted.items():
            if name not in self._specs:
                await self._attach(spec)
        return self.status()

    async def shutdown(self) -> None:
        async with self._lock:
            for name in list(self._specs):
                await self._drop(name)

    # ------------------------------------------------------------------ internals
    async def _attach(self, spec: MountSpec) -> None:
        conn = self._factory(spec)
        self._specs[spec.name] = spec
        self._connections[spec.name] = conn
        self._tools[spec.name] = []
        if not await conn.start():
            logger.warning("MCP mount %s not available: %s", spec.name, conn.error)
            return

        registry = get_tool_registry()
        for tool in conn.tools:
            safe = _BAD_CHARS.sub("_", str(tool["name"]))
            full = tool_name(spec.name, safe)
            if len(full) > MAX_TOOL_NAME_LEN:
                self._skipped[f"{spec.name}/{tool['name']}"] = f"registry name longer than {MAX_TOOL_NAME_LEN}"
                continue
            if full in self._schemas or registry.get_tool(full) is not None:
                self._skipped[f"{spec.name}/{tool['name']}"] = "name already registered"
                continue
            read_only = tool["name"] in spec.read_only_tools
            schema = tool["input_schema"]
            register_tool(
                name=full,
                description=f"[external MCP: {spec.name}] {tool['description']}".strip(),
                category=CATEGORY,
                parameters_schema=schema,
                handler=self._make_handler(spec.name, tool["name"], schema),
                author=f"mcp:{spec.name}",
                tags=["mcp", spec.name],
                is_read_only=read_only,
                is_concurrent_safe=read_only,
                search_hint=f"external mcp {spec.name} {tool['name']}",
            )
            self._tools[spec.name].append(full)
            self._schemas[full] = {
                "type": "function",
                "function": {"name": full, "description": registry.get_tool(full).description, "parameters": schema},
            }
        logger.info("MCP mount %s: %d tool(s) registered", spec.name, len(self._tools[spec.name]))

    async def _drop(self, name: str) -> None:
        for full in self._tools.pop(name, []):
            unregister_tool(full)
            self._schemas.pop(full, None)
        conn = self._connections.pop(name, None)
        self._specs.pop(name, None)
        if conn is not None:
            await conn.stop()

    def _make_handler(self, server: str, tool: str, schema: Dict[str, Any]) -> Callable[..., Awaitable[Dict[str, Any]]]:
        declared = _declared_arguments(schema)

        async def handler(**kwargs: Any) -> Dict[str, Any]:
            arguments = {
                k: v
                for k, v in kwargs.items()
                if k not in _HOST_ARGUMENTS and (declared is None or k in declared)
            }
            conn = self._connections.get(server)
            spec = self._specs.get(server)
            if conn is None or spec is None or not conn.connected:
                return build_failure(server, tool, "mount_unavailable", f"MCP server '{server}' is not connected")
            try:
                result = await self._call(conn, tool, arguments)
            except asyncio.TimeoutError:
                return build_failure(server, tool, "timeout", f"no answer within {spec.timeout_sec:g}s")
            except Exception as exc:
                logger.warning("MCP mount %s/%s failed: %s", server, tool, exc)
                return build_failure(server, tool, "call_failed", f"{type(exc).__name__}: {exc}")
            return build_result(server, tool, result, limit=spec.max_result_chars)

        return handler

    async def _call(self, conn: MountConnection, tool: str, arguments: Dict[str, Any]) -> Any:
        """Run on the loop that owns the connection, even when called from another."""
        owner = self._loop
        if owner is None or asyncio.get_running_loop() is owner:
            return await conn.call(tool, arguments)
        future = asyncio.run_coroutine_threadsafe(conn.call(tool, arguments), owner)
        return await asyncio.wrap_future(future)


_manager: Optional[MountManager] = None


def get_mount_manager() -> MountManager:
    global _manager
    if _manager is None:
        _manager = MountManager()
    return _manager
