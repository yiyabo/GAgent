from __future__ import annotations

import sys

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers.execution_routes import router
from app.services.foundation.settings import get_settings
from app.services.request_principal import RequestPrincipal


def _client(principal: RequestPrincipal) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_principal(request: Request, call_next):
        request.state.principal = principal
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_shell_is_disabled_for_normal_users_and_admins_by_default(isolated_app_env, monkeypatch) -> None:
    monkeypatch.delenv("EXECUTION_SHELL_ENABLED", raising=False)
    get_settings.cache_clear()

    for principal in (
        RequestPrincipal(user_id="user", email="user@example.test", role="user"),
        RequestPrincipal(user_id="admin", email="admin@example.test", role="admin"),
    ):
        with _client(principal) as client:
            response = client.post("/execution/shell", json={"command": ["echo", "blocked"]})
        assert response.status_code == 404


def test_shell_rejects_normal_users_when_enabled(isolated_app_env, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_SHELL_ENABLED", "1")
    get_settings.cache_clear()

    with _client(
        RequestPrincipal(user_id="user", email="user@example.test", role="user"),
    ) as client:
        response = client.post("/execution/shell", json={"command": ["echo", "blocked"]})

    assert response.status_code == 403


def test_enabled_shell_is_admin_only_and_scrubs_child_environment(isolated_app_env, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_SHELL_ENABLED", "1")
    monkeypatch.setenv("SYNTHETIC_EXECUTION_SECRET", "must-not-reach-child")
    get_settings.cache_clear()

    with _client(
        RequestPrincipal(user_id="admin", email="admin@example.test", role="admin"),
    ) as client:
        response = client.post(
            "/execution/shell",
            json={
                "command": [
                    sys.executable,
                    "-c",
                    "import os; print(os.getenv('SYNTHETIC_EXECUTION_SECRET', 'missing'))",
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["stdout"].strip() == "missing"


@pytest.mark.parametrize("command, expected", [([], 422), (["rm", "synthetic"], 400)])
def test_enabled_admin_shell_rejects_invalid_commands(isolated_app_env, monkeypatch, command, expected) -> None:
    monkeypatch.setenv("EXECUTION_SHELL_ENABLED", "1")
    get_settings.cache_clear()
    with _client(RequestPrincipal(user_id="admin", email=None, role="admin")) as client:
        response = client.post("/execution/shell", json={"command": command})
    assert response.status_code == expected
