"""Child-process environment for execute_code: secret scrubbing + RPC injection.

The repo ``.env`` carries live provider keys, so the kernel child must NOT
inherit the host process env wholesale. Order of rules (copied from Hermes):
(1) secret-substring block; (2) safe-prefix allowlist; (3) RPC endpoint/token
and kernel protocol vars injected AFTER the scrub (they never ride the
inherited env). The scrubbed env is a safety envelope, not a jail.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional

# "PASS" alone is intentionally absent (false-positives on COMPASS_DIR etc.);
# PASSWORD/PASSWD cover credentials.
_SECRET_SUBSTRINGS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "WEBHOOK",
    "BEARER",
    "APIKEY",
)

_SAFE_ENV_PREFIXES = (
    "PATH",
    "HOME",
    "LANG",
    "LC_",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SHELL",
    "USER",
    "LOGNAME",
    "XDG_",
    "TERM",
)

# Kernel/RPC protocol variables injected after scrubbing. Only these may carry
# the per-spawn RPC token, and only via the child's env (never argv/logs).
ENV_RPC_ENDPOINT = "GAGENT_RPC_ENDPOINT"
ENV_RPC_TOKEN = "GAGENT_RPC_TOKEN"
ENV_KERNEL_SENTINEL = "GAGENT_KERNEL_SENTINEL"
ENV_KERNEL_SPILL_DIR = "GAGENT_KERNEL_SPILL_DIR"


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(fragment in upper for fragment in _SECRET_SUBSTRINGS)


def _is_safe_name(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _SAFE_ENV_PREFIXES)


def scrub_child_env(source_env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Block secret-looking names, then keep only safe prefixes."""
    source = dict(source_env if source_env is not None else os.environ)
    return {
        key: value
        for key, value in source.items()
        if not _is_secret_name(key) and _is_safe_name(key)
    }


def build_child_env(
    *,
    rpc_endpoint: str,
    rpc_token: str,
    kernel_dir: Path,
    sentinel: str,
    source_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Scrubbed env + injected kernel protocol variables.

    ``kernel_dir`` heads PYTHONPATH so the generated ``gagent_tools`` stub
    module is importable inside the kernel.
    """
    env = scrub_child_env(source_env)
    env[ENV_RPC_ENDPOINT] = rpc_endpoint
    env[ENV_RPC_TOKEN] = rpc_token
    env[ENV_KERNEL_SENTINEL] = sentinel
    env[ENV_KERNEL_SPILL_DIR] = str(kernel_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{kernel_dir}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(kernel_dir)
    )
    return env
