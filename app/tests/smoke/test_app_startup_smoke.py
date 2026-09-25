from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.routing import Match

from app.database_pool import get_db

# Core routes this smoke test guards, same list the test has asserted since the
# app's first release: two HTTP routes plus the terminal WebSocket route.
CORE_HTTP_ROUTES = (
    "/system/health",
    "/artifacts/sessions/{session_id}/deliverables",
)
CORE_WEBSOCKET_PREFIX = "/ws/terminal/"
# Two distinct ids prove the trailing segment is a parameter, not a literal.
CORE_WEBSOCKET_SESSION_IDS = ("smoke-session", "another-session.42")


def _routable(app: Any, path: str, scope_type: str) -> bool:
    """Whether the app's route table dispatches *path* for this ASGI scope type.

    Route *shape* is not stable across fastapi/starlette generations: older
    versions flatten every ``include_router`` call into ``app.routes`` (each
    entry an ``APIRoute``/``WebSocketRoute`` carrying ``path``), while fastapi
    >= 0.14x keeps one ``_IncludedRouter`` wrapper per call that exposes neither
    ``path`` nor ``routes`` -- hence the original ``route.path`` walk raised
    ``AttributeError`` on newer stacks.

    Dispatch is the contract every generation must honor (it is how a real
    request reaches its handler), so the route is asserted through
    ``route.matches(scope)``, the starlette routing API, instead of reading
    attributes off whichever objects the table happens to hold.
    """
    scope = {
        "type": scope_type,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
        "method": "GET",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return any(route.matches(scope)[0] is Match.FULL for route in app.router.routes)


@pytest.mark.prod_smoke
def test_create_app_startup_initializes_core_routes_and_schema(app_client_factory) -> None:
    with app_client_factory() as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        llm_response = client.get("/health/llm?ping=false")
        assert llm_response.status_code == 200
        payload = llm_response.json()
        assert payload["ping_ok"] is None
        assert payload["model"] == "qwen-test"
        assert payload["has_api_key"] is True

        # Core HTTP routes: assert the exact templates through the public,
        # generation-independent OpenAPI view of the paths the app serves.
        documented_paths = set(client.app.openapi().get("paths", {}))
        for path in CORE_HTTP_ROUTES:
            assert path in documented_paths, f"{path} missing from the OpenAPI paths"

        # Core WebSocket route: OpenAPI never lists WebSocket routes, so assert
        # it by dispatch. Distinct session ids must land on the route, while the
        # prefix alone and a second segment must not -- together that pins the
        # guarded route to `/ws/terminal/{session_id}`.
        for session_id in CORE_WEBSOCKET_SESSION_IDS:
            probe = f"{CORE_WEBSOCKET_PREFIX}{session_id}"
            assert _routable(client.app, probe, "websocket"), f"{probe} is not routed"
        for unrouted in (
            CORE_WEBSOCKET_PREFIX.rstrip("/"),
            CORE_WEBSOCKET_PREFIX,
            f"{CORE_WEBSOCKET_PREFIX}a/b",
        ):
            assert not _routable(client.app, unrouted, "websocket"), (
                f"{unrouted} is routed, so the session id is not the single "
                f"parameter segment of {CORE_WEBSOCKET_PREFIX}{{session_id}}"
            )

        with get_db() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        table_names = {str(row["name"]) for row in rows}
        assert {"plans", "chat_sessions", "chat_messages", "chat_runs"}.issubset(table_names)


@pytest.mark.prod_smoke
def test_create_app_startup_still_serves_health_when_toolbox_init_fails(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as app_main

    async def _failing_initialize_toolbox() -> None:
        raise RuntimeError("toolbox unavailable")

    monkeypatch.setattr(app_main, "initialize_toolbox", _failing_initialize_toolbox)

    with app_client_factory() as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "AI-Driven Task Orchestration System"
