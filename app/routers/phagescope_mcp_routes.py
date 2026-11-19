"""Self-service tokens for the external PhageScope MCP endpoint (``/mcp``).

Phase 1 issues tokens only to admins and users on PHAGESCOPE_MCP_ALLOWED_USERS.
The plaintext token is returned once, at creation.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services.foundation.settings import get_settings
from ..services.phagescope_mcp import policy, store
from ..services.request_principal import require_authenticated_principal
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
    )
    return {"token": plaintext, "record": record.public()}


@router.delete("/tokens/{token_id}")
async def revoke_my_token(token_id: str, request: Request) -> dict[str, Any]:
    principal = require_authenticated_principal(request)
    if not store.revoke_token(token_id, user_id=principal.user_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return {"revoked": token_id}
