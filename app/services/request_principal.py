from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request


LEGACY_LOCAL_OWNER_ID = "legacy-local"


@dataclass(frozen=True)
class RequestPrincipal:
    owner_id: str
    email: Optional[str]
    auth_source: str = "proxy"


def get_request_principal(request: Request) -> RequestPrincipal:
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, RequestPrincipal):
        return principal
    return RequestPrincipal(
        owner_id=LEGACY_LOCAL_OWNER_ID,
        email=None,
        auth_source="fallback",
    )


def get_request_owner_id(request: Request) -> str:
    return get_request_principal(request).owner_id


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

