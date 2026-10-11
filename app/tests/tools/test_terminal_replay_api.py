from __future__ import annotations

import base64
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import terminal_routes


def _build_client() -> TestClient:
    # Bare app without the auth middleware: handlers authenticate themselves,
    # so opt into the single-tenant proxy fallback explicitly instead of
    # inheriting whatever settings cache a neighbouring test left behind.
    import os

    from app.services.foundation.settings import get_settings

    os.environ["AUTH_MODE"] = "proxy"
    os.environ.pop("PROXY_AUTH_REQUIRED", None)
    os.environ.setdefault("APP_ENV", "development")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(terminal_routes.router)
    return TestClient(app)


def test_replay_api_returns_events(monkeypatch, local_sandbox_terminal) -> None:
    monkeypatch.setenv("TERMINAL_ENABLED", "true")
    client = _build_client()

    create = client.post(
        "/api/v1/terminal/sessions",
        json={"session_id": "ws-replay-session", "mode": "sandbox"},
    )
    assert create.status_code == 200
    terminal_id = create.json()["terminal_id"]

    try:
        with client.websocket_connect(f"/ws/terminal/ws-replay-session?mode=sandbox&terminal_id={terminal_id}") as ws:
            hello = ws.receive_json()
            assert hello.get("type") == "pong"
            encoded = base64.b64encode(b"echo replay_event_ok\\n").decode("ascii")
            ws.send_json({"type": "input", "payload": encoded})
            time.sleep(0.2)

        replay_resp = client.get(f"/api/v1/terminal/sessions/{terminal_id}/replay?limit=100")
        assert replay_resp.status_code == 200
        replay = replay_resp.json()
        assert isinstance(replay, list)
        assert replay
        assert any(row.get("type") == "i" for row in replay)
    finally:
        client.delete(f"/api/v1/terminal/sessions/{terminal_id}")
        client.close()
