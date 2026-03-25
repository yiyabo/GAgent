from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from typing import Any, Dict, Optional
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import HTTPException, Response

from app.database_pool import get_db
from app.services.foundation.settings import get_settings
from app.services.request_principal import RequestPrincipal
from app.utils.route_helpers import parse_bool

AUTH_MODE_LOCAL = "local"
AUTH_MODE_PROXY = "proxy"
AUTH_MODE_HYBRID = "hybrid"
AUTH_ROLE_ADMIN = "admin"
AUTH_ROLE_USER = "user"

_password_hasher = PasswordHasher()

try:  # argon2-cffi compatibility across minor versions
    from argon2.exceptions import InvalidHashError
except ImportError:  # pragma: no cover - older argon2-cffi
    InvalidHashError = VerificationError


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_email(raw_email: str) -> str:
    return str(raw_email or "").strip().lower()


def get_auth_mode() -> str:
    raw = str(get_settings().auth_mode or AUTH_MODE_LOCAL).strip().lower()
    if raw not in {AUTH_MODE_LOCAL, AUTH_MODE_PROXY, AUTH_MODE_HYBRID}:
        return AUTH_MODE_LOCAL
    return raw


def proxy_auth_required() -> bool:
    configured = os.getenv("PROXY_AUTH_REQUIRED")
    if configured is not None:
        return parse_bool(configured, default=False)

    app_env = str(
        os.getenv("APP_ENV")
        or os.getenv("ENV")
        or getattr(get_settings(), "app_env", "")
        or ""
    ).strip().lower()
    return app_env in {"prod", "production"}


def legacy_proxy_access_allowed(principal: RequestPrincipal, *, mode: Optional[str] = None) -> bool:
    resolved_mode = str(mode or get_auth_mode()).strip().lower()
    return (
        resolved_mode == AUTH_MODE_PROXY
        and principal.auth_source == "fallback"
        and not proxy_auth_required()
    )


def local_auth_enabled() -> bool:
    return get_auth_mode() in {AUTH_MODE_LOCAL, AUTH_MODE_HYBRID}


def signup_enabled() -> bool:
    return bool(get_settings().auth_open_signup)


def auth_cookie_name() -> str:
    raw = str(get_settings().auth_cookie_name or "ga_session").strip()
    return raw or "ga_session"


def auth_session_ttl_hours() -> int:
    raw = int(get_settings().auth_session_ttl_hours or 168)
    return raw if raw > 0 else 168


def auth_cookie_secure() -> bool:
    app_env = str(
        os.getenv("APP_ENV")
        or os.getenv("ENV")
        or getattr(get_settings(), "app_env", "")
        or ""
    ).strip().lower()
    return app_env in {"prod", "production"}


