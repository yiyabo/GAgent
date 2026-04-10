from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import BackgroundTasks
from starlette.requests import Request

from app.routers.chat import routes as chat_routes
from app.routers.chat.models import ChatRequest
from app.services.llm.structured_response import LLMReply, LLMStructuredResponse
from app.services.request_principal import RequestPrincipal


def _build_raw_request(owner_id: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat/message",
        "raw_path": b"/chat/message",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("test", 80),
        "state": {},
    }
    raw_request = Request(scope)
    raw_request.state.principal = RequestPrincipal(
        user_id=owner_id,
        email=f"{owner_id}@example.com",
        auth_source="test",
    )
    return raw_request


class _PlanSessionStub:
    def __init__(self, *, repo, plan_id):
        _ = repo
        self.plan_id = plan_id

    def refresh(self) -> None:
        return None

    def detach(self) -> None:
        self.plan_id = None


class _AgentStub:
    def __init__(self, **kwargs):
        _ = kwargs

    async def get_structured_response(self, _message: str) -> LLMStructuredResponse:
        return LLMStructuredResponse(
            llm_reply=LLMReply(message="stub reply"),
            actions=[],
        )

    async def execute_structured(self, _structured: LLMStructuredResponse):
        return SimpleNamespace(
            reply="assistant response",
            suggestions=[],
            steps=[],
            primary_intent="assistant",
            success=True,
            errors=[],
            bound_plan_id=None,
            plan_outline=None,
            plan_persisted=False,
            actions_summary=None,
            job_id=None,
            job_type=None,
        )


def test_chat_message_propagates_request_owner_to_session_helpers(
    monkeypatch,
) -> None:
    seen: dict[str, object] = {}

    monkeypatch.setattr(chat_routes, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(chat_routes, "get_structured_chat_agent_cls", lambda: _AgentStub)

    def _resolve_plan_binding(session_id, requested_plan_id, *, owner_id=None):
        seen["binding"] = (session_id, requested_plan_id, owner_id)
        return requested_plan_id

    def _save_chat_message(session_id, role, content, metadata=None, *, owner_id=None):
        seen.setdefault("saved_messages", []).append((session_id, role, content, owner_id))
        _ = metadata

    def _get_session_settings(session_id, *, owner_id=None):
        seen["settings"] = (session_id, owner_id)
        return {}

    def _get_session_current_task(session_id, *, owner_id=None):
        seen["current_task"] = (session_id, owner_id)
        return None

    def _load_session_runtime_context(session_id, *, owner_id=None):
        seen["runtime_context"] = (session_id, owner_id)
        return {}

    def _save_assistant_response(session_id, response, *, owner_id=None):
        seen["assistant"] = (session_id, owner_id, response.response)
        return response

    def _set_session_plan_id(session_id, plan_id, *, owner_id=None):
        seen["set_plan"] = (session_id, plan_id, owner_id)

    monkeypatch.setattr(chat_routes, "_resolve_plan_binding", _resolve_plan_binding)
    monkeypatch.setattr(chat_routes, "_save_chat_message", _save_chat_message)
    monkeypatch.setattr(chat_routes, "_get_session_settings", _get_session_settings)
    monkeypatch.setattr(chat_routes, "_get_session_current_task", _get_session_current_task)
    monkeypatch.setattr(chat_routes, "_load_session_runtime_context", _load_session_runtime_context)
    monkeypatch.setattr(chat_routes, "_save_assistant_response", _save_assistant_response)
    monkeypatch.setattr(chat_routes, "_set_session_plan_id", _set_session_plan_id)

    payload = ChatRequest(message="hello", session_id="sess-owner-1")
    raw_request = _build_raw_request("alice")

    response = asyncio.run(
        chat_routes.chat_message(payload, BackgroundTasks(), raw_request)
    )

    assert response.response == "assistant response"
    assert seen["binding"] == ("sess-owner-1", None, "alice")
    assert seen["settings"] == ("sess-owner-1", "alice")
    assert seen["runtime_context"] == ("sess-owner-1", "alice")
    assert seen["current_task"] == ("sess-owner-1", "alice")
    assert seen["set_plan"] == ("sess-owner-1", None, "alice")
    assert seen["saved_messages"] == [("sess-owner-1", "user", "hello", "alice")]
    assert seen["assistant"] == ("sess-owner-1", "alice", "assistant response")
