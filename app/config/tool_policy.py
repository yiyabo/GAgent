"""Centralized tool policy helpers (allowlist/denylist)."""

from __future__ import annotations

import os
from typing import Iterable, List, Set, Tuple


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _split_env(value: str | None) -> Set[str]:
    if not value:
        return set()
    parts = [item.strip() for item in value.split(",")]
    return {_normalize_name(item) for item in parts if item}


def get_tool_policy() -> dict:
    """Return tool policy from environment variables."""
    allowlist = _split_env(os.getenv("TOOL_ALLOWLIST"))
    denylist = _split_env(os.getenv("TOOL_DENYLIST"))
    return {"allowlist": allowlist, "denylist": denylist}


def is_tool_allowed(tool_name: str, policy: dict | None = None) -> bool:
    if not tool_name:
        return False
    policy = policy or get_tool_policy()
    allowlist = policy.get("allowlist", set())
    denylist = policy.get("denylist", set())

    normalized = _normalize_name(tool_name)
    if allowlist:
        return normalized in allowlist
    if denylist:
        return normalized not in denylist
    return True


def filter_tool_names(
    tool_names: Iterable[str], policy: dict | None = None
) -> List[str]:
    policy = policy or get_tool_policy()
    return [name for name in tool_names if is_tool_allowed(name, policy)]


def filter_tool_objects(tools: Iterable[object], policy: dict | None = None) -> List[object]:
    policy = policy or get_tool_policy()
    filtered = []
    for tool in tools:
        name = getattr(tool, "name", "")
        if is_tool_allowed(str(name), policy):
            filtered.append(tool)
    return filtered


def filter_tool_calls(tool_calls: Iterable[dict], policy: dict | None = None) -> List[dict]:
    policy = policy or get_tool_policy()
    filtered = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = call.get("tool_name") or call.get("tool") or call.get("name") or ""
        if is_tool_allowed(str(name), policy):
            filtered.append(call)
    return filtered
