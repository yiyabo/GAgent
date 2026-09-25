"""Session directory discovery and path safety for artifact routes.

The former single runtime root has a legacy information-sessions sibling, so a
session id may resolve to several candidate directories; ``_resolve_session_dir``
scores them by purpose. Storage-layout logic lives here, not in the endpoints.

``RUNTIME_DIR``/``INFO_SESSIONS_DIR`` are owned by the package facade and are
patched by tests, so they are read through the facade at call time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Literal, Tuple

from fastapi import HTTPException, Request, status

from app.database import get_db
from app.services.request_principal import ensure_owner_access
from app.services.session_paths import normalize_session_base


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import artifact_routes as facade

    return facade


def _assert_path_within(target: Path, root: Path, detail: str = "Invalid path") -> None:
    """Raise HTTP 400 if *target* is not inside *root* (resolves symlinks)."""
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _strip_session_prefixes(value: str) -> str:
    return normalize_session_base(value)


def _runtime_root_dir() -> Path:
    override = os.getenv("APP_RUNTIME_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(_facade().RUNTIME_DIR).resolve()


def _info_sessions_root_dir() -> Path:
    override = os.getenv("APP_INFO_SESSIONS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(_facade().INFO_SESSIONS_DIR).resolve()


def _find_session_candidates(root: Path, *, session_base: str) -> List[Path]:
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        return []

    candidates: List[Path] = []
    for item in resolved_root.iterdir():
        if not item.is_dir():
            continue
        try:
            candidate = item.resolve()
        except Exception:
            continue
        if not str(candidate).startswith(str(resolved_root)):
            continue
        if _strip_session_prefixes(item.name) != session_base:
            continue
        candidates.append(candidate)
    return candidates


def _candidate_score(candidate: Path, *, purpose: str, source: str) -> Tuple[int, float]:
    score = 0
    deliverables_root = candidate / "deliverables"
    deliverables_manifest = deliverables_root / "manifest_latest.json"
    deliverables_latest = deliverables_root / "latest"
    raw_files = candidate / "raw_files"
    tool_outputs = candidate / "tool_outputs"

    if purpose == "raw":
        if raw_files.exists():
            score += 140
        if tool_outputs.exists():
            score += 60
        # New writes are runtime-first; keep info root as legacy fallback.
        if source == "runtime":
            score += 20
        elif source == "info":
            score += 5
        if deliverables_manifest.exists() or deliverables_latest.exists():
            score += 5
    elif purpose == "deliverables":
        if deliverables_manifest.exists():
            score += 120
        elif deliverables_latest.exists():
            score += 90
        elif deliverables_root.exists():
            score += 60
        if source == "runtime":
            score += 15
    else:
        if deliverables_root.exists():
            score += 40
        if tool_outputs.exists():
            score += 40

    try:
        modified = candidate.stat().st_mtime
    except Exception:
        modified = 0.0
    return score, modified


def _resolve_session_dir(
    session_id: str,
    *,
    purpose: Literal["raw", "deliverables", "generic"] = "generic",
) -> Path:
    normalized = session_id.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required")

    session_base = _strip_session_prefixes(normalized)
    if not session_base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is invalid")

    runtime_root = _runtime_root_dir()
    info_root = _info_sessions_root_dir()
    runtime_candidates = _find_session_candidates(runtime_root, session_base=session_base)
    info_candidates = _find_session_candidates(info_root, session_base=session_base)

    combined: List[Tuple[Path, str]] = []
    combined.extend((item, "runtime") for item in runtime_candidates)
    combined.extend((item, "info") for item in info_candidates)
    if not combined:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session artifacts not found")

    ranked = sorted(
        combined,
        key=lambda item: _candidate_score(item[0], purpose=purpose, source=item[1]),
        reverse=True,
    )
    return ranked[0][0]


def _ensure_session_access(session_id: str, request: Request) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT owner_id FROM chat_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    ensure_owner_access(request, row["owner_id"], detail="session owner mismatch")


def _deliverables_root(session_dir: Path) -> Path:
    return session_dir / "deliverables"


def _deliverables_latest_dir(session_dir: Path) -> Path:
    return _deliverables_root(session_dir) / "latest"


def _deliverables_history_dir(session_dir: Path) -> Path:
    return _deliverables_root(session_dir) / "history"
