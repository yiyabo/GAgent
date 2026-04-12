from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import terminal_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(terminal_routes.router)
    return TestClient(app)


def _cleanup_reaper() -> None:
    task = getattr(terminal_routes.terminal_session_manager, "_reaper_task", None)
    if task is not None:
        task.cancel()
        terminal_routes.terminal_session_manager._reaper_task = None


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
        _cleanup_reaper()
        client.close()


def test_terminal_websocket_mode_mismatch_reuses_chat_session_with_requested_mode(monkeypatch) -> None:
    monkeypatch.setenv("TERMINAL_ENABLED", "true")
    client = _build_client()

    sandbox_session = SimpleNamespace(
        terminal_id="sandbox-tid",
        session_id="ws-test-session",
        mode="sandbox",
    )
    qwen_session = SimpleNamespace(
        terminal_id="qwen-tid",
        session_id="ws-test-session",
        mode="qwen_code",
    )

    async def _fake_get_session(terminal_id: str):
        assert terminal_id == "sandbox-tid"
        return sandbox_session

    async def _fake_ensure_session_for_chat(session_id: str, *, mode: str):
        assert session_id == "ws-test-session"
        assert mode == "qwen_code"
        return qwen_session

    async def _fake_subscribe(_terminal_id: str):
        return asyncio.Queue()

    async def _fake_unsubscribe(_terminal_id: str, _queue):
        return None

    monkeypatch.setattr(terminal_routes.terminal_session_manager, "get_session", _fake_get_session)
    monkeypatch.setattr(
        terminal_routes.terminal_session_manager,
        "ensure_session_for_chat",
        _fake_ensure_session_for_chat,
    )
    monkeypatch.setattr(terminal_routes.terminal_session_manager, "subscribe", _fake_subscribe)
    monkeypatch.setattr(terminal_routes.terminal_session_manager, "unsubscribe", _fake_unsubscribe)

    try:
        with client.websocket_connect(
            "/ws/terminal/ws-test-session?mode=qwen_code&terminal_id=sandbox-tid"
        ) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "pong"
            assert hello["payload"]["terminal_id"] == "qwen-tid"
            assert hello["payload"]["mode"] == "qwen_code"
    finally:
        _cleanup_reaper()
        client.close()
