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
