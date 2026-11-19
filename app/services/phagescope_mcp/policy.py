"""Who may hold PhageScope MCP tokens (phase 1: an explicit allowlist)."""

from __future__ import annotations

from app.services.foundation.settings import get_settings
from app.services.request_principal import RequestPrincipal, is_admin_principal


def allowed_identities() -> set[str]:
    raw = str(get_settings().phagescope_mcp_allowed_users or "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def may_self_issue(principal: RequestPrincipal) -> bool:
    """Admins, and users whose id or email is on PHAGESCOPE_MCP_ALLOWED_USERS."""
    if not principal.is_authenticated:
        return False
    if is_admin_principal(principal):
        return True
    allowed = allowed_identities()
    ids = {str(principal.user_id or "").lower(), str(principal.email or "").lower()}
    return bool(allowed & (ids - {""}))
