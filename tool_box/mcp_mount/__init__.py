"""Mount external MCP servers as Tool Box tools (the MCP *client* side)."""

from .config import MountSpec, TOOL_PREFIX, load_mounts, parse_entry, tool_name
from .manager import MountManager, get_mount_manager

__all__ = [
    "MountManager",
    "MountSpec",
    "TOOL_PREFIX",
    "get_mount_manager",
    "load_mounts",
    "parse_entry",
    "tool_name",
]
