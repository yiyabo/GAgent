"""Payload policy for the Docker API proxy (app/ops/docker_proxy.py).

WHY THIS EXISTS
The proxy is the app container's only path to the daemon socket, and its endpoint
allowlist deliberately does not look at payloads: ``containers/create`` can be
asked for arbitrary host binds, privileged mode, host networking or port
publication, and ``stop``/``kill``/``DELETE`` accept any container id. The app's
own builders (app/ops/sandbox.py) are the first line of defence, but they do not
sit between a *caller* and the proxy. This module is the missing contract — the
one LOCAL_INFRA §124 promised as the "sandbox-manager": it turns "which
endpoints" into "which containers".

SCOPE, HONESTLY
- It bounds the *shape* of a create payload: image, bind sources and modes,
  privileged/capability/namespace/port fields, and the requirement that every
  container the app manages carries a ``gagent.`` label. Lifecycle calls are
  then scoped to those labelled containers, so a wrong or compromised caller
  cannot stop or delete the course platform's containers.
- It cannot verify path *identity*: the proxy sees no host filesystem (only the
  daemon socket), so a symlink inside an allowed root that points elsewhere
  cannot be detected here. Bind sources are compared lexically.
- Report mode is the default (``DOCKER_PROXY_POLICY_ENFORCE`` unset): violations
  are logged as ``policy would-deny`` and never blocked, so real traffic can be
  observed before enforcement. Enforcement also requires ``HOST_RUNTIME_ROOT``;
  without it (dev boxes) the policy stays report-only on purpose.

Rules are derived from what the app actually does (container-lane audit,
2026-10-18: execute_code kernel, terminal PTY, interpreter, bio_tools, qwen-code
session driver), not from an ideal model, so no legitimate lane is refused.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs

logger = logging.getLogger("app.ops.docker_policy")

#: Sensitive host paths no caller may bind, whatever the mode. Checked before
#: the allowed-root rules, so a broad allowed root (e.g. /data) stays usable.
DEFAULT_DENIED_PREFIXES: Tuple[str, ...] = (
    "/etc",
    "/root",
    "/home",
    "/srv",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/var/run/docker.sock",
    "/var/lib/docker",
    "/data/docker",
    "/data/gagent-backups",
    "/data/home",
    "/data/gagent-git",
    "/data/logs",
    "/data/databases.bak",
)

#: NetworkMode values the app uses today: the sandbox network, the interpreter's
#: deliberate host-network path (it targets loopback proxy tunnels), and the
#: default bridge the SDK leaves in place. ``container:<id>`` is never allowed.
DEFAULT_ALLOWED_NETWORKS = frozenset({"", "default", "bridge", "none", "host", "gagent-sandbox"})

ALLOWED_SECURITY_OPTS = frozenset({"no-new-privileges", "no-new-privileges:true"})

#: Runtime images the app builds/uses, so the allowlist is never empty.
DEFAULT_RUNTIME_IMAGES: Tuple[str, ...] = (
    "gagent-sandbox-python",
    "gagent-sandbox-qwen",
    "gagent-python-runtime",
)

DEFAULT_BIO_TOOLS_CONFIG = "/app/tool_box/bio_tools/tools_config.json"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = "ok"


@dataclass(frozen=True)
class ProxyPolicy:
    enforce: bool = False
    runtime_root: str = ""
    ro_roots: Tuple[str, ...] = ()
    denied_prefixes: Tuple[str, ...] = DEFAULT_DENIED_PREFIXES
    allowed_images: frozenset = frozenset()
    allowed_image_prefixes: Tuple[str, ...] = ()
    label_prefix: str = "gagent."
    allowed_networks: frozenset = DEFAULT_ALLOWED_NETWORKS

    @property
    def configured(self) -> bool:
        return bool(self.runtime_root)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def _split_env(name: str) -> Tuple[str, ...]:
    raw = str(os.getenv(name) or "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def normalize_image(ref: str) -> str:
    """Docker-style normalization: drop the digest, default the tag to latest."""
    text = str(ref or "").strip()
    if not text:
        return ""
    if "@" in text:
        text = text.split("@", 1)[0]
    last = text.rsplit("/", 1)[-1]
    if ":" not in last:
        text = f"{text}:latest"
    return text


def load_bio_tool_images(config_path: str) -> Tuple[str, ...]:
    """Every ``image`` value in the bio_tools config (single source of truth)."""
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        logger.warning("docker_policy: bio_tools config unreadable at %s", config_path)
        return ()
    images: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            image = node.get("image")
            if isinstance(image, str) and image.strip():
                images.append(image.strip())
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(data)
    return tuple(images)


def load_policy_from_env() -> ProxyPolicy:
    runtime_root = str(os.getenv("HOST_RUNTIME_ROOT") or "").strip()
    refdata_root = str(os.getenv("GAGENT_REFDATA_DIR") or "/data").strip()
    ro_roots = tuple(
        dict.fromkeys(
            root
            for root in (refdata_root, *_split_env("DOCKER_PROXY_ALLOWED_RO_ROOTS"))
            if root
        )
    )

    images = set()
    for name in ("SANDBOX_PYTHON_IMAGE", "SANDBOX_QWEN_IMAGE"):
        value = str(os.getenv(name) or "").strip()
        if value:
            images.add(normalize_image(value))
    for base in DEFAULT_RUNTIME_IMAGES:
        images.add(f"{base}:latest")
    for value in _split_env("DOCKER_PROXY_ALLOWED_IMAGES"):
        images.add(normalize_image(value))
    config_path = str(os.getenv("DOCKER_PROXY_BIO_TOOLS_CONFIG") or DEFAULT_BIO_TOOLS_CONFIG).strip()
    for value in load_bio_tool_images(config_path):
        images.add(normalize_image(value))

    requested = str(os.getenv("DOCKER_PROXY_POLICY_ENFORCE", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    policy = ProxyPolicy(
        enforce=requested and bool(runtime_root),
        runtime_root=runtime_root,
        ro_roots=ro_roots,
        denied_prefixes=DEFAULT_DENIED_PREFIXES + _split_env("DOCKER_PROXY_DENY_PREFIXES"),
        allowed_images=frozenset(images),
        allowed_image_prefixes=_split_env("DOCKER_PROXY_ALLOWED_IMAGE_PREFIXES"),
    )
    logger.info(
        "docker_policy: enforce=%s runtime_root=%s ro_roots=%s images=%d prefixes=%s",
        policy.enforce,
        policy.runtime_root or "(unset)",
        list(policy.ro_roots),
        len(policy.allowed_images),
        list(policy.allowed_image_prefixes),
    )
    if requested and not runtime_root:
        logger.warning(
            "docker_policy: enforcement requested but HOST_RUNTIME_ROOT is unset; "
            "running report-only (host paths are unknowable here)"
        )
    return policy


# ---------------------------------------------------------------------------
# Path and mount rules
# ---------------------------------------------------------------------------

def _under(path: str, root: str) -> bool:
    if not root:
        return False
    candidate = posixpath.normpath(str(path))
    base = posixpath.normpath(str(root))
    return candidate == base or candidate.startswith(base.rstrip("/") + "/")


def _denied_prefix(path: str, policy: ProxyPolicy) -> Optional[str]:
    for prefix in policy.denied_prefixes:
        if _under(path, prefix):
            return prefix
    return None


def check_bind(source: str, mode: str, policy: ProxyPolicy) -> PolicyDecision:
    """One bind: writable only under the runtime root, read-only under ro roots."""
    text = str(source or "").strip()
    if not text:
        return PolicyDecision(False, "empty_bind_source")
    if not text.startswith("/"):
        return PolicyDecision(False, f"named_volume_bind:{text}")
    denied = _denied_prefix(text, policy)
    if denied:
        return PolicyDecision(False, f"denied_bind_path:{denied}")
    readonly = str(mode or "rw").lower() == "ro"
    if _under(text, policy.runtime_root):
        return PolicyDecision(True, "ok")
    if not readonly:
        return PolicyDecision(False, f"writable_bind_outside_runtime_root:{text}")
    if any(_under(text, root) for root in policy.ro_roots):
        return PolicyDecision(True, "ok")
    return PolicyDecision(False, f"readonly_bind_outside_allowed_roots:{text}")


def _bind_entries(host_config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """``HostConfig.Binds`` (CLI) and ``HostConfig.Mounts`` (API) as (src, mode)."""
    entries: List[Tuple[str, str]] = []
    for raw in host_config.get("Binds") or []:
        parts = str(raw).split(":")
        if len(parts) >= 3:
            entries.append((parts[0], parts[-1] or "rw"))
        elif len(parts) == 2:
            entries.append((parts[0], "rw"))
        else:
            entries.append((parts[0], "rw"))
    for item in host_config.get("Mounts") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("Type") or "bind").strip().lower() != "bind":
            entries.append((str(item.get("Source") or ""), "named_volume"))
            continue
        entries.append((str(item.get("Source") or ""), "ro" if item.get("ReadOnly") else "rw"))
    return entries


def _volume_entries(volumes: Any) -> List[Tuple[str, str]]:
    """The SDK's ``volumes={host: {"bind": path, "mode": mode}}`` form."""
    entries: List[Tuple[str, str]] = []
    if isinstance(volumes, dict):
        for key, spec in volumes.items():
            mode = "rw"
            if isinstance(spec, dict):
                mode = str(spec.get("mode") or ("ro" if spec.get("ReadOnly") else "rw"))
            entries.append((str(key), mode))
    elif isinstance(volumes, list):
        for item in volumes:
            entries.append((str(item), "rw"))
    return entries


