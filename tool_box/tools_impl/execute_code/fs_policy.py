"""Host side of the kernel filesystem guard: builds the policy fs_guard_runtime.py enforces.

The policy mirrors the session boundary ``file_operations`` applies (``_enforce_session_scope``): the
session's own directory is open, the project's cross-session and global directories (runtime/, data/,
output/, results/, logs, .env files) are not, and neither are the host paths it calls dangerous. On top of
that a cell gets what a file tool never needed to refuse: other processes' /proc entries (the app's
environment, including the provider keys the env scrub removed from the kernel's own), credential
directories in the user's home, and writes anywhere outside its workspace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import config

# Project-root entries a cell must not read: other sessions' workspaces, the SQLite files, artifacts and
# logs. ``log`` is the spelling file_operations allows next to ``logs``.
_PROJECT_PRIVATE_DIRS = ("runtime", "data", "output", "results", "logs", "log")

# Credential and config directories under the user's home (the kernel keeps the app's HOME).
_HOME_PRIVATE = (
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
    ".docker",
    ".kube",
    ".netrc",
    ".git-credentials",
    ".config/gagent",
    ".config/gh",
    ".config/gcloud",
)

# file_operations refuses these outright; /etc keeps the few entries libraries read at import time.
_SYSTEM_DENY = ("/etc", "/root", "/var/log", "/sys", "/run/secrets", "/var/run/secrets")
_SYSTEM_READABLE = (
    "/etc/ssl",
    "/etc/pki",
    "/etc/ca-certificates",
    "/etc/ca-certificates.conf",
    "/etc/localtime",
    "/etc/timezone",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/nsswitch.conf",
    "/etc/host.conf",
    "/etc/gai.conf",
    "/etc/protocols",
    "/etc/services",
    "/etc/mime.types",
    "/etc/fonts",
    "/etc/os-release",
    "/etc/lsb-release",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/sys/devices/system",
    "/sys/fs/cgroup",
)


def fs_guard_enabled() -> bool:
    """On by default. ``CODE_MODE_FS_GUARD=0`` is the operator's way back to the old, unguarded kernel."""
    return os.environ.get(config.ENV_FS_GUARD, "").strip() != "0"


def allow_subprocess() -> bool:
    return os.environ.get(config.ENV_ALLOW_SUBPROCESS, "").strip() == "1"


def _runtime_root() -> Path:
    try:
        from app.services.session_paths import get_runtime_root

        return Path(get_runtime_root())
    except Exception:  # noqa: BLE001 - the repo-local runtime dir is where sessions live by default
        return config._REPO_ROOT / "runtime"


def _session_dir(session_id: str) -> Optional[Path]:
    if not session_id:
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        return Path(get_runtime_session_dir(session_id, create=False))
    except Exception:  # noqa: BLE001 - an id that maps to no directory just gets no extra root
        return None


def _project_sibling_dirs(session_id: str) -> List[Path]:
    """Read-only workspaces of the same owner's other sessions in the same project.

    Recall (memory/context_recall.py) lets a new session continue from an earlier one's output and names
    that path in the prompt, so the cell must be able to open it. The scope is recall's own: same owner,
    same non-null project. Fixed when the kernel starts.
    """
    if not session_id:
        return []
    try:
        from app.repository.context_recall import project_session_ids
        from app.services.session_paths import get_runtime_session_dir

        found: List[Path] = []
        for sibling in project_session_ids(session_id):
            try:
                found.append(Path(get_runtime_session_dir(sibling, create=False)))
            except ValueError:
                continue
        return found
    except Exception:  # noqa: BLE001 - no database (unit tests, scripts): no siblings, the stricter answer
        return []


def _bio_tools_runtime(runtime_root: Path) -> Path:
    override = os.environ.get("BIO_TOOLS_RUNTIME_DIR", "").strip()
    return Path(override) if override else runtime_root / "bio_tools"


def _project_roots(runtime_root: Path) -> List[Path]:
    roots = [runtime_root.parent, config._REPO_ROOT]
    seen: List[Path] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def _env_files(root: Path) -> List[Path]:
    found = [root / ".env"]
    try:
        found.extend(sorted(p for p in root.glob(".env.*")))
    except OSError:
        pass
    return found


def _resolved(paths: Iterable[Path]) -> List[str]:
    """Each path as given and as resolved: a symlinked data/ must be refused under both names."""
    out: List[str] = []
    for path in paths:
        for candidate in (path, _safe_resolve(path)):
            text = str(candidate)
            if text and text not in out:
                out.append(text)
    return out


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path


def build_fs_policy(
    *,
    session_id: str,
    child_cwd: Path,
    scratch_dir: Path,
    kernel_dir: Optional[Path],
) -> Dict[str, object]:
    runtime_root = _runtime_root()

    rw: List[Path] = [Path(child_cwd), Path(scratch_dir)]
    if kernel_dir is not None:
        rw.append(Path(kernel_dir))
    session_dir = _session_dir(session_id)
    if session_dir is not None:
        rw.append(session_dir)

    deny: List[Path] = [runtime_root]
    for root in _project_roots(runtime_root):
        deny.extend(root / name for name in _PROJECT_PRIVATE_DIRS)
        deny.extend(_env_files(root))
    home = os.path.expanduser("~")
    if home and home != "~":
        deny.extend(Path(home) / name for name in _HOME_PRIVATE)
    deny.extend(Path(item) for item in _SYSTEM_DENY)

    bio_runtime = _bio_tools_runtime(runtime_root)
    # Native bio_tools runs keep their outputs under an unguessable run id outside any session. A cell can
    # open the paths a tool result names, but cannot list the directory to find other users' runs.
    blind = [bio_runtime / "_native" / "runs"]

    return {
        "allow_subprocess": allow_subprocess(),
        "rw_roots": _resolved(rw),
        "ro_roots": _resolved([Path(item) for item in _SYSTEM_READABLE] + _project_sibling_dirs(session_id)),
        "blind_roots": _resolved(blind),
        "deny_roots": _resolved(deny),
    }


def build_fs_policy_json(**kwargs) -> str:
    return json.dumps(build_fs_policy(**kwargs), sort_keys=True)
