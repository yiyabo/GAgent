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

HOST_RE = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")  # DNS names only: no IP literals, any spelling
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,199}$")
MEMORY_RE = re.compile(r"^[1-9][0-9]{0,5}[kmg]$")

DEFAULT_CONTAINER_MEMORY = "512m"
DEFAULT_CONTAINER_CPUS = "1"
DEFAULT_CONTAINER_PIDS = 128

DEFAULT_TIMEOUT_SEC = 60.0
MAX_TIMEOUT_SEC = 600.0
DEFAULT_MAX_RESULT_CHARS = 20_000
MAX_RESULT_CHARS_CAP = 200_000


class MountConfigError(ValueError):
    """The mount file is unusable as a whole (unreadable or not a JSON object)."""


@dataclass(frozen=True)
class ContainerSpec:
    """Run a stdio server inside a hardened sibling container instead of on the host.

    ``egress`` lists the HTTPS hosts the server may reach (``*.example.org``
    allowed). Empty and not ``public_web`` means no outbound access. The container never gets
    host mounts, and its environment is exactly ``MountSpec.env``.
    """

    image: str
    egress: Tuple[str, ...] = ()
    # Any globally routable host on :443 (for web fetchers). Private, loopback and
    # link-local targets stay refused either way.
    public_web: bool = False
    memory: str = DEFAULT_CONTAINER_MEMORY
    cpus: str = DEFAULT_CONTAINER_CPUS
    pids_limit: int = DEFAULT_CONTAINER_PIDS


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
    container: Optional[ContainerSpec] = None

    @property
    def isolation(self) -> str:
        """Where the third-party code runs: ``container``, ``host`` or ``remote``."""
        if self.transport == "http":
            return "remote"
        return "container" if self.container is not None else "host"


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


def parse_container(raw: Any) -> ContainerSpec:
    if not isinstance(raw, dict):
        raise ValueError("container must be an object")
    unknown = set(raw) - {"image", "egress", "public_web", "memory", "cpus", "pids_limit"}
    if unknown:
        raise ValueError(f"container has unknown field(s): {', '.join(sorted(unknown))}")
    image = raw.get("image")
    if not isinstance(image, str) or not IMAGE_RE.match(image):
        raise ValueError("container.image must be an image reference")
    if image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@" not in image):
        raise ValueError("container.image must be pinned to a tag or digest, not 'latest'")
    egress = []
    for host in _str_list(raw.get("egress"), "container.egress"):
        host = host.strip().lower()
        if not HOST_RE.match(host):
            raise ValueError(f"container.egress entry {host!r} must be a DNS name such as 'rest.uniprot.org' or '*.example.org'")
        egress.append(host)
    public_web = raw.get("public_web", False)
    if not isinstance(public_web, bool):
        raise ValueError("container.public_web must be true or false")
    if public_web and egress:
        raise ValueError("container.public_web and container.egress are mutually exclusive")
    memory = raw.get("memory", DEFAULT_CONTAINER_MEMORY)
    if not isinstance(memory, str) or not MEMORY_RE.match(memory.lower()):
        raise ValueError("container.memory must look like '512m' or '2g'")
    cpus = _clamp(raw.get("cpus"), float(DEFAULT_CONTAINER_CPUS), 0.1, 4.0, "container.cpus")
    pids = int(_clamp(raw.get("pids_limit"), float(DEFAULT_CONTAINER_PIDS), 16.0, 512.0, "container.pids_limit"))
    return ContainerSpec(
        image=image,
        egress=tuple(dict.fromkeys(egress)),
        public_web=public_web,
        memory=memory.lower(),
        cpus=f"{cpus:g}",
        pids_limit=pids,
    )


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
        if raw.get("container") is not None and raw.get("cwd"):
            raise ValueError("'cwd' is a host path and cannot be combined with 'container'")
    else:
        if raw.get("container") is not None:
            raise ValueError("'container' applies to stdio mounts only")
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
        container=parse_container(raw["container"]) if raw.get("container") is not None else None,
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
