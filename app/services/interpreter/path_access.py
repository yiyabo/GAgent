"""Owner-scoped path checks for interpreter HTTP inputs.

Interpreter endpoints take raw filesystem paths from the client. A path is
accepted only when, after resolving symlinks and ``..``, it lies inside a
session directory owned by the requesting principal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from fastapi import HTTPException, Request

from app.database import get_db
from app.services.request_principal import LEGACY_LOCAL_OWNER_ID, get_request_owner_id
from app.services.session_paths import (
    get_legacy_info_sessions_root,
    get_runtime_root,
    normalize_session_base,
)

_FORBIDDEN_DETAIL = "Path is outside your session storage"


def _session_base_for(path: Path) -> Optional[str]:
    for root in (get_runtime_root(), get_legacy_info_sessions_root()):
        try:
            relative = path.relative_to(root.resolve())
        except ValueError:
            continue
        if not relative.parts:
            return None
        base = normalize_session_base(relative.parts[0])
        if base and relative.parts[0].startswith("session"):
            return base
    return None


def _owner_session_bases(owner_id: str) -> set[str]:
    sql = "SELECT id FROM chat_sessions WHERE owner_id=?"
    if owner_id == LEGACY_LOCAL_OWNER_ID:
        sql += " OR owner_id IS NULL OR TRIM(owner_id)=''"
    with get_db() as conn:
        rows = conn.execute(sql, (owner_id,)).fetchall()
    return {normalize_session_base(row["id"]) for row in rows} - {""}


def resolve_owned_path(request: Request, raw_path: str) -> str:
    """Return the resolved path, or raise 403 if the caller does not own it."""
    text = str(raw_path or "").strip()
    if not text or "\x00" in text:
        raise HTTPException(status_code=400, detail="Invalid path")
    resolved = Path(text).expanduser().resolve()
    base = _session_base_for(resolved)
    if base is None or base not in _owner_session_bases(get_request_owner_id(request)):
        raise HTTPException(status_code=403, detail=_FORBIDDEN_DETAIL)
    return str(resolved)


def resolve_owned_paths(request: Request, raw_paths: Iterable[str]) -> List[str]:
    return [resolve_owned_path(request, raw) for raw in raw_paths]


def resolve_optional_owned_path(request: Request, raw_path: Optional[str]) -> Optional[str]:
    if raw_path is None or not str(raw_path).strip():
        return None
    return resolve_owned_path(request, raw_path)
