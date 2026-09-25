"""Project/session path resolution and context-file reading.

``_PROJECT_ROOT`` is monkeypatched on the package namespace by tests (22 sites),
so this module never holds its own copy: every read goes through the late-bound
``_facade()`` accessor, which returns the package ``__init__`` module object at
call time. ``_resolve_session_dir`` keeps its function-level
``app.services.path_router`` import so the tool_box -> app import stays lazy.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, List, Optional

from .config import (
    _ALLOWED_TEXT_EXTENSIONS,
    _CONTEXT_FILE_MAX_BYTES,
    _TRUTHY_VALUES,
)

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import manuscript_writer as facade

    return facade


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY_VALUES


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except AttributeError:
        return str(path).startswith(str(root))


_PROJECT_ARTIFACT_SUBDIRS = frozenset({"runtime", "results", "data", "log", "output"})


def _resolve_project_path(path_str: str) -> Path:
    raw_path = Path(path_str)
    if not raw_path.is_absolute():
        raw_path = _facade()._PROJECT_ROOT / raw_path
    resolved = raw_path.resolve()
    if not _is_relative_to(resolved, _facade()._PROJECT_ROOT):
        raise ValueError(f"Path is outside project root: {path_str}")
    return resolved


def _is_disallowed_project_source_write(resolved: Path) -> bool:
    if not _is_relative_to(resolved, _facade()._PROJECT_ROOT):
        return False
    try:
        rel = resolved.relative_to(_facade()._PROJECT_ROOT)
    except Exception:
        return True
    parts = rel.parts
    if not parts:
        return True
    return parts[0] not in _PROJECT_ARTIFACT_SUBDIRS


def _session_tmp_output_dir(session_dir: Path) -> Path:
    tmp_dir = (session_dir / "raw_files" / "tmp").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def _is_project_level_results_write(resolved: Path) -> bool:
    """True when *resolved* points at the project-level ``results/`` tree.

    Project-root ``results/`` sits outside every session workspace, so files
    written there are invisible to the session-scoped Artifacts UI.  Within a
    session context these writes are redirected into the session instead.
    """
    return _is_relative_to(resolved, (_facade()._PROJECT_ROOT / "results").resolve())


def _resolve_session_scoped_project_path(path_str: str, session_dir: Optional[Path]) -> Path:
    raw_path = Path(path_str).expanduser()
    if session_dir is not None:
        tmp_dir = _session_tmp_output_dir(session_dir)
        if not raw_path.is_absolute():
            resolved = (tmp_dir / raw_path).resolve()
            if not _is_relative_to(resolved, session_dir):
                raise ValueError(f"Path escapes session workspace: {path_str}")
            return resolved
        resolved = raw_path.resolve()
        if _is_relative_to(resolved, session_dir):
            try:
                rel = resolved.relative_to(session_dir.resolve())
                if len(rel.parts) == 1:
                    return (tmp_dir / rel.name).resolve()
            except Exception:
                pass
            return resolved
        if _is_disallowed_project_source_write(resolved) or _is_project_level_results_write(resolved):
            redirected = (tmp_dir / resolved.name).resolve()
            if not _is_relative_to(redirected, session_dir):
                raise ValueError(f"Path escapes session workspace: {path_str}")
            return redirected
        return resolved
    if raw_path.is_absolute():
        resolved = _resolve_project_path(str(raw_path))
    else:
        resolved = _resolve_project_path(path_str)
    if _is_disallowed_project_source_write(resolved):
        raise ValueError(
            "Writing to project repository root/source tree is not allowed; "
            "provide a session_id or an output path under runtime/results/data."
        )
    return resolved


def _read_text_file(path: Path, max_bytes: int) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"Expected file, got directory: {path}")
    file_size = path.stat().st_size
    with path.open("rb") as handle:
        if file_size > max_bytes:
            content = handle.read(max_bytes)
            suffix = (
                f"\n\n[TRUNCATED] File size {file_size} bytes exceeds limit {max_bytes} bytes."
            )
            return content.decode("utf-8", errors="replace") + suffix
        return handle.read().decode("utf-8", errors="replace")


def _build_context_blocks(paths: Iterable[str], max_bytes: int) -> str:
    blocks: List[str] = []
    for raw in paths:
        if not raw:
            continue
        try:
            path = _resolve_project_path(raw)
            if path.suffix.lower() not in _ALLOWED_TEXT_EXTENSIONS:
                logger.warning("Skipping unsupported context file type: %s", path)
                continue
            content = _read_text_file(path, min(max_bytes, _CONTEXT_FILE_MAX_BYTES))
            rel = path.relative_to(_facade()._PROJECT_ROOT)
            blocks.append(f"### File: {rel}\n{content}")
        except Exception as exc:
            logger.warning("Failed to read context file %s: %s", raw, exc)
    return "\n\n".join(blocks).strip()


def _default_analysis_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".analysis.md")


def _resolve_session_dir(session_id: Optional[str]) -> Optional[Path]:
    if not session_id:
        return None
    normalized = str(session_id).strip()
    if not normalized:
        return None
    from app.services.path_router import get_path_router
    router = get_path_router()
    # Return session directory itself, not tool-specific subdirectory
    return router.get_session_dir(normalized, create=True)
