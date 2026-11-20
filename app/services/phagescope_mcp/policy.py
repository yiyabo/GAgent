"""Who may hold PhageScope MCP tokens: an explicit allowlist, or every signed-in user when open."""

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
    if is_admin_principal(principal) or get_settings().phagescope_mcp_open_access:
        return True
    user_id = str(principal.user_id or "")
    identities = allowed_identities()
    if user_id and (user_id in allowed_user_ids() or user_id.lower() in identities):
        return True
    email = str(principal.email or "").lower()
    return not get_settings().auth_open_signup and bool(email and email in identities)


def self_issue_scopes() -> tuple[str, ...]:
    """Open access hands out analysis:submit too while analysis is enabled."""
    from app.services.phagescope_mcp import store

    settings = get_settings()
    if settings.phagescope_mcp_open_access and settings.phagescope_mcp_analysis_enabled:
        return (store.SCOPE_DATA_READ, store.SCOPE_ANALYSIS_SUBMIT)
    return (store.SCOPE_DATA_READ,)