def _label_keys(labels: Any) -> List[str]:
    if isinstance(labels, dict):
        return [str(key) for key in labels]
    if isinstance(labels, list):
        return [str(item).split("=", 1)[0] for item in labels]
    return []


def image_allowed(image: str, policy: ProxyPolicy) -> bool:
    if not image:
        return False
    if image in policy.allowed_images:
        return True
    return any(image.startswith(prefix) for prefix in policy.allowed_image_prefixes)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def evaluate_container_create(body: bytes, policy: ProxyPolicy) -> PolicyDecision:
    """Validate one ``POST /containers/create`` payload."""
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return PolicyDecision(False, "invalid_json_body")
    if not isinstance(payload, dict):
        return PolicyDecision(False, "invalid_create_payload")

    image = normalize_image(payload.get("Image") or "")
    if not image_allowed(image, policy):
        return PolicyDecision(False, f"image_not_allowed:{image or '(missing)'}")

    if not any(key.startswith(policy.label_prefix) for key in _label_keys(payload.get("Labels"))):
        return PolicyDecision(False, f"missing_{policy.label_prefix}label")

    host_config = payload.get("HostConfig") or {}
    if not isinstance(host_config, dict):
        return PolicyDecision(False, "invalid_host_config")

    if host_config.get("Privileged"):
        return PolicyDecision(False, "host_config_privileged")
    if host_config.get("CapAdd"):
        return PolicyDecision(False, "host_config_cap_add")
    if host_config.get("Devices"):
        return PolicyDecision(False, "host_config_devices")
    if host_config.get("PidMode"):
        return PolicyDecision(False, f"host_config_pid_mode:{host_config.get('PidMode')}")
    if str(host_config.get("IpcMode") or "").strip().lower() == "host":
        return PolicyDecision(False, "host_config_ipc_host")
    if host_config.get("UsernsMode"):
        return PolicyDecision(False, "host_config_userns")
    if host_config.get("CgroupParent") or host_config.get("Runtime") or host_config.get("Sysctls"):
        return PolicyDecision(False, "host_config_unsupported_field")
    if host_config.get("PortBindings") or host_config.get("PublishAllPorts"):
        return PolicyDecision(False, "host_config_port_binding")
    for option in host_config.get("SecurityOpt") or []:
        if str(option).strip().lower() not in ALLOWED_SECURITY_OPTS:
            return PolicyDecision(False, f"host_config_security_opt:{option}")
    network = str(host_config.get("NetworkMode") or "").strip().lower()
    if network not in policy.allowed_networks:
        return PolicyDecision(False, f"host_config_network:{network}")

    for source, mode in _bind_entries(host_config) + _volume_entries(payload.get("Volumes")):
        if mode == "named_volume":
            return PolicyDecision(False, f"named_volume_bind:{source}")
        decision = check_bind(source, mode, policy)
        if not decision.allowed:
            return decision
    return PolicyDecision(True, "ok")


