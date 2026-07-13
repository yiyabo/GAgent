from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.routers import register_router
from app.services.auth import (
    authenticate_user,
    auth_cookie_name,
    build_user_payload,
    change_password,
    legacy_proxy_access_allowed,
    clear_session_cookie,
    consume_sso_handoff,
    create_auth_session,
    rate_limiter,
    register_user,
    revoke_auth_session,
    require_local_auth_enabled,
    set_session_cookie,
    session_principal_from_session_id,
)
from app.services.request_principal import get_request_principal, require_authenticated_principal

router = APIRouter(prefix="/auth", tags=["auth"])


register_router(
    namespace="auth",
    version="v1",
    path="/auth",
    router=router,
    tags=["auth"],
    allow_anonymous=True,
    description="Local account registration, login, session, and password management",
)


class PlatformContextResponse(BaseModel):
    user_id: int
    project_id: Optional[int] = None
    project_label: Optional[str] = None


class AuthUserResponse(BaseModel):
    user_id: str
    email: str
    role: str
    auth_source: str
    access_mode: str = "local"
    platform_context: Optional[PlatformContextResponse] = None


class AuthSessionResponse(BaseModel):
    authenticated: bool = True
    user: AuthUserResponse


class AuthMeResponse(BaseModel):
    authenticated: bool
    user: Optional[AuthUserResponse] = None
    legacy_access_allowed: bool = False


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


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


def _principal_user_response(principal) -> AuthUserResponse:
    platform_context = None
    if principal.is_platform_access:
        platform_context = PlatformContextResponse(
            user_id=principal.require_platform_user_id(),
            project_id=principal.platform_project_id,
            project_label=principal.platform_project_label,
        )
    return AuthUserResponse(
        user_id=principal.user_id,
        email=principal.email or "",
        role=principal.role,
        auth_source=principal.auth_source,
        access_mode=principal.access_mode,
        platform_context=platform_context,
    )


def _auth_success_response(user: Dict[str, Any], *, auth_source: str = "session") -> AuthSessionResponse:
    payload = build_user_payload(user)
    payload["auth_source"] = auth_source
    payload["access_mode"] = "local"
    return AuthSessionResponse(user=AuthUserResponse(**payload))


@router.post("/register", response_model=AuthSessionResponse)
def register_local_account(
    payload: RegisterRequest,
    request: Request,
    response: Response,
):
    require_local_auth_enabled()
    rate_limiter.check("register", _request_ip(request), limit=5, window_seconds=15 * 60)

    user = register_user(payload.email, payload.password)
    session = create_auth_session(
        user["id"],
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
    )
    request.state.skip_auth_cookie_refresh = True
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"], host=_request_host(request))
    return _auth_success_response(user)


@router.post("/login", response_model=AuthSessionResponse)
def login_local_account(
    payload: LoginRequest,
    request: Request,
    response: Response,
):
    require_local_auth_enabled()
    rate_limiter.check(
        "login",
        f"{_request_ip(request)}:{str(payload.email).strip().lower()}",
        limit=10,
        window_seconds=15 * 60,
    )

    user = authenticate_user(payload.email, payload.password)
    session = create_auth_session(
        user["id"],
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
    )
    request.state.skip_auth_cookie_refresh = True
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"], host=_request_host(request))
    return _auth_success_response(user)


@router.post("/logout")
def logout_current_session(
    request: Request,
    response: Response,
):
    principal = get_request_principal(request)
    raw_session = request.cookies.get(auth_cookie_name())
    if raw_session:
        revoke_auth_session(raw_session)
    request.state.skip_auth_cookie_refresh = True
    clear_session_cookie(response, host=_request_host(request))
    return {"success": True}


@router.get("/me", response_model=AuthMeResponse)
def get_current_auth_state(request: Request):
    principal = get_request_principal(request)
    if not principal.is_authenticated:
        return AuthMeResponse(
            authenticated=False,
            user=None,
            legacy_access_allowed=legacy_proxy_access_allowed(principal),
        )
    return AuthMeResponse(
        authenticated=True,
        user=_principal_user_response(principal),
        legacy_access_allowed=False,
    )


@router.post("/change-password", response_model=AuthSessionResponse)
def change_local_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest = Body(...),
):
    require_local_auth_enabled()
    principal = require_authenticated_principal(request)
    if principal.auth_source != "session":
        raise HTTPException(status_code=401, detail="Local password changes require a local session.")
    rate_limiter.check(
        "change-password",
        principal.user_id,
        limit=5,
        window_seconds=15 * 60,
    )
    updated_user = change_password(
        principal.user_id,
        payload.current_password,
        payload.new_password,
    )
    session = create_auth_session(
        principal.user_id,
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
    )
    request.state.skip_auth_cookie_refresh = True
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"], host=_request_host(request))
    return _auth_success_response(updated_user)


class SSOCompleteRequest(BaseModel):
    handoff_token: str = Field(..., min_length=1, max_length=512)


@router.post("/sso-complete", response_model=AuthSessionResponse)
def complete_sso_login(
    payload: SSOCompleteRequest,
    request: Request,
    response: Response,
):
    """Consume a single-use SSO handoff and set the real session cookie."""
    session_id = consume_sso_handoff(payload.handoff_token)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Invalid, expired, or already used SSO handoff")
    resolved = session_principal_from_session_id(session_id, touch=True)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Invalid or expired SSO session")
    principal, expires_at = resolved
    if not principal.is_authenticated or not principal.is_platform_access:
        raise HTTPException(status_code=401, detail="Session is not a platform SSO session")

    request.state.skip_auth_cookie_refresh = True
    set_session_cookie(response, session_id=session_id, expires_at=expires_at, host=_request_host(request))
    return AuthSessionResponse(authenticated=True, user=_principal_user_response(principal))
