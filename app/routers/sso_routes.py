"""SSO (Single Sign-On) routes for integration with main platform."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.routers import register_router
from app.services.auth import (
    auth_cookie_name,
    create_auth_session,
    set_session_cookie,
)
from app.services.sso import (
    SSOUserData,
    get_user_by_global_uuid,
    sync_sso_user,
    verify_sso_token,
)

router = APIRouter(prefix="/sso", tags=["sso"])


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


@router.get("/login/")
def sso_login(
    request: Request,
    response: Response,
    token: str = Query(..., description="SSO token from main platform"),
    redirect_url: Optional[str] = Query(None, description="URL to redirect after login"),
    project_id: Optional[int] = Query(None, description="Project ID from main platform"),
):
    """SSO login endpoint.
    
    Verifies token with main platform, creates/updates local user, and establishes session.
    """
    user_data = verify_sso_token(token)
    
    sso_user = SSOUserData(user_data)
    
    existing_user = get_user_by_global_uuid(sso_user.uuid)
    
    if not existing_user:
        sync_result = sync_sso_user(sso_user)
        if sync_result.get("code") not in {"CREATED", "UPDATED"}:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create SSO user: {sync_result.get('message')}"
            )
        existing_user = get_user_by_global_uuid(sso_user.uuid)
    
    if not existing_user:
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve user after SSO sync"
        )
    
    if not existing_user.get("is_active"):
        raise HTTPException(
            status_code=403,
            detail="User account is disabled"
        )
    
    session = create_auth_session(
        existing_user["id"],
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
    )
    
    request.state.skip_auth_cookie_refresh = True
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"], host=_request_host(request))
    
    frontend_base = redirect_url or "http://bioagent.byoryn.cn"
    separator = "&" if "?" in frontend_base else "?"
    final_redirect = f"{frontend_base}{separator}__sso_session={session['id']}"
    
    if project_id is not None:
        final_redirect += f"&project_id={project_id}"
    
    return RedirectResponse(url=final_redirect, status_code=302)


@router.post("/users/", response_model=SSOUserSyncResponse)
def sync_sso_user_endpoint(
    payload: SSOUserSyncRequest,
    request: Request,
):
    """User synchronization endpoint.
    
    Called by main platform to create, update, or delete users.
    """
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
