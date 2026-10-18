"""Extensions service: the vetted catalog, the persisted on/off state, and the glue to the mount manager.

Two rules shape this module:

* Only what ships in ``catalog.json`` can be switched on from the API. The API
  never accepts a command, image or URL, so enabling is "turn on a thing we
  already reviewed", not "run whatever the caller names".
* The on/off state is a small JSON file next to the databases, written
  atomically. Without it a restart would silently forget what the admin chose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from tool_box.mcp_mount import MountSpec, get_mount_manager, parse_entry

from .state import read_state, write_state

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).with_name("catalog.json")
ENABLE_ENV = "EXTENSIONS_MCP_ENABLED"


class ExtensionError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class CatalogServer:
    id: str
    raw: Dict[str, Any]

    def spec(self) -> MountSpec:
        return parse_entry(self.id, {**self.raw["mount"], "read_only_tools": [t["name"] for t in self.raw["tools"] if t.get("read_only")]})


def load_catalog(path: Path = CATALOG_PATH) -> Dict[str, CatalogServer]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {sid: CatalogServer(sid, raw) for sid, raw in (data.get("servers") or {}).items()}


# ----------------------------------------------------------------------------- MCP


def mcp_feature_enabled() -> bool:
    """Catalog servers can be switched on only when the operator opted in."""
    return str(os.getenv(ENABLE_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _unavailable_reason(server: CatalogServer) -> Optional[str]:
    container = (server.raw.get("mount") or {}).get("container")
    if container is None:
        return None
    if shutil.which("docker") is None:
        return "本机没有 docker 命令,无法启动容器"
    return None


async def _image_present(image: str) -> bool:
    binary = shutil.which("docker")
    if binary is None:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "image", "inspect", image, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        return await asyncio.wait_for(proc.wait(), 15) == 0
    except (OSError, asyncio.TimeoutError):
        return False


def _isolation(server: CatalogServer) -> str:
    mount = server.raw["mount"]
    if mount.get("transport") == "http":
        return "remote"
    return "container" if mount.get("container") else "host"


def _egress(server: CatalogServer) -> List[str]:
    container = (server.raw.get("mount") or {}).get("container") or {}
    if container.get("public_web"):
        return ["任意公网主机 (仅 443)"]
    return list(container.get("egress") or [])


async def describe_server(server: CatalogServer, *, state: Dict[str, Any], configured: bool) -> Dict[str, Any]:
    manager = get_mount_manager()
    live = next((row for row in manager.status() if row.get("server") == server.id), None)
    wanted = bool((state.get("mcp") or {}).get(server.id, {}).get("enabled"))
    connected = bool(live and live.get("connected"))
    reason = _unavailable_reason(server)
    container = (server.raw.get("mount") or {}).get("container")
    if reason is None and container and not await _image_present(container["image"]):
        reason = f"镜像 {container['image']} 尚未构建"
    error = None
    if live and not connected:
        error = manager.connection_error(server.id) or live.get("error")
    if wanted and not connected:
        status = "error"
        error = error or "已启用但尚未连接"
    elif connected:
        status = "enabled"
    elif reason:
        status = "unavailable"
    else:
        status = "disabled"
    raw = server.raw
    return {
        "id": server.id,
        "title": raw["title"],
        "description": raw["description"],
        "category": raw["category"],
        "homepage": raw["homepage"],
        "license": raw["license"],
        "version": raw["version"],
        "source": raw["source"],
        "verification": raw["verification"],
        "isolation": _isolation(server),
        "egress": _egress(server),
        "tools": raw["tools"],
        "state": status,
        "connected": connected,
        "error": error,
        "live_tools": list(live.get("tools") or []) if live else [],
        "unavailable_reason": reason if status == "unavailable" or (reason and not connected) else None,
    }


async def list_mcp(*, can_manage: bool) -> Dict[str, Any]:
    catalog = load_catalog()
    state = read_state()
    configured = mcp_feature_enabled()
    servers = [await describe_server(s, state=state, configured=configured) for s in catalog.values()]
    custom = [
        {
            "name": row["server"],
            "transport": row.get("transport", "stdio"),
            "isolation": row.get("isolation", "host"),
            "connected": bool(row.get("connected")),
            "error": row.get("error"),
            "tools": list(row.get("tools") or []),
        }
        for row in get_mount_manager().status()
        if row.get("server") not in catalog and not row.get("managed")
    ]
    return {"configured": configured, "can_manage": can_manage, "servers": servers, "custom": custom}


async def set_mcp_enabled(server_id: str, enabled: bool) -> Dict[str, Any]:
    catalog = load_catalog()
    server = catalog.get(server_id)
    if server is None:
        raise ExtensionError(404, f"未知的 MCP 服务器: {server_id}")
    if not mcp_feature_enabled():
        raise ExtensionError(409, f"服务器未开启扩展功能:请先设置 {ENABLE_ENV}=1")
    state = read_state()
    manager = get_mount_manager()
    if enabled:
        reason = _unavailable_reason(server)
        container = (server.raw.get("mount") or {}).get("container")
        if reason is None and container and not await _image_present(container["image"]):
            reason = f"镜像 {container['image']} 尚未构建"
        if reason:
            raise ExtensionError(409, reason)
        await manager.set_managed(server_id, server.spec())
        if manager.connection_error(server_id) and not any(r.get("server") == server_id and r.get("connected") for r in manager.status()):
            err = manager.connection_error(server_id)
            await manager.set_managed(server_id, None)
            raise ExtensionError(409, f"启动失败:{err}")
    else:
        await manager.set_managed(server_id, None)
    state.setdefault("mcp", {})[server_id] = {"enabled": enabled}
    write_state(state)
    return await describe_server(server, state=state, configured=True)


async def restore_enabled_servers() -> None:
    """Called at startup: re-mount whatever the admin left switched on."""
    if not mcp_feature_enabled():
        return
    catalog = load_catalog()
    managed: Dict[str, MountSpec] = {}
    for sid, entry in (read_state().get("mcp") or {}).items():
        if entry.get("enabled") and sid in catalog:
            managed[sid] = catalog[sid].spec()
    if managed:
        manager = get_mount_manager()
        await manager.load(os.environ.get("MCP_MOUNTS_FILE") or None, managed)
