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
    create_auth_session,
    rate_limiter,
    register_user,
    revoke_auth_session,
    require_local_auth_enabled,
    set_session_cookie,
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


class AuthUserResponse(BaseModel):
    user_id: str
    email: str
    role: str
    auth_source: str


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


def _request_user_agent(request: Request) -> Optional[str]:
    raw = request.headers.get("user-agent")
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:512] if text else None


def _auth_success_response(user: Dict[str, Any], *, auth_source: str = "session") -> AuthSessionResponse:
    payload = build_user_payload(user)
    payload["auth_source"] = auth_source
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
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"])
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
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"])
    return _auth_success_response(user)


@router.post("/logout")
def logout_current_session(
    request: Request,
    response: Response,
):
    principal = get_request_principal(request)
    if principal.auth_source == "session":
        raw_session = request.cookies.get(auth_cookie_name())
        if raw_session:
            revoke_auth_session(raw_session)
    request.state.skip_auth_cookie_refresh = True
    clear_session_cookie(response)
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
        user=AuthUserResponse(
            user_id=principal.user_id,
            email=principal.email or "",
            role=principal.role,
            auth_source=principal.auth_source,
        ),
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
    set_session_cookie(response, session_id=session["id"], expires_at=session["expires_at"])
    return _auth_success_response(updated_user)
