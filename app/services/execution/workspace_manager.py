"""Execution workspace management utilities.

Provides helpers to create, clean, and inspect per-session
execution workspaces that are colocated with the backend.

The implementation intentionally reuses the existing file operations
tooling in ``tool_box.tools_impl.file_operations`` so the agent has a
consistent abstraction for file access whether requests originate from
API handlers or via the ToolBox runtime.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from tool_box.tools_impl import file_operations as file_ops
from tool_box.tools_impl.file_operations import file_operations_handler

_DEFAULT_ROOT = Path(os.getenv("EXECUTION_WORKSPACES_ROOT", "runtime/workspaces"))
_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _allow_workspace_root(path: Path) -> None:
    """Ensure the file-operations security whitelist permits *path*."""
    allowed = getattr(file_ops, "ALLOWED_BASE_PATHS", None)
    if not isinstance(allowed, list):  # pragma: no cover - defensive
        return

    try:
        resolved = str(path.resolve())
    except FileNotFoundError:  # pragma: no cover - defensive
        resolved = str(path)

    if not any(resolved.startswith(existing) for existing in allowed):
        allowed.append(resolved)


def _normalise_owner(owner: str) -> str:
    """Normalise owner/session identifiers to safe directory names."""
    if not owner:
        raise ValueError("Workspace owner identifier is required")
    slug = _SLUG_PATTERN.sub("-", owner.strip())
    slug = slug.strip("-._") or "workspace"
    return slug[:128]


def _ensure_root() -> Path:
    root = _DEFAULT_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    _allow_workspace_root(root)
    return root


def get_workspace_path(owner: str) -> Path:
    """Return absolute path to a workspace without creating it."""
    root = _ensure_root()
    return root / _normalise_owner(owner)


async def prepare_workspace(owner: str, *, reset: bool = False) -> Path:
    """Ensure a workspace exists for *owner* and optionally reset its contents."""
    workspace = get_workspace_path(owner)
    if reset and workspace.exists():
        # Reuse file_operations delete for consistent safeguards
        await file_operations_handler("delete", str(workspace))
    workspace.mkdir(parents=True, exist_ok=True)
    _allow_workspace_root(workspace)
    return workspace


async def cleanup_workspace(owner: str) -> Dict[str, Any]:
    """Delete a workspace directory and return the tool response."""
    workspace = get_workspace_path(owner)
    if not workspace.exists():
        return {
            "operation": "delete",
            "path": str(workspace),
            "success": True,
            "message": "Workspace already removed",
        }
    return await file_operations_handler("delete", str(workspace))


async def list_workspace(owner: str, pattern: Optional[str] = None) -> Dict[str, Any]:
    """List files under a workspace using the shared file operations tool."""
    workspace = get_workspace_path(owner)
    if not workspace.exists():
        return {
            "operation": "list",
            "path": str(workspace),
            "success": False,
            "error": "Workspace not found",
        }
    return await file_operations_handler("list", str(workspace), pattern=pattern)


async def file_exists(owner: str, relative_path: str) -> bool:
    """Check whether *relative_path* exists inside the workspace."""
    workspace = get_workspace_path(owner)
    target = workspace / relative_path
    response = await file_operations_handler("exists", str(target))
    return bool(response.get("exists"))


async def read_file(owner: str, relative_path: str) -> Dict[str, Any]:
    workspace = get_workspace_path(owner)
    target = workspace / relative_path
    return await file_operations_handler("read", str(target))


async def write_file(owner: str, relative_path: str, content: str) -> Dict[str, Any]:
    workspace = get_workspace_path(owner)
    target = workspace / relative_path
    return await file_operations_handler("write", str(target), content=content)
