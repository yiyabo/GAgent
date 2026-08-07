from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.foundation.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_rate_limit_defaults(monkeypatch) -> None:
    for var in (
        "RATE_LIMIT_REGISTER",
        "RATE_LIMIT_LOGIN",
        "RATE_LIMIT_CHANGE_PASSWORD",
        "RATE_LIMIT_SSO_COMPLETE",
        "RATE_LIMIT_SSO_LOGIN",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.rate_limit_register == 5
    assert s.rate_limit_login == 10
    assert s.rate_limit_change_password == 5
    assert s.rate_limit_sso_complete == 10
    assert s.rate_limit_sso_login == 10


def test_rate_limit_env_override(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REGISTER", "100000")
    monkeypatch.setenv("RATE_LIMIT_LOGIN", "999")
    get_settings.cache_clear()
    s = get_settings()
    assert s.rate_limit_register == 100000
    assert s.rate_limit_login == 999


def test_rate_limit_invalid_env_rejected(monkeypatch) -> None:
    # Consistent with every other int field in AppSettings: pydantic-settings
    # raises on unparseable env values (fail-fast on misconfiguration).
    monkeypatch.setenv("RATE_LIMIT_REGISTER", "not-a-number")
    get_settings.cache_clear()
    with pytest.raises(Exception):
        get_settings()


def _make_request() -> MagicMock:
    request = MagicMock()
    request.client = SimpleNamespace(host="10.0.0.1")
    request.headers = {}
    request.cookies = {}
    return request


def test_register_route_uses_configured_limit(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REGISTER", "7")
    get_settings.cache_clear()

    from app.routers import auth_routes

    captured = {}
    monkeypatch.setattr(auth_routes, "require_local_auth_enabled", lambda: None)
    monkeypatch.setattr(
        auth_routes,
        "rate_limiter",
        SimpleNamespace(
            check=lambda bucket, ident, *, limit, window_seconds: captured.update(
                bucket=bucket, limit=limit, window_seconds=window_seconds
            )
        ),
    )
    monkeypatch.setattr(
        auth_routes, "register_user", lambda email, password: {"id": "u1", "email": email}
    )
    monkeypatch.setattr(
        auth_routes,
        "create_auth_session",
        lambda user_id, **kwargs: {"id": "sess1", "expires_at": 0},
    )
    monkeypatch.setattr(auth_routes, "set_session_cookie", lambda *a, **k: None)
    monkeypatch.setattr(
        auth_routes, "build_user_payload", lambda user: {"user_id": "u1", "email": "a@b.com", "role": "user"}
    )

    payload = auth_routes.RegisterRequest(email="a@b.com", password="password123")
    auth_routes.register_local_account(payload, _make_request(), MagicMock())

    assert captured["bucket"] == "register"
    assert captured["limit"] == 7
    assert captured["window_seconds"] == 15 * 60


def test_sso_login_route_uses_configured_limit(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_SSO_LOGIN", "4321")
    get_settings.cache_clear()

    from app.routers import sso_routes

    captured = {}
    monkeypatch.setattr(
        sso_routes,
        "rate_limiter",
        SimpleNamespace(
            check=lambda bucket, ident, *, limit, window_seconds: captured.update(
                bucket=bucket, limit=limit, window_seconds=window_seconds
            )
        ),
    )

    async def _fake_verify(token):
        return {}

    monkeypatch.setattr(sso_routes, "verify_sso_token", _fake_verify)
    monkeypatch.setenv("SSO_ALLOWED_REDIRECT_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()

    import asyncio

    async def _invoke():
        try:
            await sso_routes.sso_login(
                _make_request(), MagicMock(), token="t", redirect_url=None, project_id=None
            )
        except Exception:
            pass

    asyncio.run(_invoke())
    assert captured["bucket"] == "sso_login"
    assert captured["limit"] == 4321
    assert captured["window_seconds"] == 60
