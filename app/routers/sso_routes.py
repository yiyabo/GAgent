"""SSO (Single Sign-On) routes for integration with main platform."""

from __future__ import annotations

import hmac
import logging
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.routers import register_router
from app.services.auth import (
    create_auth_session,
    create_sso_handoff,
    rate_limiter,
)
from app.services.foundation.settings import get_settings
from app.services.platform_api import get_platform_api_client
from app.services.sso import (
    SSOUserData,
    get_user_by_global_uuid,
    sync_sso_user,
    verify_sso_token,
)

router = APIRouter(prefix="/sso", tags=["sso"])

logger = logging.getLogger(__name__)


register_router(
    namespace="sso",
    version="v1",
    path="/sso",
    router=router,
    tags=["sso"],
    allow_anonymous=True,
    description="Single Sign-On integration with main platform",
)


class SSOUserSyncRequest(BaseModel):
    global_uuid: str = Field(..., description="Global UUID from main platform")
    action: str = Field(..., description="Action type: create, update, or delete")
    user: Dict[str, Any] = Field(..., description="User data from main platform")


class SSOUserSyncResponse(BaseModel):
    code: str = Field(..., description="Result code: CREATED, UPDATED, SKIPPED, INVALID_REQUEST, INTERNAL_ERROR")
    message: Optional[str] = Field(None, description="Result message")


def _request_ip(request: Request) -> str:
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def _request_host(request: Request) -> Optional[str]:
    return request.headers.get("host")


def _request_user_agent(request: Request) -> Optional[str]:
    raw = request.headers.get("user-agent")
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:512] if text else None


def _allowed_frontend_redirect(redirect_url: Optional[str]) -> str:
    configured = [
        item.strip().rstrip("/").lower()
        for item in str(get_settings().sso_allowed_redirect_origins or "").split(",")
        if item.strip()
    ]
    if not configured:
        raise HTTPException(status_code=503, detail="SSO redirect origins are not configured")
    candidate = str(redirect_url or configured[0]).strip()
    parsed = urlparse(candidate)
    origin = f"{parsed.scheme}://{parsed.netloc.lower()}".rstrip("/")
    if parsed.scheme not in {"http", "https"} or origin not in configured:
        raise HTTPException(status_code=400, detail="SSO redirect URL is not allowed")
    return candidate


@router.get("/login/")
async def sso_login(
    request: Request,
    response: Response,
    token: str = Query(..., description="SSO token from main platform"),
    redirect_url: Optional[str] = Query(None, description="URL to redirect after login"),
    project_id: Optional[int] = Query(None, description="Project ID from main platform"),
    user_id: Optional[int] = Query(None, description="Main platform user ID"),
    project_label: Optional[str] = Query(None, description="Project label from main platform"),
):
    """Verify platform SSO, bind one authorized project, then issue a handoff."""
    rate_limiter.check("sso_login", _request_ip(request), limit=get_settings().rate_limit_sso_login, window_seconds=60)
    frontend_base = _allowed_frontend_redirect(redirect_url)
    if project_id is None:
        raise HTTPException(status_code=400, detail="Platform SSO requires a project binding")

    user_data = await verify_sso_token(token)
    if not isinstance(user_data, dict):
        user_data = {}
    sso_user = SSOUserData(user_data)
    resolved_user_id = sso_user.main_platform_user_id
    if resolved_user_id is None and user_id is not None:
        try:
            resolved_user_id = int(user_id)
        except (TypeError, ValueError):
            resolved_user_id = None
        if resolved_user_id is not None:
            user_bucket = user_data.get("user")
            if not isinstance(user_bucket, dict):
                user_bucket = {}
                user_data["user"] = user_bucket
            user_bucket.setdefault("id", resolved_user_id)
            sso_user = SSOUserData(user_data)
            logger.info(
                "[SSO] Platform verify omitted user.id; using URL user_id=%s after token verify",
                resolved_user_id,
            )
    if resolved_user_id is None:
        logger.error(
            "[SSO] Platform verify response missing user id; data_keys=%s user_keys=%s",
            sorted(str(k) for k in user_data.keys()),
            sorted(str(k) for k in (sso_user.user or {}).keys()),
        )
        raise HTTPException(status_code=502, detail="Platform SSO verification omitted the user ID")
    if user_id is not None and int(user_id) != resolved_user_id:
        logger.warning(
            "[SSO] User binding mismatch: URL user_id=%s vs token user_id=%s",
            user_id, resolved_user_id,
        )
        raise HTTPException(status_code=403, detail="SSO user binding mismatch")

    existing_user = get_user_by_global_uuid(sso_user.uuid)
    if not existing_user:
        sync_result = sync_sso_user(sso_user)
        if sync_result.get("code") not in {"CREATED", "UPDATED"}:
            raise HTTPException(status_code=500, detail="Failed to synchronize SSO user")
        existing_user = get_user_by_global_uuid(sso_user.uuid)
    if not existing_user:
        raise HTTPException(status_code=500, detail="Failed to retrieve synchronized SSO user")
    if not existing_user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account is disabled")

    project_data = await get_platform_api_client().get_project_context(resolved_user_id, project_id)
    raw_project_id = project_data.get("id")
    if raw_project_id is None:
        raise HTTPException(
            status_code=502,
            detail="Platform project context omitted the project ID",
        )
    validated_project_id = int(raw_project_id)
    validated_project_label = str(project_data.get("label") or "").strip() or None

    session = create_auth_session(
        existing_user["id"],
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
        access_mode="platform",
        platform_user_id=resolved_user_id,
        platform_project_id=validated_project_id,
        platform_project_label=validated_project_label,
    )
    handoff = create_sso_handoff(
        session["id"],
        ttl_seconds=get_settings().sso_handoff_ttl_seconds,
    )
    separator = "&" if "?" in frontend_base else "?"
    final_redirect = f"{frontend_base}{separator}__sso_handoff={quote(handoff)}"
    return RedirectResponse(url=final_redirect, status_code=302)


@router.post("/users/", response_model=SSOUserSyncResponse)
def sync_sso_user_endpoint(
    payload: SSOUserSyncRequest,
    request: Request,
):
    """User synchronization endpoint.

    Called by main platform to create, update, or delete users. Requires a
    shared service key via the X-Sso-Sync-Key header when configured.
    """
    expected_key = get_settings().sso_user_sync_api_key
    if not expected_key:
        # Security: fail closed — endpoint disabled unless SSO_USER_SYNC_API_KEY is set.
        raise HTTPException(status_code=503, detail="SSO user sync is not configured")
    # 平台侧实际以 X-Api-Key 头发送（ms sso_sync.py），X-Sso-Sync-Key 为本文档约定头，两者都认
    provided = request.headers.get("X-Sso-Sync-Key", "") or request.headers.get("X-Api-Key", "")
    if not provided or not hmac.compare_digest(provided, expected_key):
        logger.warning("[SSO] sync key rejected: provided_len=%d prefix=%r", len(provided), provided[:4])
        raise HTTPException(status_code=401, detail="Missing or invalid SSO sync key")
    sso_data = SSOUserData({
        "global_uuid": payload.global_uuid,
        "action": payload.action,
        "user": payload.user,
    })
    result = sync_sso_user(sso_data)
    return SSOUserSyncResponse(
        code=result.get("code", "INTERNAL_ERROR"),
        message=result.get("message"),
    )
