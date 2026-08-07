#!/usr/bin/env python3
"""SSO user-sync endpoint must fail closed when no shared key is configured."""

import pytest
from fastapi import HTTPException

from app.routers import sso_routes


class _FakeSettings:
    def __init__(self, key) -> None:
        self.sso_user_sync_api_key = key


class _FakeRequest:
    def __init__(self, headers=None) -> None:
        self.headers = headers or {}


def _sync_payload() -> sso_routes.SSOUserSyncRequest:
    return sso_routes.SSOUserSyncRequest(
        global_uuid="g-1",
        action="create",
        user={"username": "alice"},
    )


def test_sso_user_sync_fails_closed_without_configured_key(monkeypatch) -> None:
    monkeypatch.setattr(sso_routes, "get_settings", lambda: _FakeSettings(None))

    with pytest.raises(HTTPException) as exc_info:
        sso_routes.sync_sso_user_endpoint(_sync_payload(), _FakeRequest())

    assert exc_info.value.status_code == 503


def test_sso_user_sync_rejects_wrong_key(monkeypatch) -> None:
    monkeypatch.setattr(sso_routes, "get_settings", lambda: _FakeSettings("secret"))

    with pytest.raises(HTTPException) as exc_info:
        sso_routes.sync_sso_user_endpoint(
            _sync_payload(), _FakeRequest({"X-Sso-Sync-Key": "wrong"})
        )

    assert exc_info.value.status_code == 401


def test_sso_user_sync_accepts_valid_key(monkeypatch) -> None:
    monkeypatch.setattr(sso_routes, "get_settings", lambda: _FakeSettings("secret"))
    monkeypatch.setattr(
        sso_routes,
        "sync_sso_user",
        lambda data: {"code": "CREATED", "message": "ok"},
    )

    response = sso_routes.sync_sso_user_endpoint(
        _sync_payload(), _FakeRequest({"X-Sso-Sync-Key": "secret"})
    )

    assert response.code == "CREATED"
