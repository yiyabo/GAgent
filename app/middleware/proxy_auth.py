from __future__ import annotations

import os
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.services.request_principal import (
    LEGACY_LOCAL_OWNER_ID,
    RequestPrincipal,
)
from app.utils.route_helpers import parse_bool


def _trim_header(value: Optional[str], *, limit: int = 256) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        return text[:limit]
    return text


def _proxy_auth_required() -> bool:
    configured = os.getenv("PROXY_AUTH_REQUIRED")
    if configured is not None:
        return parse_bool(configured, default=False)

    app_env = str(os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    return app_env in {"prod", "production"}


class ProxyAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.user_header = str(
            os.getenv("PROXY_AUTH_USER_HEADER", "X-Forwarded-User")
        ).strip()
        self.email_header = str(
            os.getenv("PROXY_AUTH_EMAIL_HEADER", "X-Forwarded-Email")
        ).strip()

    async def dispatch(self, request: Request, call_next):
        raw_owner = _trim_header(request.headers.get(self.user_header))
        raw_email = _trim_header(request.headers.get(self.email_header))

        if not raw_owner and _proxy_auth_required():
            return JSONResponse(
                status_code=401,
                content={"detail": f"Missing authenticated user header: {self.user_header}"},
            )

        principal = RequestPrincipal(
            owner_id=raw_owner or LEGACY_LOCAL_OWNER_ID,
            email=raw_email,
            auth_source="proxy" if raw_owner else "fallback",
        )
        request.state.principal = principal
        return await call_next(request)

