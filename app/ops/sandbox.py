"""Hardened run-args for sibling sandbox containers (P3).

WHY THIS EXISTS
``execute_code`` used to run model-written Python in a child process *inside*
the app container (``subprocess.Popen`` in
``tool_box/tools_impl/execute_code/kernel.py``), and the terminal's ``sandbox``
mode was an in-process PTY (``pty.openpty()`` + ``/bin/bash``) — both reach the
app's filesystem and its environment directly. P3 moves that work into
*sibling* containers created through the Docker API proxy
(``app/ops/docker_proxy.py``).

Sibling containers are Docker-outside-of-Docker: the daemon resolves every
``-v`` SOURCE on the HOST, never inside the app container. A mount whose
container path is ``/app/runtime/session_x`` therefore has to be written as
``$(HOST_RUNTIME_ROOT)/session_x:/app/runtime/session_x``. That translation is
``app.services.session_paths.host_path_for``, applied here once for every
caller instead of at each of the four creation sites.

The hardening profile lives in ONE place on purpose: execute_code kernel,
docker interpreter, bio_tools and the terminal must not drift apart. The
builders are pure — they return argv / kwargs and never call docker — so the
profile is unit-testable and each caller keeps its own transport (CLI argv, or
the SDK's ``containers.run(**kwargs)``).

Network default: ``gagent-sandbox`` — the compose network that only the app and
the sandboxes share (``internal: true``, no route off the host). A sandbox can
therefore reach the app (its tool RPC and cell channel) but NOT the internet;
cells that need the outside world call tools over RPC, which is the design.
``SANDBOX_NETWORK=none`` cuts even that, ``SANDBOX_NETWORK=host`` is refused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import List, Mapping, Optional, Sequence, Tuple

from app.services.session_paths import (
    get_host_runtime_root,
    get_runtime_root,
    host_path_for,
)

DEFAULT_NETWORK = "gagent-sandbox"
DEFAULT_PIDS_LIMIT = 512
DEFAULT_TMPFS_SIZE = "2g"
DEFAULT_WORKDIR = "/workspace"

LABEL_SESSION = "gagent.session"
LABEL_KIND = "gagent.kind"

#: Refused outright: host networking would put a sandbox back on the app's own
#: namespace, which is exactly what P3 is undoing.
_FORBIDDEN_NETWORKS = frozenset({"host", "container"})


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or "").strip() or default


def sandbox_network() -> str:
    """Network for sandbox containers; ``host``/``container:*`` are refused.

    ``--network container:<id>`` would join another container's namespace —
    including the app's own — which is the escape this whole module exists to
    prevent, so it falls back to the sandbox network like ``host`` does.
    """
    value = _env("SANDBOX_NETWORK", DEFAULT_NETWORK)
    if value in _FORBIDDEN_NETWORKS or value.startswith("container:"):
        return DEFAULT_NETWORK
    return value


def sandbox_python_image() -> str:
    return _env("SANDBOX_PYTHON_IMAGE", "gagent-sandbox-python:latest")


def sandbox_qwen_image() -> str:
    return _env("SANDBOX_QWEN_IMAGE", "gagent-sandbox-qwen:latest")


def sandbox_memory() -> Optional[str]:
    return _env("SANDBOX_MEM") or None


def sandbox_cpus() -> Optional[str]:
    return _env("SANDBOX_CPUS") or None


def sandbox_pids_limit() -> int:
    try:
        return max(64, int(_env("SANDBOX_PIDS_LIMIT", str(DEFAULT_PIDS_LIMIT))))
    except ValueError:
        return DEFAULT_PIDS_LIMIT


def translate_mount(
    container_path: str,
    *,
    mode: str = "rw",
    target: Optional[str] = None,
) -> str:
    """``-v`` value for a sibling container.

    *container_path* is the path as seen INSIDE the app container; the source is
    translated to its host path (a no-op when ``HOST_RUNTIME_ROOT`` is unset, as
    on a host-network dev box). *target* defaults to the same path, which keeps
    every in-container path identical across the app and its sandboxes.
    """
    source = host_path_for(str(container_path))
    dest = str(target or container_path)
    return f"{source}:{dest}:{mode}"


def require_translatable(container_path: str) -> str:
    """Host path for a bind source, refusing anything outside the runtime root.

    With ``HOST_RUNTIME_ROOT`` set we are a container talking to the host
    daemon, so a source that does not live under the runtime root has no known
    host path — passing it through unchanged would ask the daemon to mount a
    path that exists only inside this container (i.e. nothing, or the wrong
    thing). Fail closed instead. Paths that are *meant* to be same-path
    (``/data``, which compose binds identically on both sides) are the
    caller's business and do not go through here.
    """
    source = host_path_for(str(container_path))
    if get_host_runtime_root() and source == str(container_path):
        raise ValueError(
            "refusing to mount %s: outside the runtime root (%s) and "
            "HOST_RUNTIME_ROOT is set, so its host path is unknown"
            % (container_path, get_runtime_root())
        )
    return source


def build_sandbox_env(
    *,
    home: str = "/tmp",
    workdir: Optional[str] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> dict:
    """The sandbox's environment: an explicit allowlist, never inherited.

    The local kernel path scrubs the host env (``env_scrub``) because it must
    inherit PATH/HOME to run at all. A container starts from the image's own
    environment, so the correct thing here is *not* to forward the app's values
    at all — only names whose values we compute. That is strictly tighter than
    scrubbing: nothing from ``/app/.env`` can ride along.
    """
    env = {
        "HOME": home,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    if workdir:
        env["WORKSPACE"] = workdir
        env["DATA_DIR"] = workdir
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


@dataclass(frozen=True)
class SandboxRunSpec:
    """Everything one sandbox container needs, transport-independent."""

    image: str
    kind: str
    command: Sequence[str] = ()
    workdir: Optional[str] = DEFAULT_WORKDIR
    name: Optional[str] = None
    session_id: Optional[str] = None
    user: Optional[str] = None
    #: (container_path, mode) — sources are host-translated at build time.
    mounts: Sequence[Tuple[str, str]] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    network: Optional[str] = None
    memory: Optional[str] = None
    cpus: Optional[str] = None
    pids_limit: Optional[int] = None
    read_only: bool = True
    tmpfs_size: str = DEFAULT_TMPFS_SIZE
    detach: bool = True
    labels: Mapping[str, str] = field(default_factory=dict)
    extra_args: Sequence[str] = ()

    def labels_with_defaults(self) -> dict:
        labels = {str(k): str(v) for k, v in self.labels.items()}
        if self.session_id:
            labels.setdefault(LABEL_SESSION, str(self.session_id))
        if self.kind:
            labels.setdefault(LABEL_KIND, str(self.kind))
        return labels

    def resolved_network(self) -> str:
        return self.network or sandbox_network()


def _hardening_args(spec: SandboxRunSpec) -> List[str]:
    args: List[str] = [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={int(spec.pids_limit or sandbox_pids_limit())}",
    ]
    if spec.user:
        args.extend(["--user", spec.user])
    if spec.read_only:
        args.append("--read-only")
        if spec.tmpfs_size:
            args.extend(["--tmpfs", f"/tmp:rw,size={spec.tmpfs_size}"])
    if spec.memory:
        args.append(f"--memory={spec.memory}")
    if spec.cpus:
        args.append(f"--cpus={spec.cpus}")
    return args


def build_sandbox_run_args(spec: SandboxRunSpec, *, docker_bin: str = "docker") -> List[str]:
    """``docker run`` argv for a sandbox container (CLI transport)."""
    args: List[str] = [docker_bin, "run"]
    if spec.detach:
        args.append("-d")
    args.append("--rm")
    if spec.name:
        args.extend(["--name", str(spec.name)])
    for key, value in sorted(spec.labels_with_defaults().items()):
        args.extend(["--label", f"{key}={value}"])
    args.extend(_hardening_args(spec))
    args.extend(["--network", spec.resolved_network()])
    for container_path, mode in spec.mounts:
        args.extend(["-v", translate_mount(container_path, mode=mode)])
    for key, value in sorted(spec.env.items()):
        args.extend(["-e", f"{key}={value}"])
    if spec.workdir:
        args.extend(["-w", str(spec.workdir)])
    args.extend(str(item) for item in spec.extra_args)
    args.append(str(spec.image))
    args.extend(str(item) for item in spec.command)
    return args


def as_run_kwargs(spec: SandboxRunSpec) -> dict:
    """``containers.run(**kwargs)`` for the SDK transport.

    Only the subset the SDK understands; hardening flags the SDK does not
    expose as kwargs (pids limit, tmpfs) are carried by ``extra_host_config``
    so the two transports stay equivalent.
    """
    kwargs: dict = {
        "image": str(spec.image),
        "command": [str(item) for item in spec.command] or None,
        "detach": bool(spec.detach),
        "remove": True,
        "labels": spec.labels_with_defaults(),
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "network": spec.resolved_network(),
        "volumes": {
            host_path_for(str(container_path)): {"bind": str(container_path), "mode": mode}
            for container_path, mode in spec.mounts
        },
        "environment": {str(k): str(v) for k, v in spec.env.items()},
        "working_dir": str(spec.workdir) if spec.workdir else None,
        "read_only": bool(spec.read_only),
    }
    if spec.name:
        kwargs["name"] = str(spec.name)
    if spec.user:
        kwargs["user"] = str(spec.user)
    if spec.memory:
        kwargs["mem_limit"] = str(spec.memory)
    if spec.cpus:
        kwargs["nano_cpus"] = int(float(spec.cpus) * 1_000_000_000)
    host_config: dict = {"pids_limit": int(spec.pids_limit or sandbox_pids_limit())}
    if spec.read_only and spec.tmpfs_size:
        host_config["tmpfs"] = {"/tmp": f"rw,size={spec.tmpfs_size}"}
    kwargs["extra_host_config"] = host_config
    return {key: value for key, value in kwargs.items() if value is not None}


def sandbox_name(prefix: str, token: str) -> str:
    """Deterministic, label-friendly container name."""
    safe = "".join(ch for ch in str(token) if ch.isalnum() or ch in "-_")[:32]
    return f"{prefix}-{safe}" if safe else prefix


def without_name(spec: SandboxRunSpec) -> SandboxRunSpec:
    return replace(spec, name=None)
