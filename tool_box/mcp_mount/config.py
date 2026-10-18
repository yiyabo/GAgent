"""Mount configuration: which external MCP servers to attach, and under what trust.

The file is read from ``MCP_MOUNTS_FILE``. Without it nothing is mounted, so a
deployment that never opts in behaves exactly as before. Secrets never live in
the file: a bearer token is referenced by the *name* of an environment variable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
TOOL_PREFIX = "mcp__"

DEFAULT_TIMEOUT_SEC = 60.0
MAX_TIMEOUT_SEC = 600.0
DEFAULT_MAX_RESULT_CHARS = 20_000
MAX_RESULT_CHARS_CAP = 200_000


class MountConfigError(ValueError):
    """The mount file is unusable as a whole (unreadable or not a JSON object)."""


@dataclass(frozen=True)
class MountSpec:
    name: str
    transport: str  # "stdio" | "http"
    command: Optional[str] = None
    args: Tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    bearer_token_env: Optional[str] = None
    # Only tools named here are treated as read-only; everything else is assumed to
    # have side effects. "*" is deliberately not supported.
    read_only_tools: Tuple[str, ...] = ()
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    enabled: bool = True


def tool_name(server: str, tool: str) -> str:
    """Registry name of a mounted tool; the prefix keeps it clear of built-ins."""
    return f"{TOOL_PREFIX}{server}__{tool}"


def _str_list(value: Any, field_name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def _clamp(value: Any, default: float, low: float, high: float, field_name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    if not low <= float(value) <= high:
        raise ValueError(f"{field_name} must be between {low:g} and {high:g}")
    return float(value)


def parse_entry(name: str, raw: Any) -> MountSpec:
    if not NAME_RE.match(name or ""):
        raise ValueError("name must be 1-32 chars of letters, digits, '_' or '-'")
    if "__" in name:
        raise ValueError("name must not contain '__' (it separates server and tool)")
    if not isinstance(raw, dict):
        raise ValueError("entry must be an object")

    transport = str(raw.get("transport") or "").strip().lower()
    if transport not in {"stdio", "http"}:
        raise ValueError("transport must be 'stdio' or 'http'")

    env = raw.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be an object of strings")

    command = raw.get("command")
    url = raw.get("url")
    token_env = raw.get("bearer_token_env")
    if transport == "stdio":
        if not isinstance(command, str) or not command.strip():
            raise ValueError("stdio mounts need a non-empty 'command'")
        if url or token_env:
            raise ValueError("'url' and 'bearer_token_env' apply to http mounts only")
    else:
        if not isinstance(url, str) or not re.match(r"^https?://", url):
            raise ValueError("http mounts need an http(s) 'url'")
        if command or raw.get("args") or env:
            raise ValueError("'command', 'args' and 'env' apply to stdio mounts only")
        if token_env is not None and not (isinstance(token_env, str) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token_env)):
            raise ValueError("bearer_token_env must be the NAME of an environment variable, not a token")

    return MountSpec(
        name=name,
        transport=transport,
        command=command.strip() if isinstance(command, str) else None,
        args=_str_list(raw.get("args"), "args"),
        env=dict(env),
        cwd=raw.get("cwd") if isinstance(raw.get("cwd"), str) else None,
        url=url if isinstance(url, str) else None,
        bearer_token_env=token_env,
        read_only_tools=_str_list(raw.get("read_only_tools"), "read_only_tools"),
        timeout_sec=_clamp(raw.get("timeout_sec"), DEFAULT_TIMEOUT_SEC, 1.0, MAX_TIMEOUT_SEC, "timeout_sec"),
        max_result_chars=int(
            _clamp(raw.get("max_result_chars"), DEFAULT_MAX_RESULT_CHARS, 200, MAX_RESULT_CHARS_CAP, "max_result_chars")
        ),
        enabled=bool(raw.get("enabled", True)),
    )


def load_mounts(path: Optional[str]) -> Tuple[List[MountSpec], Dict[str, str]]:
    """Return (valid specs, {entry name: reason} for entries that were rejected).

    A bad entry never blocks the good ones; only an unreadable file raises.
    """
    if not path:
        return [], {}
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MountConfigError(f"cannot read mount file {path}: {exc}") from exc
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise MountConfigError("mount file must be an object with a 'servers' object")

    specs: List[MountSpec] = []
    rejected: Dict[str, str] = {}
    for name, raw in servers.items():
        try:
            spec = parse_entry(str(name), raw)
        except (ValueError, TypeError) as exc:
            rejected[str(name)] = str(exc)
            continue
        specs.append(spec)
    return specs, rejected