def evaluate_container_operation(
    action: str,
    inspect: Optional[Dict[str, Any]],
    policy: ProxyPolicy,
) -> PolicyDecision:
    """start/stop/kill/wait/delete may only touch containers the app labelled."""
    if not isinstance(inspect, dict):
        # The daemon could not describe the target (usually 404). Let the real
        # request answer; a missing container is not a policy question.
        return PolicyDecision(True, "inspect_unavailable")
    config = inspect.get("Config") or {}
    if not any(key.startswith(policy.label_prefix) for key in _label_keys(config.get("Labels"))):
        return PolicyDecision(False, f"{action}_unlabelled_container")
    return PolicyDecision(True, "ok")


def evaluate_containers_list(query: bytes, policy: ProxyPolicy) -> PolicyDecision:
    """``GET /containers/json`` must be scoped to the app's own labels."""
    try:
        params = parse_qs(query.decode("utf-8"))
    except UnicodeDecodeError:
        return PolicyDecision(False, "invalid_query")
    raw = (params.get("filters") or [""])[0]
    try:
        filters = json.loads(raw) if raw else {}
    except ValueError:
        return PolicyDecision(False, "invalid_filters")
    if not isinstance(filters, dict):
        return PolicyDecision(False, "invalid_filters")
    labels = filters.get("label")
    if isinstance(labels, dict):
        keys: Iterable[str] = labels.keys()
    elif isinstance(labels, list):
        keys = [str(item).split("=", 1)[0] for item in labels]
    else:
        keys = []
    if not any(str(key).startswith(policy.label_prefix) for key in keys):
        return PolicyDecision(False, "list_without_gagent_label_filter")
    return PolicyDecision(True, "ok")


__all__ = [
    "PolicyDecision",
    "ProxyPolicy",
    "check_bind",
    "evaluate_container_create",
    "evaluate_container_operation",
    "evaluate_containers_list",
    "image_allowed",
    "load_bio_tool_images",
    "load_policy_from_env",
    "normalize_image",
]
