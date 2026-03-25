from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request


LEGACY_LOCAL_OWNER_ID = "legacy-local"
_current_principal: ContextVar["RequestPrincipal"] = ContextVar(
    "current_request_principal"
)


@dataclass(frozen=True)
class RequestPrincipal:
    user_id: str
    email: Optional[str]
    role: str = "user"
    auth_source: str = "proxy"
    is_authenticated: bool = True

    @property
    def owner_id(self) -> str:
        return self.user_id


def make_legacy_principal(*, authenticated: bool = False, source: str = "fallback") -> RequestPrincipal:
    return RequestPrincipal(
        user_id=LEGACY_LOCAL_OWNER_ID,
        email=None,
        role="legacy",
        auth_source=source,
        is_authenticated=authenticated,
    )


def set_current_principal(principal: RequestPrincipal) -> Token:
    return _current_principal.set(principal)


def reset_current_principal(token: Token) -> None:
    _current_principal.reset(token)


def get_current_principal() -> Optional[RequestPrincipal]:
    try:
        return _current_principal.get()
    except LookupError:
        return None


def get_request_principal(request: Request) -> RequestPrincipal:
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, RequestPrincipal):
        return principal
    current = get_current_principal()
    if current is not None:
        return current
    return make_legacy_principal()


def get_request_owner_id(request: Request) -> str:
    return get_request_principal(request).owner_id


def require_authenticated_principal(
    request: Request,
    *,
    detail: str = "Authentication required",
) -> RequestPrincipal:
    principal = get_request_principal(request)
    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail=detail)
    return principal


def ensure_owner_access(
    request: Request,
    resource_owner_id: Optional[str],
    *,
    detail: str = "Forbidden",
) -> str:
    request_owner_id = get_request_owner_id(request)
    normalized = str(resource_owner_id or "").strip() or LEGACY_LOCAL_OWNER_ID
    if normalized != request_owner_id:
        raise HTTPException(status_code=403, detail=detail)
    return request_owner_id
