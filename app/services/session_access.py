from sqlite3 import Connection

from fastapi import Request

from app.services.request_principal import ensure_owner_access
from app.services.session_paths import normalize_session_base


def ensure_session_alias_access(conn: Connection, session_id: str, request: Request) -> None:
    base = normalize_session_base(session_id)
    # SQL narrows the normal prefix aliases; Python normalization handles the
    # repeated-prefix and safe-name rules consistently with filesystem paths.
    candidates = conn.execute(
        "SELECT id, owner_id FROM chat_sessions WHERE id LIKE ?",
        (f"%{base}%",),
    ).fetchall()
    for row in candidates:
        if normalize_session_base(row["id"]) == base:
            ensure_owner_access(request, row["owner_id"], detail="session owner mismatch")
