"""SSO (Single Sign-On) service for integration with main platform."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from argon2 import PasswordHasher
from fastapi import HTTPException

from app.database_pool import get_db
from app.services.auth import normalize_email

logger = logging.getLogger(__name__)

_password_hasher = PasswordHasher()

SSO_VERIFY_URL = "http://119.147.24.196:3087/api/v1/sso/verify-token/"
SSO_API_KEY = "E9-U3-Or-TH9al3aB9twT5wBv6J541636jAh18PBm4IuVwsmtBoyhQ"
SSO_TIMEOUT_SECONDS = 10.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class SSOUserData:
    """SSO user data structure from main platform."""
    
    def __init__(self, data: Dict[str, Any]):
        self.global_uuid: str = data.get("global_uuid", "")
        self.action: str = data.get("action", "create")
        self.user: Dict[str, Any] = data.get("user", {})
        
    @property
    def uuid(self) -> str:
        return self.user.get("uuid", self.global_uuid)
    
    @property
    def name(self) -> str:
        return self.user.get("name", "")
    
    @property
    def username(self) -> str:
        return self.user.get("username", "")
    
    @property
    def email(self) -> str:
        return self.user.get("email", "")
    
    @property
    def password(self) -> Optional[str]:
        return self.user.get("password")
    
    @property
    def department(self) -> Optional[int]:
        return self.user.get("department")
    
    @property
    def department_code(self) -> str:
        return self.user.get("department_code", "")
    
    @property
    def department_display(self) -> str:
        return self.user.get("department_display", "")
    
    @property
    def profile(self) -> Dict[str, Any]:
        return self.user.get("profile", {})


def verify_sso_token(token: str) -> Dict[str, Any]:
    """Verify SSO token with main platform and return user data.
    
    Args:
        token: SSO token from main platform
        
    Returns:
        User data dictionary from main platform
        
    Raises:
        HTTPException: If token verification fails
    """
    try:
        with httpx.Client(timeout=SSO_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{SSO_VERIFY_URL}?token={token}",
                headers={"X-Api-Key": SSO_API_KEY}
            )
            
            if response.status_code != 200:
                logger.error(f"SSO token verification failed: {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=401,
                    detail=f"SSO token verification failed: {response.status_code}"
                )
            
            data = response.json()
            
            if data.get("code") != 0:
                logger.error(f"SSO token verification failed: {data.get('message')}")
                raise HTTPException(
                    status_code=401,
                    detail=f"SSO token verification failed: {data.get('message')}"
                )
            
            return data.get("data", {})
            
    except httpx.TimeoutException:
        logger.error("SSO token verification timeout")
        raise HTTPException(
            status_code=504,
            detail="SSO token verification timeout"
        )
    except httpx.RequestError as e:
        logger.error(f"SSO token verification request error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"SSO token verification request error: {str(e)}"
        )


def sync_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    """Synchronize SSO user data to local database.
    
    Args:
        sso_data: SSO user data from main platform
        
    Returns:
        Dictionary with sync result: {"code": "CREATED"|"UPDATED"|"SKIPPED", "message": "..."}
    """
    action = sso_data.action
    
    if action == "create":
        return _create_sso_user(sso_data)
    elif action == "update":
        return _update_sso_user(sso_data)
    elif action == "delete":
        return _delete_sso_user(sso_data)
    else:
        logger.warning(f"Unknown SSO action: {action}")
        return {"code": "SKIPPED", "message": f"Unknown action: {action}"}


def _create_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    """Create new SSO user in local database."""
    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE global_uuid = ? OR email = ?",
                (sso_data.uuid, normalize_email(sso_data.email))
            ).fetchone()
            
            if existing:
                logger.info(f"SSO user already exists: {sso_data.uuid}")
                return {"code": "SKIPPED", "message": "User already exists"}
            
            user_id = str(uuid4())
            
            password_hash = ""
            if sso_data.password:
                password_hash = _password_hasher.hash(sso_data.password)
            else:
                password_hash = _password_hasher.hash(str(uuid4()))
            
            import json
            profile_json = json.dumps(sso_data.profile, ensure_ascii=False)
            
            conn.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, role, is_active,
                    global_uuid, name, username, department,
                    department_code, department_display, profile,
                    sso_enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalize_email(sso_data.email),
                    password_hash,
                    "user",
                    1,
                    sso_data.uuid,
                    sso_data.name,
                    sso_data.username,
                    sso_data.department,
                    sso_data.department_code,
                    sso_data.department_display,
                    profile_json,
                    1,
                    _serialize_timestamp(_now_utc())
                )
            )
            
            logger.info(f"Created SSO user: {sso_data.uuid} ({sso_data.email})")
            return {"code": "CREATED", "message": "User created successfully"}
            
    except Exception as e:
        logger.error(f"Failed to create SSO user: {e}")
        return {"code": "INTERNAL_ERROR", "message": str(e)}


def _update_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    """Update existing SSO user in local database."""
    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT id FROM users WHERE global_uuid = ?",
                (sso_data.uuid,)
            ).fetchone()
            
            if not user:
                logger.warning(f"SSO user not found for update: {sso_data.uuid}")
                return {"code": "SKIPPED", "message": "User not found"}
            
            user_id = user["id"]
            
            update_fields = []
            update_values = []
            
            if sso_data.email:
                update_fields.append("email = ?")
                update_values.append(normalize_email(sso_data.email))
            
            if sso_data.name:
                update_fields.append("name = ?")
                update_values.append(sso_data.name)
            
            if sso_data.username:
                update_fields.append("username = ?")
                update_values.append(sso_data.username)
            
            if sso_data.department is not None:
                update_fields.append("department = ?")
                update_values.append(sso_data.department)
            
            if sso_data.department_code:
                update_fields.append("department_code = ?")
                update_values.append(sso_data.department_code)
            
            if sso_data.department_display:
                update_fields.append("department_display = ?")
                update_values.append(sso_data.department_display)
            
            if sso_data.profile:
                import json
                update_fields.append("profile = ?")
                update_values.append(json.dumps(sso_data.profile, ensure_ascii=False))
            
            if sso_data.password:
                password_hash = _password_hasher.hash(sso_data.password)
                update_fields.append("password_hash = ?")
                update_values.append(password_hash)
            
            if not update_fields:
                logger.info(f"No fields to update for SSO user: {sso_data.uuid}")
                return {"code": "SKIPPED", "message": "No fields to update"}
            
            update_values.append(user_id)
            
            update_sql = f"UPDATE users SET {', '.join(update_fields)} WHERE id = ?"
            conn.execute(update_sql, update_values)
            
            logger.info(f"Updated SSO user: {sso_data.uuid}")
            return {"code": "UPDATED", "message": "User updated successfully"}
            
    except Exception as e:
        logger.error(f"Failed to update SSO user: {e}")
        return {"code": "INTERNAL_ERROR", "message": str(e)}


def _delete_sso_user(sso_data: SSOUserData) -> Dict[str, Any]:
    """Delete SSO user from local database."""
    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT id FROM users WHERE global_uuid = ?",
                (sso_data.uuid,)
            ).fetchone()
            
            if not user:
                logger.warning(f"SSO user not found for deletion: {sso_data.uuid}")
                return {"code": "SKIPPED", "message": "User not found"}
            
            user_id = user["id"]
            
            conn.execute(
                "UPDATE users SET is_active = 0 WHERE id = ?",
                (user_id,)
            )
            
            logger.info(f"Deleted SSO user: {sso_data.uuid}")
            return {"code": "UPDATED", "message": "User deleted successfully"}
            
    except Exception as e:
        logger.error(f"Failed to delete SSO user: {e}")
        return {"code": "INTERNAL_ERROR", "message": str(e)}


def get_user_by_global_uuid(global_uuid: str) -> Optional[Dict[str, Any]]:
    """Get user by global_uuid from main platform."""
    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE global_uuid = ? AND is_active = 1",
                (global_uuid,)
            ).fetchone()
            
            if user:
                return dict(user)
            return None
    except Exception as e:
        logger.error(f"Failed to get user by global_uuid: {e}")
        return None
