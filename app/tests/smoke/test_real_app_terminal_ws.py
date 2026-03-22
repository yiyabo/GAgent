from __future__ import annotations

import base64
import time

import pytest


@pytest.mark.prod_smoke
def test_real_app_terminal_http_and_websocket_roundtrip(
    app_client_factory,
    isolated_terminal_manager,
) -> None:
    _ = isolated_terminal_manager

    with app_client_factory() as client:
        create_response = client.post(
            "/api/v1/terminal/sessions",
            json={"session_id": "prod-terminal-session", "mode": "sandbox"},
        )
        assert create_response.status_code == 200
        terminal_id = create_response.json()["terminal_id"]

        list_response = client.get(
            "/api/v1/terminal/sessions",
            params={"session_id": "prod-terminal-session"},
        )
        assert list_response.status_code == 200
        assert any(
            row["terminal_id"] == terminal_id for row in list_response.json()
        )

        with client.websocket_connect(
            f"/ws/terminal/prod-terminal-session?mode=sandbox&terminal_id={terminal_id}"
        ) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "pong"
            assert hello["payload"]["terminal_id"] == terminal_id

            ws.send_json({"type": "ping", "payload": None})
            assert ws.receive_json()["type"] == "pong"

            ws.send_json(
                {
                    "type": "input",
                    "payload": base64.b64encode(b"echo prod_ws_ok\n").decode("ascii"),
                }
            )
            time.sleep(0.3)

        replay_response = client.get(
            f"/api/v1/terminal/sessions/{terminal_id}/replay",
            params={"limit": 200},
        )
        assert replay_response.status_code == 200
        replay = replay_response.json()
        assert any(row.get("type") == "i" for row in replay)
        assert any(row.get("type") == "o" for row in replay)

        close_response = client.delete(f"/api/v1/terminal/sessions/{terminal_id}")
        assert close_response.status_code == 200
        assert close_response.json()["success"] is True

        list_after_close = client.get(
            "/api/v1/terminal/sessions",
            params={"session_id": "prod-terminal-session"},
        )
        assert list_after_close.status_code == 200
        assert list_after_close.json() == []


@pytest.mark.prod_smoke
def test_real_app_terminal_disabled_returns_service_unavailable(
    app_client_factory,
    isolated_terminal_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = isolated_terminal_manager
    monkeypatch.setenv("TERMINAL_ENABLED", "false")

    with app_client_factory() as client:
        response = client.post(
            "/api/v1/terminal/sessions",
            json={"session_id": "prod-terminal-disabled", "mode": "sandbox"},
        )
        assert response.status_code == 503
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["message"] == "Terminal feature is disabled"


@pytest.mark.prod_smoke
def test_real_app_terminal_session_mismatch_and_replay_errors_are_diagnostic(
    app_client_factory,
    isolated_terminal_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.routers.terminal_routes as terminal_routes

    _ = isolated_terminal_manager

    with app_client_factory(raise_server_exceptions=False) as client:
        create_response = client.post(
            "/api/v1/terminal/sessions",
            json={"session_id": "terminal-owner-a", "mode": "sandbox"},
        )
        assert create_response.status_code == 200
        terminal_id = create_response.json()["terminal_id"]

        with client.websocket_connect(
            f"/ws/terminal/terminal-owner-b?mode=sandbox&terminal_id={terminal_id}"
        ) as ws:
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["payload"]["code"] == "SESSION_MISMATCH"

        async def _broken_get_replay(_terminal_id: str, *, limit: int = 4000):
            raise RuntimeError("replay query failed")

        monkeypatch.setattr(
            terminal_routes.terminal_session_manager,
            "get_replay",
            _broken_get_replay,
        )

        replay_response = client.get(
            f"/api/v1/terminal/sessions/{terminal_id}/replay",
            params={"limit": 10},
        )
        assert replay_response.status_code == 500
        payload = replay_response.json()
        assert payload["success"] is False
        assert payload["error"]["context"]["exception_type"] == "RuntimeError"
