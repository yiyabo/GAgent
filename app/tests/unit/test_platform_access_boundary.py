from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.chat.models import ChatRequest
from app.services.platform_access import (
    bind_chat_request_to_principal,
    require_bound_platform_project,
)
from app.services.request_principal import RequestPrincipal


def _request(principal: RequestPrincipal):
    return SimpleNamespace(state=SimpleNamespace(principal=principal))


def test_platform_chat_context_comes_from_authenticated_principal() -> None:
    principal = RequestPrincipal(
        user_id="local-user",
        email="user@example.com",
        auth_source="sso",
        access_mode="platform",
        platform_user_id=17,
        platform_project_id=9,
    )
    payload = ChatRequest(message="hello", session_id="session-1")

    bound = bind_chat_request_to_principal(_request(principal), payload)

    assert bound.user_id == 17
    assert bound.project_id == 9
    assert bound.context is not None
    assert bound.context["platform_access_mode"] == "platform"
    assert bound.context["platform_user_id"] == 17
    assert bound.context["platform_project_id"] == 9


@pytest.mark.parametrize(
    ("user_id", "project_id"),
    [(18, 9), (17, 10)],
)
def test_platform_chat_rejects_forged_identity(user_id: int, project_id: int) -> None:
    principal = RequestPrincipal(
        user_id="local-user",
        email="user@example.com",
        auth_source="sso",
        access_mode="platform",
        platform_user_id=17,
        platform_project_id=9,
    )

    with pytest.raises(HTTPException) as exc:
        bind_chat_request_to_principal(
            _request(principal),
            ChatRequest(message="hello", user_id=user_id, project_id=project_id),
        )

    assert exc.value.status_code == 403


def test_local_chat_rejects_platform_identity_fields() -> None:
    principal = RequestPrincipal(user_id="local-user", email="user@example.com")

    with pytest.raises(HTTPException) as exc:
        bind_chat_request_to_principal(
            _request(principal),
            ChatRequest(message="hello", user_id=17, project_id=9),
        )

    assert exc.value.status_code == 400


def test_project_binding_rejects_cross_project_access() -> None:
    principal = RequestPrincipal(
        user_id="local-user",
        email="user@example.com",
        auth_source="sso",
        access_mode="platform",
        platform_user_id=17,
        platform_project_id=9,
    )

    assert require_bound_platform_project(_request(principal), 9) == (17, 9)
    with pytest.raises(HTTPException) as exc:
        require_bound_platform_project(_request(principal), 10)
    assert exc.value.status_code == 403
