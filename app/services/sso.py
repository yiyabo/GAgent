"""SSO user synchronization and compatibility wrappers for main-platform integration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from argon2 import PasswordHasher

from app.database_pool import get_db
from app.services.auth import normalize_email
from app.services.platform_api import get_platform_api_client

logger = logging.getLogger(__name__)
_password_hasher = PasswordHasher()


async def get_project_context(user_id: Optional[int], project_id: int) -> Dict[str, Any]:
    """Compatibility wrapper requiring an authenticated main-platform user."""
    if user_id is None:
        raise ValueError("Platform project access requires a platform user ID")
    return await get_platform_api_client().get_project_context(int(user_id), int(project_id))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class SSOUserData:
    """Normalized user payload verified by the main platform."""

    def __init__(self, data: Dict[str, Any]):
        self._root: Dict[str, Any] = data if isinstance(data, dict) else {}
        self.global_uuid: str = str(data.get("global_uuid") or "")
        self.action: str = str(data.get("action") or "create")
        raw_user = data.get("user")
        if isinstance(raw_user, dict):
            self.user = raw_user
        elif any(k in data for k in ("id", "user_id", "email", "uuid", "username")):
            self.user = dict(data)
        else:
            self.user = {}

    @property
    def uuid(self) -> str:
        return str(self.user.get("uuid") or self.global_uuid or "")

    @property
    def name(self) -> str:
        return str(self.user.get("name") or "")

    @property
    def username(self) -> str:
        return str(self.user.get("username") or "")

    @property
    def email(self) -> str:
        return str(self.user.get("email") or "")

    @property
    def password(self) -> Optional[str]:
        raw = self.user.get("password")
        return str(raw) if raw else None

    @property
    def department(self) -> Optional[int]:
        raw = self.user.get("department")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def department_code(self) -> str:
        return str(self.user.get("department_code") or "")

    @property
    def department_display(self) -> str:
        return str(self.user.get("department_display") or "")

    @property
    def profile(self) -> Dict[str, Any]:
        raw = self.user.get("profile")
        return raw if isinstance(raw, dict) else {}

    @property
    def main_platform_user_id(self) -> Optional[int]:
        candidates = (
            self.user.get("id"),
            self.user.get("user_id"),
            self.user.get("userId"),
            getattr(self, "_root", {}).get("id") if isinstance(getattr(self, "_root", None), dict) else None,
            getattr(self, "_root", {}).get("user_id") if isinstance(getattr(self, "_root", None), dict) else None,
        )
        for raw in candidates:
            if raw is None or raw == "":
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
        return None


async def verify_sso_token(token: str) -> Dict[str, Any]:
    return await get_platform_api_client().verify_sso_token(token)


def sync_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    action = sso_data.action
    if action == "create":
        return _create_sso_user(sso_data)
    if action == "update":
        return _update_sso_user(sso_data)
    if action == "delete":
        return _delete_sso_user(sso_data)
    return {"code": "SKIPPED", "message": f"Unknown SSO action: {action}"}


def _create_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    if not sso_data.uuid or not normalize_email(sso_data.email):
        return {"code": "INVALID_REQUEST", "message": "SSO user requires UUID and email"}
    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE global_uuid = ? OR email = ?",
                (sso_data.uuid, normalize_email(sso_data.email)),
            ).fetchone()
            if existing:
                return {"code": "SKIPPED", "message": "User already exists"}
            user_id = str(uuid4())
            password_hash = _password_hasher.hash(sso_data.password or str(uuid4()))
            conn.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, role, is_active, global_uuid, name,
                    username, department, department_code, department_display, profile,
                    sso_enabled, main_platform_user_id, created_at
                ) VALUES (?, ?, ?, 'user', 1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    user_id,
                    normalize_email(sso_data.email),
                    password_hash,
                    sso_data.uuid,
                    sso_data.name,
                    sso_data.username,
                    sso_data.department,
                    sso_data.department_code,
                    sso_data.department_display,
                    json.dumps(sso_data.profile, ensure_ascii=False),
                    sso_data.main_platform_user_id,
                    _serialize_timestamp(_now_utc()),
                ),
            )
        return {"code": "CREATED", "message": "User created successfully"}
    except Exception:
        logger.exception("Failed to create SSO user")
        return {"code": "INTERNAL_ERROR", "message": "Failed to create SSO user"}


def _update_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    try:
        with get_db() as conn:
            row = conn.execute("SELECT id FROM users WHERE global_uuid = ?", (sso_data.uuid,)).fetchone()
            if not row:
                return {"code": "SKIPPED", "message": "User not found"}
            conn.execute(
                """
                UPDATE users SET
                    email=?, name=?, username=?, department=?, department_code=?,
                    department_display=?, profile=?, main_platform_user_id=?, sso_enabled=1
                WHERE id=?
                """,
                (
                    normalize_email(sso_data.email),
                    sso_data.name,
                    sso_data.username,
                    sso_data.department,
                    sso_data.department_code,
                    sso_data.department_display,
                    json.dumps(sso_data.profile, ensure_ascii=False),
                    sso_data.main_platform_user_id,
                    row["id"],
                ),
            )
        return {"code": "UPDATED", "message": "User updated successfully"}
    except Exception:
        logger.exception("Failed to update SSO user")
        return {"code": "INTERNAL_ERROR", "message": "Failed to update SSO user"}


def _delete_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    try:
        with get_db() as conn:
            cursor = conn.execute("UPDATE users SET is_active=0 WHERE global_uuid=?", (sso_data.uuid,))
        if cursor.rowcount:
            return {"code": "UPDATED", "message": "User disabled successfully"}
        return {"code": "SKIPPED", "message": "User not found"}
    except Exception:
        logger.exception("Failed to disable SSO user")
        return {"code": "INTERNAL_ERROR", "message": "Failed to disable SSO user"}


def get_user_by_global_uuid(global_uuid: str) -> Optional[Dict[str, Any]]:
    if not global_uuid:
        return None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE global_uuid=? AND is_active=1", (str(global_uuid),)
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.exception("Failed to load SSO user")
        return None


def get_main_platform_user_id(local_user_id: str) -> Optional[int]:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT main_platform_user_id FROM users WHERE id=?", (str(local_user_id),)
            ).fetchone()
        if not row or row["main_platform_user_id"] is None:
            return None
        return int(row["main_platform_user_id"])
    except (TypeError, ValueError):
        return None
    except Exception:
        logger.exception("Failed to load main platform user ID")
        return None


def backfill_main_platform_user_id(local_user_id: str, main_platform_user_id: int) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE users
                SET main_platform_user_id=?
                WHERE id=? AND main_platform_user_id IS NULL
                """,
                (int(main_platform_user_id), str(local_user_id)),
            )
        return cursor.rowcount == 1
    except Exception:
        logger.exception("Failed to backfill main platform user ID")
        return False
