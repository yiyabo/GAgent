from __future__ import annotations

import logging
import os
from typing import Iterable, Optional, Tuple

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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

# Paths that are reachable without a principal. This is an explicit ANONYMOUS
# allowlist (LOCAL_INFRA §120): before 2026-10-10 the middleware kept a list of
# *protected* prefixes instead, so every router whose prefix was missing from
# that list (/tools, /usage, /tasks, /skill-learning) was silently public.
# Anything not matched here requires authentication.
_ANONYMOUS_EXACT = frozenset({"/", "/health", "/health/llm", "/health/ready"})
_ANONYMOUS_PREFIXES: Tuple[str, ...] = ("/auth/", "/sso/")
# API schema/docs stay anonymous only outside production.
_DOCS_PATHS = frozenset({"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"})
# Static SPA assets served by the catch-all in app.main._register_spa. API
# routers are registered under these prefixes, so an unknown path that
# does not start with any API prefix is a frontend asset or client route.
_PROXY_SECRET_HEADER = "X-Gagent-Proxy-Secret"

_WS_CLOSE_UNAUTHENTICATED = 1008  # policy violation


def _is_production() -> bool:
    app_env = str(os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    return app_env in {"prod", "production"}


def _api_prefixes() -> Iterable[str]:
    """Every prefix an API router registered, plus the unprefixed API namespaces."""
    from app.routers.registry import RouterRegistry

    prefixes = {entry.path.rstrip("/") for entry in RouterRegistry.entries() if entry.path}
    # Routers that register at the root or mount outside their declared path.
    prefixes.update({"/api", "/ws", "/mcp", "/models", "/health"})
    return prefixes


def _is_api_path(path: str) -> bool:
    for prefix in _api_prefixes():
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _is_anonymous_path(path: str) -> bool:
    normalized = str(path or "").strip() or "/"
    if normalized in _ANONYMOUS_EXACT:
        return True
    if normalized in _DOCS_PATHS:
        return not _is_production()
    if any(normalized.startswith(prefix) for prefix in _ANONYMOUS_PREFIXES):
        return True
    # Non-API paths are SPA assets/routes handled by the catch-all; the API
    # calls the SPA makes are protected individually.
    return not _is_api_path(normalized)


def _trim_header(value: Optional[str], *, limit: int = 256) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        return text[:limit]
    return text


class ProxyAuthMiddleware:
    """Pure ASGI middleware so both ``http`` and ``websocket`` scopes are covered.

    The previous ``BaseHTTPMiddleware`` implementation only ran for HTTP
    requests; WebSocket handshakes bypassed it entirely, which left
    ``/ws/terminal/{session_id}`` reachable without any credential.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.mode = get_auth_mode()
        self.proxy_auth_required = proxy_auth_required()
        self.user_header = str(os.getenv("PROXY_AUTH_USER_HEADER", "X-Forwarded-User")).strip()
        self.email_header = str(os.getenv("PROXY_AUTH_EMAIL_HEADER", "X-Forwarded-Email")).strip()
        # Shared secret the reverse proxy must present before identity headers
        # are trusted. Unset = legacy behaviour (headers trusted as-is), kept so
        # existing deployments keep working until the gateway is configured.
        self.proxy_shared_secret = str(os.getenv("PROXY_AUTH_SHARED_SECRET", "")).strip()
        # 平台上下文自启开关：只有配置了平台回源地址，网关注入的平台身份头
        # （X-Forwarded-User-Id / X-Forwarded-Project-Id）才会被解析成平台绑定；
        # 未配置时这两个头被无视，独立部署行为与升级前完全一致
        self.platform_context_enabled = bool(os.getenv("PLATFORM_API_BASE_URL", "").strip())

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "http":
            await self._handle_http(scope, receive, send)
        elif scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _handle_http(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        if request.method.upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return

        principal, session_refresh_id, session_refresh_expires = self._resolve(request)
        scope.setdefault("state", {})
        request.state.principal = principal

        if self._denied(principal, request.url.path):
            response = JSONResponse(status_code=401, content={"detail": self._denied_detail()})
            await response(scope, receive, send)
            return

        skip_cookie_refresh = False

        async def send_wrapper(message) -> None:
            nonlocal skip_cookie_refresh
            if (
                message["type"] == "http.response.start"
                and session_refresh_id
                and session_refresh_expires is not None
            ):
                skip_cookie_refresh = bool(
                    getattr(request.state, "skip_auth_cookie_refresh", False)
                )
                if not skip_cookie_refresh:
                    from starlette.datastructures import MutableHeaders
                    from starlette.responses import Response

                    probe = Response()
                    set_session_cookie(
                        probe,
                        session_id=session_refresh_id,
                        expires_at=session_refresh_expires,
                        host=_trim_header(request.headers.get("host")),
                    )
                    headers = MutableHeaders(scope=message)
                    for key, value in probe.raw_headers:
                        if key == b"set-cookie":
                            headers.append("set-cookie", value.decode("latin-1"))
            await send(message)

        token = set_current_principal(principal)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_current_principal(token)

    async def _handle_websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        websocket = WebSocket(scope, receive, send)
        principal, _, _ = self._resolve(websocket)
        scope.setdefault("state", {})
        websocket.state.principal = principal

        if self._denied(principal, websocket.url.path):
            # Close before accept: the client sees a 403 handshake rejection.
            await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason="Authentication required.")
            return

        token = set_current_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_principal(token)

    # -------------------------------------------------------------- helpers
    def _denied(self, principal: RequestPrincipal, path: str) -> bool:
        if _is_anonymous_path(path):
            return False
        if principal.is_authenticated:
            return False
        return not legacy_proxy_access_allowed(principal, mode=self.mode)

    def _denied_detail(self) -> str:
        if self.mode == AUTH_MODE_PROXY and self.proxy_auth_required:
            return f"Missing authenticated user header: {self.user_header}"
        return "Authentication required."

    def _resolve(self, conn):
        """Resolve the principal for an HTTP request or a WebSocket handshake."""
        principal: Optional[RequestPrincipal] = None
        session_refresh_id: Optional[str] = None
        session_refresh_expires = None

        if self.mode in {AUTH_MODE_LOCAL, AUTH_MODE_HYBRID}:
            raw_session_id = _trim_header(conn.cookies.get(auth_cookie_name()), limit=512)
            if raw_session_id:
                resolved = session_principal_from_session_id(raw_session_id, touch=True)
                if resolved is not None:
                    principal, session_refresh_expires = resolved
                    session_refresh_id = raw_session_id

        if principal is None and self.mode in {AUTH_MODE_PROXY, AUTH_MODE_HYBRID}:
            principal = self._resolve_proxy_principal(conn)

        if principal is None:
            principal = make_legacy_principal()
        return principal, session_refresh_id, session_refresh_expires

    def _proxy_headers_trusted(self, conn) -> bool:
        if not self.proxy_shared_secret:
            return True
        presented = _trim_header(conn.headers.get(_PROXY_SECRET_HEADER), limit=512)
        if presented is None:
            return False
        import secrets

        return secrets.compare_digest(presented, self.proxy_shared_secret)

    def _resolve_proxy_principal(self, conn) -> Optional[RequestPrincipal]:
        raw_owner = _trim_header(conn.headers.get(self.user_header))
        raw_email = _trim_header(conn.headers.get(self.email_header))
        if not raw_owner:
            return None
        if not self._proxy_headers_trusted(conn):
            logger.warning(
                "Identity header %s presented without a valid %s; ignoring",
                self.user_header,
                _PROXY_SECRET_HEADER,
            )
            return None

        # 平台绑定（受信头，仅网关可注入；自启条件见 __init__）：
        # 两个头必须同时有效才升级为 platform 模式，随后上游会按
        # (platform_user_id, platform_project_id) 回源主平台拿项目级 LLM 网关与密钥。
        # 用 isdecimal 而非 isdigit：后者对上下标数字（如 ²）也返回 True 但 int() 会抛错
        platform_kwargs: dict = {}
        if self.platform_context_enabled:
            raw_user_id = _trim_header(conn.headers.get("X-Forwarded-User-Id"))
            raw_project_id = _trim_header(conn.headers.get("X-Forwarded-Project-Id"))
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


def resolve_websocket_principal(websocket: WebSocket) -> RequestPrincipal:
    """Principal for a WebSocket handler.

    The middleware stores it on ``websocket.state``; handlers mounted on a bare
    ``FastAPI()`` (unit tests) fall back to resolving from the handshake.
    """
    principal = getattr(websocket.state, "principal", None)
    if isinstance(principal, RequestPrincipal):
        return principal
    middleware = ProxyAuthMiddleware(app=lambda *_: None)  # type: ignore[arg-type]
    principal, _, _ = middleware._resolve(websocket)
    return principal


def websocket_access_allowed(websocket: WebSocket) -> bool:
    principal = resolve_websocket_principal(websocket)
    if principal.is_authenticated:
        return True
    return legacy_proxy_access_allowed(principal)


__all__ = [
    "ProxyAuthMiddleware",
    "resolve_websocket_principal",
    "websocket_access_allowed",
]