def require_local_auth_enabled() -> None:
    if local_auth_enabled():
        return
    raise HTTPException(status_code=409, detail="Local authentication is disabled.")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return bool(_password_hasher.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def build_principal_from_user(user_row: Dict[str, Any], *, auth_source: str) -> RequestPrincipal:
    return RequestPrincipal(
        user_id=str(user_row["id"]),
        email=str(user_row["email"]),
        role=str(user_row.get("role") or AUTH_ROLE_USER),
        auth_source=auth_source,
        is_authenticated=True,
    )


def set_session_cookie(
    response: Response,
    *,
    session_id: str,
    expires_at: datetime,
) -> None:
    max_age = max(0, int((expires_at - _now_utc()).total_seconds()))
    response.set_cookie(
        key=auth_cookie_name(),
        value=session_id,
        max_age=max_age,
        expires=expires_at,
        httponly=True,
        samesite="lax",
        secure=auth_cookie_secure(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=auth_cookie_name(),
        httponly=True,
        samesite="lax",
        secure=auth_cookie_secure(),
        path="/",
    )


def _session_expiry() -> datetime:
    return _now_utc() + timedelta(hours=auth_session_ttl_hours())


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_email(email)
    if not normalized:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, email, password_hash, role, is_active, created_at, last_login_at
            FROM users
            WHERE email=?
            """,
            (normalized,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, email, password_hash, role, is_active, created_at, last_login_at
            FROM users
            WHERE id=?
            """,
            (str(user_id),),
        ).fetchone()
    return dict(row) if row else None


def register_user(email: str, password: str) -> Dict[str, Any]:
    normalized_email = normalize_email(email)
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required.")
    if len(password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if not signup_enabled():
        raise HTTPException(status_code=403, detail="Open signup is disabled.")

    password_hash = hash_password(password)
    user_id = uuid4().hex
    with get_db() as conn:
        row = None
        try:
            # Serialize bootstrap registration so exactly one first user
            # observes the empty table and becomes admin.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT COUNT(1) AS total FROM users").fetchone()
            total_users = int(existing["total"]) if existing else 0
            role = AUTH_ROLE_ADMIN if total_users == 0 else AUTH_ROLE_USER
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (user_id, normalized_email, password_hash, role),
            )
            if role == AUTH_ROLE_ADMIN:
                _claim_legacy_resources(conn, user_id)

            row = conn.execute(
                """
                SELECT id, email, role, is_active, created_at, last_login_at
                FROM users
                WHERE id=?
                """,
                (user_id,),
            ).fetchone()
            conn.commit()
        except Exception as exc:
            conn.rollback()
            message = str(exc).lower()
            if "unique" in message or "constraint" in message:
                raise HTTPException(status_code=409, detail="Email is already registered.") from exc
            raise
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create user.")
    return dict(row)


def authenticate_user(email: str, password: str) -> Dict[str, Any]:
    user = get_user_by_email(email)
    if user is None or not bool(user.get("is_active")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not verify_password(str(user["password_hash"]), password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",
            (user["id"],),
        )
    refreshed = get_user_by_id(str(user["id"]))
    if refreshed is None:
        raise HTTPException(status_code=500, detail="User no longer exists.")
    return refreshed


def create_auth_session(
    user_id: str,
    *,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    session_id = token_urlsafe(32)
    expires_at = _session_expiry()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions (
                id, user_id, expires_at, created_at, last_seen_at, ip, user_agent
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
            """,
            (session_id, str(user_id), _serialize_timestamp(expires_at), ip, user_agent),
        )
    return {"id": session_id, "expires_at": expires_at}


def get_auth_session(
    session_id: str,
    *,
    touch: bool = False,
) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.user_id, s.expires_at, u.email, u.role, u.is_active
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.id=?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        payload = dict(row)
        expires_at = _parse_timestamp(payload.get("expires_at"))
        if expires_at is None or expires_at <= _now_utc() or not bool(payload.get("is_active")):
            conn.execute("DELETE FROM auth_sessions WHERE id=?", (session_id,))
            return None
        if touch:
            refreshed_expiry = _session_expiry()
            conn.execute(
                """
                UPDATE auth_sessions
                SET expires_at=?, last_seen_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (_serialize_timestamp(refreshed_expiry), session_id),
            )
            payload["expires_at"] = _serialize_timestamp(refreshed_expiry)
    return payload


def revoke_auth_session(session_id: str) -> None:
    if not session_id:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE id=?", (session_id,))


def revoke_user_sessions(user_id: str) -> None:
    if not user_id:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (str(user_id),))


def change_password(user_id: str, current_password: str, new_password: str) -> Dict[str, Any]:
    if len(new_password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    user = get_user_by_id(user_id)
    if user is None or not bool(user.get("is_active")):
        raise HTTPException(status_code=404, detail="User not found.")
    if not verify_password(str(user["password_hash"]), current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(new_password), str(user_id)),
        )
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (str(user_id),))
        row = conn.execute(
            """
            SELECT id, email, role, is_active, created_at, last_login_at
            FROM users
            WHERE id=?
            """,
            (str(user_id),),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to update password.")
    return dict(row)


def session_principal_from_session_id(session_id: str, *, touch: bool = False) -> Optional[tuple[RequestPrincipal, datetime]]:
    session = get_auth_session(session_id, touch=touch)
    if session is None:
        return None
    expires_at = _parse_timestamp(session.get("expires_at")) or _session_expiry()
    principal = RequestPrincipal(
        user_id=str(session["user_id"]),
        email=str(session["email"]),
        role=str(session.get("role") or AUTH_ROLE_USER),
        auth_source="session",
        is_authenticated=True,
    )
    return principal, expires_at


def build_user_payload(user_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": str(user_row["id"]),
        "email": str(user_row["email"]),
        "role": str(user_row.get("role") or AUTH_ROLE_USER),
    }


class FixedWindowRateLimiter:
    def __init__(self) -> None:
        self._buckets: Dict[tuple[str, str], Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, identifier: str, *, limit: int, window_seconds: int) -> None:
        if not identifier:
            identifier = "anonymous"
        key = (bucket, identifier)
        now = _now_utc().timestamp()
        with self._lock:
            state = self._buckets.get(key)
            if state is None or now >= state["window_start"] + window_seconds:
                state = {"window_start": now, "count": 0}
                self._buckets[key] = state
            state["count"] += 1
            if state["count"] > limit:
                retry_after = int(max(1, state["window_start"] + window_seconds - now))
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please retry later.",
                    headers={"Retry-After": str(retry_after)},
                )


rate_limiter = FixedWindowRateLimiter()


def _claim_legacy_resources(conn, user_id: str) -> None:
    owner = str(user_id)
    conn.execute(
        """
        UPDATE chat_sessions
        SET owner_id=?
        WHERE owner_id IS NULL OR TRIM(owner_id) = '' OR owner_id = 'legacy-local'
        """,
        (owner,),
    )
    conn.execute(
        """
        UPDATE chat_action_runs
        SET owner_id=?
        WHERE owner_id IS NULL OR TRIM(owner_id) = '' OR owner_id = 'legacy-local'
        """,
        (owner,),
    )
    conn.execute(
        """
        UPDATE chat_runs
        SET owner_id=?
        WHERE owner_id IS NULL OR TRIM(owner_id) = '' OR owner_id = 'legacy-local'
        """,
        (owner,),
    )
    conn.execute(
        """
        UPDATE plan_decomposition_job_index
        SET owner_id=?
        WHERE owner_id IS NULL OR TRIM(owner_id) = '' OR owner_id = 'legacy-local'
        """,
        (owner,),
    )
    conn.execute(
        """
        UPDATE plans
        SET owner=?
        WHERE owner IS NULL OR TRIM(owner) = '' OR owner = 'legacy-local'
        """,
        (owner,),
    )
