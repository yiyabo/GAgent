"""Self-service tokens for the external PhageScope MCP endpoint (``/mcp``).

Phase 1 issues tokens only to admins and users on PHAGESCOPE_MCP_ALLOWED_USERS.
The plaintext token is returned once, at creation.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..services.foundation.settings import get_settings
from ..services.phagescope_mcp import analysis, policy, store
from ..services.request_principal import is_admin_principal, require_authenticated_principal
from . import register_router

router = APIRouter(prefix="/phagescope-mcp", tags=["phagescope-mcp"])

register_router(
    namespace="phagescope_mcp",
    version="v1",
    path="/phagescope-mcp",
    router=router,
    tags=["phagescope-mcp"],
    description="Personal access tokens for the external PhageScope MCP endpoint",
)

MAX_ACTIVE_TOKENS_PER_USER = 5


class IssueTokenRequest(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=80)
    expires_in_days: Optional[int] = Field(default=90, ge=1, le=365)


def _usage(token: store.McpToken) -> dict[str, Any]:
    return {**token.public(), "bytes_used_today": store.bytes_used_today(token.id)}


@router.get("/tokens")
async def list_my_tokens(request: Request) -> dict[str, Any]:
    principal = require_authenticated_principal(request)
    return {
        "eligible": policy.may_self_issue(principal),
        "daily_bytes_limit": int(get_settings().phagescope_mcp_daily_bytes),
        "tokens": [_usage(t) for t in store.list_tokens(principal.user_id)],
    }


@router.post("/tokens")
async def issue_my_token(payload: IssueTokenRequest, request: Request) -> dict[str, Any]:
    principal = require_authenticated_principal(request)
    if not policy.may_self_issue(principal):
        raise HTTPException(status_code=403, detail="PhageScope MCP access is limited to allowlisted users.")
    active = [t for t in store.list_tokens(principal.user_id) if not t.revoked_at]
    if len(active) >= MAX_ACTIVE_TOKENS_PER_USER:
        raise HTTPException(status_code=409, detail="Revoke an existing token before creating another.")
    plaintext, record = store.issue_token(
        principal.user_id,
        name=payload.name,
        created_by=principal.user_id,
        expires_in_days=payload.expires_in_days,
        scopes=policy.self_issue_scopes(),
    )
    return {"token": plaintext, "record": record.public()}


@router.delete("/tokens/{token_id}")
async def revoke_my_token(token_id: str, request: Request) -> dict[str, Any]:
    principal = require_authenticated_principal(request)
    if not store.revoke_token(token_id, user_id=principal.user_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return {"revoked": token_id}


class TokenScopesRequest(BaseModel):
    scopes: list[str] = Field(min_length=1, max_length=len(store.KNOWN_SCOPES))


@router.put("/admin/tokens/{token_id}/scopes")
async def set_token_scopes(token_id: str, payload: TokenScopesRequest, request: Request) -> dict[str, Any]:
    """Admins grant or remove scopes such as analysis:submit on an existing token."""
    principal = require_authenticated_principal(request)
    if not is_admin_principal(principal):
        raise HTTPException(status_code=403, detail="Only admins can change token scopes.")
    try:
        record = store.set_token_scopes(token_id, tuple(payload.scopes))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"record": record.public()}


@router.get("/analysis-files/{task_id}")
async def download_analysis_file(
    task_id: str,
    path: str = Query(..., max_length=1024),
    expires: int = Query(...),
    sig: str = Query(..., max_length=128),
) -> FileResponse:
    """Deliverable download for MCP analysis tasks; the HMAC link is the credential."""
    if not get_settings().phagescope_mcp_analysis_enabled or not analysis.verify_link(task_id, path, expires, sig):
        raise HTTPException(status_code=404, detail="Link is invalid or expired.")
    task, target = analysis.deliverable_file(task_id, path)
    if task is None or target is None:
        raise HTTPException(status_code=404, detail="File not found")
    store.record_usage(task.token_id, target.stat().st_size)
    return FileResponse(target, filename=target.name, headers={"Cache-Control": "no-store"})
