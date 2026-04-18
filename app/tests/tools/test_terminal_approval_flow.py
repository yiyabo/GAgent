from __future__ import annotations

import base64
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import terminal_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(terminal_routes.router)
    return TestClient(app)


def test_forbidden_command_path_smoke(monkeypatch) -> None:
    monkeypatch.setenv("TERMINAL_ENABLED", "true")
    client = _build_client()

    terminal_id = None
    try:
        with client.websocket_connect("/ws/terminal/ws-approval-session?mode=sandbox") as ws:
            hello = ws.receive_json()
            assert hello.get("type") == "pong"
            terminal_id = str(hello.get("payload", {}).get("terminal_id") or "")
            assert terminal_id

            encoded = base64.b64encode(b"rm -rf /tmp/terminal_approval_test\\n").decode("ascii")
            ws.send_json({"type": "input", "payload": encoded})
            time.sleep(0.2)

        audit_resp = client.get(f"/api/v1/terminal/audit?terminal_id={terminal_id}&limit=200")
        assert audit_resp.status_code == 200
        rows = audit_resp.json()
        assert any(row.get("event_type") in {"input", "command_detected"} for row in rows)
    finally:
        if terminal_id:
            client.delete(f"/api/v1/terminal/sessions/{terminal_id}")
        client.close()
