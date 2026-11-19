from __future__ import annotations

from app.services.auth import auth_cookie_secure
from app.services.foundation.settings import get_settings


def test_auth_cookie_secure_honors_env_fallback(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    try:
        assert auth_cookie_secure() is True
    finally:
        get_settings.cache_clear()


def test_auth_cookie_secure_override_wins_over_app_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    get_settings.cache_clear()
    try:
        assert auth_cookie_secure() is True
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
        assert auth_cookie_secure() is False
        monkeypatch.setenv("AUTH_COOKIE_SECURE", "")
        assert auth_cookie_secure() is True
    finally:
        get_settings.cache_clear()
