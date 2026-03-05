from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import terminal_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(terminal_routes.router)
    return TestClient(app)


def test_terminal_websocket_roundtrip(monkeypatch) -> None:
    monkeypatch.setenv("TERMINAL_ENABLED", "true")
    client = _build_client()

    terminal_id = None
    try:
        with client.websocket_connect("/ws/terminal/ws-test-session?mode=sandbox") as ws:
            hello = ws.receive_json()
            assert hello.get("type") == "pong"
            terminal_id = str(hello.get("payload", {}).get("terminal_id") or "")
            assert terminal_id

            ws.send_json({"type": "ping", "payload": None})
            _ = ws.receive_json()

            encoded = base64.b64encode(b"echo ws_roundtrip_ok\\n").decode("ascii")
            ws.send_json({"type": "input", "payload": encoded})
    finally:
        if terminal_id:
            client.delete(f"/api/v1/terminal/sessions/{terminal_id}")
        client.close()
