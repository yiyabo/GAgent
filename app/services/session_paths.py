"""Session-scoped path helpers.

This module centralizes path rules for runtime session artifacts and keeps
legacy `data/information_sessions` compatibility for cleanup and migration.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_ROOT = (_PROJECT_ROOT / "runtime").resolve()
_LEGACY_INFO_SESSIONS_ROOT = (_PROJECT_ROOT / "data" / "information_sessions").resolve()
_UNSAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9_-]+")

logger = logging.getLogger(__name__)

# Reserved top-level directory names under each session root.
# These names MUST be directories — a regular file squatting on any of them
# would break PathRouter / PathResolution / plan execution.  We pre-create
# them at session-dir creation time so that a misbehaving copy/move that
# targets ``{session_dir}/<reserved_name>`` lands *inside* the directory
# rather than replacing it with a regular file.
_RESERVED_SESSION_TOPLEVEL_DIRS: tuple[str, ...] = (
    "raw_files",
    "uploads",
    "deliverables",
    "tool_outputs",
)


def normalize_session_base(session_id: str) -> str:
    """Normalize session id by stripping repeated `session_`/`session-` prefixes."""
    token = str(session_id or "").strip()
    while True:
        if token.startswith("session_"):
            token = token[len("session_") :]
            continue
        if token.startswith("session-"):
            token = token[len("session-") :]
            continue
        break
    token = _UNSAFE_SESSION_CHARS.sub("-", token)
    token = token.strip("-_")
    if len(token) > 128:
        token = token[:128]
    return token


def get_runtime_root() -> Path:
    override = os.getenv("APP_RUNTIME_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return _RUNTIME_ROOT


def get_legacy_info_sessions_root() -> Path:
    override = os.getenv("APP_INFO_SESSIONS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return _LEGACY_INFO_SESSIONS_ROOT


def get_runtime_session_dir(session_id: str, *, create: bool = False) -> Path:
    """Return `runtime/session_<base>` for the provided session id.

    When *create* is True, also pre-creates the reserved top-level
    subdirectories (`raw_files/`, `uploads/`, `deliverables/`,
    `tool_outputs/`) plus `deliverables/latest/`.  This guards against
    tools writing a regular file at one of these reserved names
    (e.g. `shutil.copy2(src, "{session}/raw_files")`), which would
    break PathRouter and all downstream plan execution.
    """
    session_base = normalize_session_base(session_id)
    if not session_base:
        raise ValueError("session_id is required")
    session_dir = get_runtime_root() / f"session_{session_base}"
    if create:
        session_dir.mkdir(parents=True, exist_ok=True)
        _ensure_reserved_session_dirs(session_dir)
    return session_dir.resolve()


def _ensure_reserved_session_dirs(session_dir: Path) -> None:
    """Pre-create reserved top-level session subdirectories.

    Tolerates an existing regular file squatting on a reserved name
    (it will not crash session creation — downstream path validators
    are expected to surface the problem).  Always re-creates
    `deliverables/latest/` since it is required by artifact publication.
    """
    for name in _RESERVED_SESSION_TOPLEVEL_DIRS:
        path = session_dir / name
        try:
            path.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            logger.warning(
                "session %s has a non-directory entry at '%s'; reserved dir not created",
                session_dir.name, name,
            )
    latest = session_dir / "deliverables" / "latest"
    try:
        latest.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        logger.warning(
            "session %s has a non-directory entry at 'deliverables/latest'; latest dir not created",
            session_dir.name,
        )


def get_session_raw_files_dir(session_id: str, *, create: bool = False) -> Path:
    """Return ``runtime/session_<base>/raw_files`` (directory)."""
    session_dir = get_runtime_session_dir(session_id, create=create)
    raw_files_dir = session_dir / "raw_files"
    if create:
        raw_files_dir.mkdir(parents=True, exist_ok=True)
    return raw_files_dir.resolve()


def get_session_deliverables_dir(session_id: str, *, create: bool = False) -> Path:
    """Return ``runtime/session_<base>/deliverables/latest`` (directory)."""
    session_dir = get_runtime_session_dir(session_id, create=create)
    deliverables_dir = session_dir / "deliverables" / "latest"
    if create:
        deliverables_dir.mkdir(parents=True, exist_ok=True)
    return deliverables_dir.resolve()


def get_session_upload_dir(session_id: str, *, create: bool = False) -> Path:
    session_dir = get_runtime_session_dir(session_id, create=create)
    upload_dir = session_dir / "uploads"
    if create:
        upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir.resolve()


def get_session_tool_outputs_dir(session_id: str, *, create: bool = False) -> Path:
    session_dir = get_runtime_session_dir(session_id, create=create)
    output_dir = session_dir / "tool_outputs"
    if create:
        output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir.resolve()


def get_session_phagescope_work_dir(session_id: str, *, create: bool = False) -> Path:
    session_dir = get_runtime_session_dir(session_id, create=create)
    phagescope_dir = session_dir / "work" / "phagescope"
    if create:
        phagescope_dir.mkdir(parents=True, exist_ok=True)
    return phagescope_dir.resolve()


def get_session_storage_candidates(session_id: str, *, include_legacy: bool = True) -> List[Path]:
    """Return possible on-disk session directories (new runtime + legacy paths)."""
    base = normalize_session_base(session_id)
    if not base:
        return []

    candidates: List[Path] = []
    try:
        candidates.append(get_runtime_session_dir(base, create=False))
    except ValueError:
        return []

    if include_legacy:
        legacy_names = {
            f"session-{base}",
            f"session_{base}",
            f"session-session_{base}",
            f"session-session-{base}",
        }

        legacy_root = get_legacy_info_sessions_root()
        for item in sorted(legacy_names):
            candidates.append((legacy_root / item).resolve())

    deduped: List[Path] = []
    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


__all__ = [
    "get_legacy_info_sessions_root",
    "get_runtime_root",
    "get_runtime_session_dir",
    "get_session_phagescope_work_dir",
    "get_session_storage_candidates",
    "get_session_tool_outputs_dir",
    "get_session_upload_dir",
    "normalize_session_base",
]
