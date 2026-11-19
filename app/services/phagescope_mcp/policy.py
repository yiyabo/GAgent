"""Who may hold PhageScope MCP tokens (phase 1: an explicit allowlist)."""

from __future__ import annotations

from app.services.foundation.settings import get_settings
from app.services.request_principal import RequestPrincipal, is_admin_principal


def allowed_identities() -> set[str]:
    raw = str(get_settings().phagescope_mcp_allowed_users or "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def allowed_user_ids() -> set[str]:
    raw = str(get_settings().phagescope_mcp_allowed_user_ids or "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def may_self_issue(principal: RequestPrincipal) -> bool:
    """Current token eligibility; email-only matches require closed signup."""
    if not principal.is_authenticated:
        return False
    if is_admin_principal(principal):
        return True
    user_id = str(principal.user_id or "")
    identities = allowed_identities()
    if user_id and (user_id in allowed_user_ids() or user_id.lower() in identities):
        return True
    email = str(principal.email or "").lower()
    return not get_settings().auth_open_signup and bool(email and email in identities)
