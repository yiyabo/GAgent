from __future__ import annotations

from pathlib import Path

import pytest

from app.database_pool import get_db


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

        route_paths = {route.path for route in client.app.routes}
        assert "/system/health" in route_paths
        assert "/artifacts/sessions/{session_id}/deliverables" in route_paths
        assert "/ws/terminal/{session_id}" in route_paths

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
