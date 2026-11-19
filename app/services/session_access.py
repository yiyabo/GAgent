from sqlite3 import Connection

from fastapi import HTTPException, Request

from app.services.request_principal import LEGACY_LOCAL_OWNER_ID, get_request_owner_id
from app.services.session_paths import normalize_session_base


def ensure_session_alias_owner(conn: Connection, session_id: str, owner_id: str) -> None:
    """Reject a new owner before it can claim an existing session directory."""
    base = normalize_session_base(session_id)
    normalized_owner = str(owner_id or "").strip() or LEGACY_LOCAL_OWNER_ID
    for row in conn.execute("SELECT id, owner_id FROM chat_sessions").fetchall():
        if normalize_session_base(row["id"]) != base:
            continue
        existing_owner = str(row["owner_id"] or "").strip() or LEGACY_LOCAL_OWNER_ID
        if existing_owner != normalized_owner:
            raise PermissionError(f"Session {session_id} belongs to another owner")


def ensure_session_alias_access(conn: Connection, session_id: str, request: Request) -> None:
    try:
        ensure_session_alias_owner(conn, session_id, get_request_owner_id(request))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="session owner mismatch") from exc
