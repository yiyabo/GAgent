"""Reply preference endpoints; no model or tool execution."""
from fastapi import APIRouter, Request
from app.database import get_db
from app.repository.response_preferences import get_preferences
from app.services.request_principal import require_authenticated_principal
from app.services.response_style import ResponsePreferences


def mount_response_preferences_routes(router: APIRouter) -> None:
    @router.get("/response-preferences", response_model=ResponsePreferences)
    async def response_preferences(request: Request) -> ResponsePreferences:
        principal = require_authenticated_principal(request)
        with get_db() as conn:
            return get_preferences(conn, principal.owner_id)
