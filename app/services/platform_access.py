"""Bind platform-origin requests to server-side SSO session context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from app.services.request_principal import get_request_principal

if TYPE_CHECKING:
    from app.routers.chat.models import ChatRequest


def bind_chat_request_to_principal(request: Request, payload: "ChatRequest") -> "ChatRequest":
    """Reject caller-supplied platform identity and apply the trusted session binding."""
    principal = get_request_principal(request)
    if not principal.is_platform_access:
        if payload.project_id is not None or payload.user_id is not None:
            raise HTTPException(
                status_code=400,
                detail="project_id and user_id are only available through a platform SSO session",
            )
        return payload

    platform_user_id = principal.require_platform_user_id()
    platform_project_id = principal.require_platform_project_id()
    if payload.user_id is not None and int(payload.user_id) != platform_user_id:
        raise HTTPException(status_code=403, detail="Platform user binding mismatch")
    if payload.project_id is not None and int(payload.project_id) != platform_project_id:
        raise HTTPException(status_code=403, detail="Platform project binding mismatch")
    context = dict(payload.context or {})
    context.update(
        {
            "platform_access_mode": "platform",
            "platform_user_id": platform_user_id,
            "platform_project_id": platform_project_id,
        }
    )
    return payload.model_copy(
        update={
            "user_id": platform_user_id,
            "project_id": platform_project_id,
            "context": context,
        }
    )


def require_bound_platform_project(request: Request, project_id: int) -> tuple[int, int]:
    """Return the trusted platform identity after validating the path project ID."""
    principal = get_request_principal(request)
    if not principal.is_platform_access:
        raise HTTPException(status_code=403, detail="Platform SSO access is required")
    platform_user_id = principal.require_platform_user_id()
    platform_project_id = principal.require_platform_project_id()
    if int(project_id) != platform_project_id:
        raise HTTPException(status_code=403, detail="Platform project binding mismatch")
    return platform_user_id, platform_project_id
