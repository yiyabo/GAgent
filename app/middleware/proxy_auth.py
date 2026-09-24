from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.services.auth import (
    AUTH_MODE_HYBRID,
    AUTH_MODE_LOCAL,
    AUTH_MODE_PROXY,
    auth_cookie_name,
    get_auth_mode,
    legacy_proxy_access_allowed,
    proxy_auth_required,
    session_principal_from_session_id,
    set_session_cookie,
)
from app.services.request_principal import (
    RequestPrincipal,
    make_legacy_principal,
    reset_current_principal,
    set_current_principal,
)

logger = logging.getLogger("app.proxy_auth")


def _trim_header(value: Optional[str], *, limit: int = 256) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        return text[:limit]
    return text


def _is_anonymous_path(path: str) -> bool:
    normalized = str(path or "").strip() or "/"
    if normalized in {"/health", "/health/llm", "/openapi.json", "/docs", "/redoc"}:
        return True
    if normalized.startswith("/auth") or normalized.startswith("/sso"):
        return True
    if normalized.startswith("/project"):
        return True
    _API_PREFIXES = (
        "/api/",
        "/chat/",
        "/mcp/",
        "/plans/",
        "/jobs/",
        "/upload/",
        "/artifacts/",
        "/execution/",
        "/interpreter/",
        "/system/",
        "/quality/",
        "/terminal/",
        "/models/",
    )
    if any(normalized.startswith(p) for p in _API_PREFIXES):
        return False
    return True


class ProxyAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.mode = get_auth_mode()
        self.proxy_auth_required = proxy_auth_required()
        self.user_header = str(os.getenv("PROXY_AUTH_USER_HEADER", "X-Forwarded-User")).strip()
        self.email_header = str(os.getenv("PROXY_AUTH_EMAIL_HEADER", "X-Forwarded-Email")).strip()
        # 平台上下文自启开关：只有配置了平台回源地址，网关注入的平台身份头
        # （X-Forwarded-User-Id / X-Forwarded-Project-Id）才会被解析成平台绑定；
        # 未配置时这两个头被无视，独立部署行为与升级前完全一致
        self.platform_context_enabled = bool(os.getenv("PLATFORM_API_BASE_URL", "").strip())

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        principal: Optional[RequestPrincipal] = None
        session_refresh_id: Optional[str] = None
        session_refresh_expires = None

        if self.mode in {AUTH_MODE_LOCAL, AUTH_MODE_HYBRID}:
            raw_session_id = _trim_header(request.cookies.get(auth_cookie_name()), limit=512)
            if raw_session_id:
                resolved = session_principal_from_session_id(raw_session_id, touch=True)
                if resolved is not None:
                    principal, session_refresh_expires = resolved
                    session_refresh_id = raw_session_id

        if principal is None and self.mode in {AUTH_MODE_PROXY, AUTH_MODE_HYBRID}:
            principal = self._resolve_proxy_principal(request)

        if principal is None:
            principal = make_legacy_principal()

        request.state.principal = principal
        allow_legacy_proxy_access = legacy_proxy_access_allowed(principal, mode=self.mode)

        if (
            not _is_anonymous_path(request.url.path)
            and not principal.is_authenticated
            and not allow_legacy_proxy_access
        ):
            detail = "Authentication required."
            if self.mode == AUTH_MODE_PROXY and self.proxy_auth_required:
                detail = f"Missing authenticated user header: {self.user_header}"
            return JSONResponse(status_code=401, content={"detail": detail})

        token = set_current_principal(principal)
        try:
            response = await call_next(request)
        finally:
            reset_current_principal(token)

        skip_cookie_refresh = bool(getattr(request.state, "skip_auth_cookie_refresh", False))
        if session_refresh_id and session_refresh_expires is not None and not skip_cookie_refresh:
            set_session_cookie(
                response,
                session_id=session_refresh_id,
                expires_at=session_refresh_expires,
                host=_trim_header(request.headers.get("host")),
            )
        return response

    def _resolve_proxy_principal(self, request: Request) -> Optional[RequestPrincipal]:
        raw_owner = _trim_header(request.headers.get(self.user_header))
        raw_email = _trim_header(request.headers.get(self.email_header))
        if not raw_owner:
            return None

        # 平台绑定（受信头，仅网关可注入；自启条件见 __init__）：
        # 两个头必须同时有效才升级为 platform 模式，随后上游会按
        # (platform_user_id, platform_project_id) 回源主平台拿项目级 LLM 网关与密钥。
        # 用 isdecimal 而非 isdigit：后者对上下标数字（如 ²）也返回 True 但 int() 会抛错
        platform_kwargs: dict = {}
        if self.platform_context_enabled:
            raw_user_id = _trim_header(request.headers.get("X-Forwarded-User-Id"))
            raw_project_id = _trim_header(request.headers.get("X-Forwarded-Project-Id"))
            if raw_user_id and raw_project_id and raw_user_id.isdecimal() and raw_project_id.isdecimal():
                platform_kwargs = {
                    "access_mode": "platform",
                    "platform_user_id": int(raw_user_id),
                    "platform_project_id": int(raw_project_id),
                }
            elif raw_user_id or raw_project_id:
                logger.debug(
                    "平台头不完整（user_id=%r project_id=%r），按普通 proxy 身份处理",
                    raw_user_id,
                    raw_project_id,
                )

        return RequestPrincipal(
            user_id=raw_owner,
            email=raw_email,
            role="user",
            auth_source="proxy",
            is_authenticated=True,
            **platform_kwargs,
        )
