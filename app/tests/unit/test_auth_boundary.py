"""Authentication boundary regressions (LOCAL_INFRA §120).

Before 2026-10-10 the middleware kept a list of *protected* prefixes, so any
router whose prefix was missing from it was public: ``/tools``, ``/usage``,
``/tasks`` and ``/skill-learning`` were reachable from the internet with no
credential, and the ``/ws/terminal`` WebSocket (a bash PTY) bypassed the
HTTP-only middleware entirely. These tests pin the inverted model: an explicit
anonymous allowlist, everything else (HTTP and WebSocket) requires a principal.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from app.middleware import proxy_auth


def _local_mode(monkeypatch) -> None:
    """Production shape: cookie auth, no proxy fallback."""
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.delenv("PROXY_AUTH_REQUIRED", raising=False)
    get_settings.cache_clear()


# ------------------------------------------------------------ pure function
@pytest.mark.parametrize(
    "path",
    [
        "/tools/available",
        "/tools/bio-tools",
        "/usage/overview",
        "/usage/runs",
        "/tasks/1/result",
        "/tasks/1/execute",
        "/skill-learning/skills/x",
        "/project/1/files",
        "/plans/1/results",
        "/chat/sessions",
        "/api/v1/terminal/sessions",
        "/ws/terminal/abc",
        "/execution/shell",
        "/mcp/tools",
        "/models",
    ],
)
def test_every_api_prefix_is_protected(path: str) -> None:
    import app.routers  # noqa: F401  (populates the router registry)

    assert proxy_auth._is_anonymous_path(path) is False


@pytest.mark.parametrize("path", ["/", "/health", "/health/llm", "/auth/login", "/sso/complete", "/assets/app.js", "/some/spa/route"])
def test_anonymous_allowlist(path: str) -> None:
    import app.routers  # noqa: F401

    assert proxy_auth._is_anonymous_path(path) is True


def test_docs_are_anonymous_only_outside_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    assert proxy_auth._is_anonymous_path("/docs") is True
    monkeypatch.setenv("APP_ENV", "production")
    assert proxy_auth._is_anonymous_path("/docs") is False
    assert proxy_auth._is_anonymous_path("/openapi.json") is False


# ------------------------------------------------------------ through the app
@pytest.mark.parametrize(
    "path",
    ["/tools/available", "/usage/overview", "/tasks/1/result", "/skill-learning/skills/x", "/plans/1/results"],
)
def test_unauthenticated_http_requests_are_rejected_in_local_mode(app_client_factory, monkeypatch, path) -> None:
    _local_mode(monkeypatch)
    with app_client_factory(raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code == 401, (path, response.status_code, response.text[:200])


def test_health_stays_anonymous_in_local_mode(app_client_factory, monkeypatch) -> None:
    _local_mode(monkeypatch)
    with app_client_factory() as client:
        assert client.get("/health").status_code == 200


def test_unauthenticated_terminal_websocket_is_rejected_in_local_mode(app_client_factory, monkeypatch) -> None:
    _local_mode(monkeypatch)
    monkeypatch.setenv("TERMINAL_ENABLED", "true")
    with app_client_factory(raise_server_exceptions=False) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/terminal/anyone?mode=sandbox"):
                pass
    assert exc_info.value.code == 1008


def test_terminal_is_disabled_by_default(monkeypatch) -> None:
    from app.routers import terminal_routes

    monkeypatch.delenv("TERMINAL_ENABLED", raising=False)
    assert terminal_routes._terminal_enabled() is False


def test_proxy_identity_headers_require_shared_secret_when_configured(app_client_factory, monkeypatch) -> None:
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PROXY_AUTH_REQUIRED", "1")
    monkeypatch.setenv("PROXY_AUTH_SHARED_SECRET", "s3cret")
    get_settings.cache_clear()
    with app_client_factory(raise_server_exceptions=False) as client:
        forged = client.get("/chat/sessions", headers={"X-Forwarded-User": "victim"})
        assert forged.status_code == 401
        trusted = client.get(
            "/chat/sessions",
            headers={"X-Forwarded-User": "victim", "X-Gagent-Proxy-Secret": "s3cret"},
        )
        assert trusted.status_code == 200


# ------------------------------------------------------------ owner binding
def test_execution_workspace_owner_is_the_principal(app_client_factory, monkeypatch) -> None:
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PROXY_AUTH_REQUIRED", "1")
    get_settings.cache_clear()
    alice = {"X-Forwarded-User": "alice"}
    with app_client_factory(raise_server_exceptions=False) as client:
        # Path owner that is not the caller -> 403
        assert client.get("/execution/workspaces/bob", headers=alice).status_code == 403
        assert client.delete("/execution/workspaces/bob", headers=alice).status_code == 403
        # Body owner that is not the caller -> 403, never runs
        forged = client.post(
            "/execution/shell",
            headers=alice,
            json={"owner": "bob", "command": "echo hi"},
        )
        assert forged.status_code == 403
        # Own workspace works, and the body owner is optional
        own = client.get("/execution/workspaces/alice", headers=alice)
        assert own.status_code == 200
        assert own.json()["owner"] == "alice"


def test_usage_endpoints_require_auth_and_scope_to_owner(app_client_factory, monkeypatch) -> None:
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PROXY_AUTH_REQUIRED", "1")
    get_settings.cache_clear()
    with app_client_factory(raise_server_exceptions=False) as client:
        assert client.get("/usage/overview").status_code == 401

        from app.database_pool import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO chat_sessions (id, name, owner_id) VALUES (?, ?, ?)",
                ("sess-alice", "a", "alice"),
            )
            conn.execute(
                "INSERT INTO chat_sessions (id, name, owner_id) VALUES (?, ?, ?)",
                ("sess-bob", "b", "bob"),
            )
            for sid, tokens in (("sess-alice", 10), ("sess-bob", 1000)):
                conn.execute(
                    """
                    INSERT INTO llm_usage_log (provider, model, prompt_tokens, completion_tokens,
                        total_tokens, estimated_cost, session_id, call_status, created_at)
                    VALUES ('qwen', 'm', ?, 0, ?, 0.0, ?, 'ok', '2026-10-10T00:00:00+00:00')
                    """,
                    (tokens, tokens, sid),
                )
            conn.commit()

        alice = client.get("/usage/overview", headers={"X-Forwarded-User": "alice"})
        assert alice.status_code == 200
        assert alice.json()["totals"]["total_tokens"] == 10

        bob_view_of_alice = client.get(
            "/usage/runs", params={"session_id": "sess-alice"}, headers={"X-Forwarded-User": "bob"}
        )
        assert bob_view_of_alice.status_code == 200
        assert bob_view_of_alice.json()["runs"] == []

        top = client.get("/usage/top/sessions", headers={"X-Forwarded-User": "bob"})
        assert [row["session_id"] for row in top.json()["sessions"]] == ["sess-bob"]
