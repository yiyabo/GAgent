"""SQLite persistence for PhageScope MCP tokens, daily usage and audit rows.

Only a SHA-256 digest of each token is stored; the plaintext is shown once at
issue time. Tokens carry 256 bits of randomness, so an unsalted digest is a
sufficient lookup key.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database_pool import get_db

TOKEN_PREFIX = "gpsk_"
SCOPE_DATA_READ = "data:read"

_schema_lock = threading.Lock()
_schema_ready = False

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS phagescope_mcp_tokens (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        token_hint TEXT NOT NULL,
        scopes TEXT NOT NULL,
        created_by TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        revoked_at TEXT,
        last_used_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_phagescope_mcp_tokens_user ON phagescope_mcp_tokens(user_id)",
    """
    CREATE TABLE IF NOT EXISTS phagescope_mcp_usage (
        token_id TEXT NOT NULL,
        day TEXT NOT NULL,
        bytes INTEGER NOT NULL DEFAULT 0,
        calls INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (token_id, day)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS phagescope_mcp_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        arguments_digest TEXT NOT NULL,
        arguments_summary TEXT NOT NULL,
        status TEXT NOT NULL,
        error_code TEXT,
        duration_ms INTEGER NOT NULL,
        result_rows INTEGER,
        result_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_phagescope_mcp_audit_token ON phagescope_mcp_audit(token_id, created_at)",
)


@dataclass(frozen=True)
class McpToken:
    id: str
    user_id: str
    name: str
    token_hint: str
    scopes: tuple[str, ...]
    created_at: str
    expires_at: Optional[str]
    revoked_at: Optional[str]
    last_used_at: Optional[str]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "token_hint": self.token_hint,
            "scopes": list(self.scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_used_at": self.last_used_at,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def utc_day(value: Optional[datetime] = None) -> str:
    return (value or _now()).strftime("%Y-%m-%d")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with get_db() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.commit()
        _schema_ready = True


def _row_to_token(row: Any) -> McpToken:
    return McpToken(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        token_hint=row["token_hint"],
        scopes=tuple(json.loads(row["scopes"] or "[]")),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
    )


def issue_token(
    user_id: str,
    *,
    name: str,
    created_by: Optional[str] = None,
    expires_in_days: Optional[int] = 90,
    scopes: tuple[str, ...] = (SCOPE_DATA_READ,),
) -> tuple[str, McpToken]:
    """Create a token and return ``(plaintext, record)``; plaintext is never stored."""
    ensure_schema()
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    token_id = "mcpt_" + uuid.uuid4().hex[:16]
    expires_at = _iso(now + timedelta(days=expires_in_days)) if expires_in_days else None
    hint = plaintext[:9] + "…" + plaintext[-4:]
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO phagescope_mcp_tokens
                (id, user_id, name, token_hash, token_hint, scopes, created_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_id,
                user_id,
                (name or "default")[:80],
                hash_token(plaintext),
                hint,
                json.dumps(list(scopes)),
                created_by,
                _iso(now),
                expires_at,
            ),
        )
        conn.commit()
    record = get_token(token_id)
    assert record is not None
    return plaintext, record


def get_token(token_id: str) -> Optional[McpToken]:
    ensure_schema()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM phagescope_mcp_tokens WHERE id = ?", (token_id,)).fetchone()
    return _row_to_token(row) if row else None


def list_tokens(user_id: Optional[str] = None) -> list[McpToken]:
    ensure_schema()
    with get_db() as conn:
        if user_id is None:
            rows = conn.execute("SELECT * FROM phagescope_mcp_tokens ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM phagescope_mcp_tokens WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
    return [_row_to_token(r) for r in rows]


def revoke_token(token_id: str, *, user_id: Optional[str] = None) -> bool:
    """Revoke a token; when ``user_id`` is given it must own the token."""
    ensure_schema()
    with get_db() as conn:
        sql = "UPDATE phagescope_mcp_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL"
        params: list[Any] = [_iso(_now()), token_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0


def authenticate(plaintext: str) -> Optional[McpToken]:
    """Return the active token for ``plaintext`` or ``None``."""
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX) or len(plaintext) > 200:
        return None
    ensure_schema()
    digest = hash_token(plaintext)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM phagescope_mcp_tokens WHERE token_hash = ?", (digest,)
        ).fetchone()
        if not row or row["revoked_at"]:
            return None
        if row["expires_at"] and row["expires_at"] <= _iso(_now()):
            return None
        conn.execute(
            "UPDATE phagescope_mcp_tokens SET last_used_at = ? WHERE id = ?",
            (_iso(_now()), row["id"]),
        )
        conn.commit()
    return _row_to_token(row)


def bytes_used_today(token_id: str) -> int:
    ensure_schema()
    with get_db() as conn:
        row = conn.execute(
            "SELECT bytes FROM phagescope_mcp_usage WHERE token_id = ? AND day = ?",
            (token_id, utc_day()),
        ).fetchone()
    return int(row["bytes"]) if row else 0


def record_usage(token_id: str, nbytes: int) -> int:
    """Add ``nbytes`` to today's counter and return the new total."""
    ensure_schema()
    day = utc_day()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO phagescope_mcp_usage (token_id, day, bytes, calls) VALUES (?, ?, ?, 1)
            ON CONFLICT(token_id, day) DO UPDATE SET bytes = bytes + excluded.bytes, calls = calls + 1
            """,
            (token_id, day, int(nbytes)),
        )
        row = conn.execute(
            "SELECT bytes FROM phagescope_mcp_usage WHERE token_id = ? AND day = ?", (token_id, day)
        ).fetchone()
        conn.commit()
    return int(row["bytes"]) if row else int(nbytes)


def summarize_arguments(arguments: dict[str, Any]) -> tuple[str, str]:
    """Return ``(digest, summary)``: a stable hash and a short key=value preview."""
    canonical = json.dumps(arguments or {}, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    parts = []
    for key in sorted(arguments or {}):
        value = json.dumps(arguments[key], default=str, ensure_ascii=False)
        parts.append(f"{key}={value[:60]}")
    return digest, "; ".join(parts)[:400]


def record_audit(
    *,
    token: McpToken,
    tool: str,
    arguments: dict[str, Any],
    status: str,
    duration_ms: int,
    result_bytes: int,
    result_rows: Optional[int] = None,
    error_code: Optional[str] = None,
) -> None:
    ensure_schema()
    digest, summary = summarize_arguments(arguments)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO phagescope_mcp_audit
                (token_id, user_id, tool, arguments_digest, arguments_summary, status, error_code,
                 duration_ms, result_rows, result_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token.id,
                token.user_id,
                tool,
                digest,
                summary,
                status,
                error_code,
                int(duration_ms),
                result_rows,
                int(result_bytes),
                _iso(_now()),
            ),
        )
        conn.commit()


def recent_audit(token_id: str, limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM phagescope_mcp_audit WHERE token_id = ? ORDER BY id DESC LIMIT ?",
            (token_id, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]
