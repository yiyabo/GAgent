from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from app.routers.chat import action_execution
from app.routers import chat_routes
from app.services.request_principal import RequestPrincipal


def _build_request(owner_id: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat/actions/demo",
        "raw_path": b"/chat/actions/demo",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"x-forwarded-user", owner_id.encode("utf-8"))],
        "client": ("testclient", 50000),
        "server": ("test", 80),
        "state": {},
    }
    request = Request(scope)
    request.state.principal = RequestPrincipal(
        user_id=owner_id,
        email=f"{owner_id}@example.com",
        auth_source="test",
    )
    return request


def test_get_action_status_filters_by_request_owner(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fetch_action_run(run_id: str, *, owner_id=None):
        seen["fetch"] = (run_id, owner_id)
        if owner_id == "alice":
            return {
                "id": run_id,
                "status": "completed",
                "plan_id": 7,
                "result": {},
                "errors": [],
                "created_at": "2026-03-25T00:00:00Z",
                "started_at": "2026-03-25T00:00:01Z",
                "finished_at": "2026-03-25T00:00:02Z",
            }
        return None

    monkeypatch.setattr(action_execution, "fetch_action_run", _fetch_action_run)
    monkeypatch.setattr(
        action_execution,
        "_build_action_status_payloads",
        lambda record: ([], []),
    )
    monkeypatch.setattr(
        action_execution.plan_decomposition_jobs,
        "get_job_payload",
        lambda *_args, **_kwargs: None,
    )

    response = asyncio.run(
        action_execution.get_action_status("act_owned", _build_request("alice"))
    )
    assert response.tracking_id == "act_owned"
    assert response.status == "completed"
    assert seen["fetch"] == ("act_owned", "alice")

    try:
        asyncio.run(
            action_execution.get_action_status("act_owned", _build_request("bob"))
        )
    except HTTPException as exc:
        assert exc.status_code == 404
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected HTTPException for mismatched owner")


def test_retry_action_run_reuses_request_owner(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _StructuredStub:
        def sorted_actions(self):
            return []

    class _PlanSessionStub:
        def __init__(self, *, repo, plan_id):
            _ = repo
            self.plan_id = plan_id

        def refresh(self) -> None:
            return None

        def detach(self) -> None:
            self.plan_id = None

    monkeypatch.setattr(
        action_execution,
        "fetch_action_run",
        lambda run_id, *, owner_id=None: {
            "id": run_id,
            "owner_id": owner_id,
            "session_id": "sess-owned",
            "user_message": "retry me",
            "mode": "assistant",
            "plan_id": 11,
            "context": {},
            "history": [],
            "structured_json": "{}",
        }
        if owner_id == "alice"
        else None,
    )
    monkeypatch.setattr(
        action_execution.LLMStructuredResponse,
        "model_validate_json",
        lambda _raw: _StructuredStub(),
    )
    monkeypatch.setattr(action_execution, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(
        action_execution,
        "create_action_run",
        lambda **kwargs: seen.setdefault("create_action_run", kwargs),
    )
    monkeypatch.setattr(
        action_execution.plan_decomposition_jobs,
        "create_job",
        lambda **kwargs: seen.setdefault("create_job", kwargs),
    )
    monkeypatch.setattr(
        action_execution,
        "append_action_log_entry",
        lambda **kwargs: None,
    )

    response = asyncio.run(
        action_execution.retry_action_run(
            "act_owned",
            BackgroundTasks(),
            _build_request("alice"),
        )
    )

    assert response.status == "pending"
    assert seen["create_action_run"]["owner_id"] == "alice"
    assert seen["create_action_run"]["session_id"] == "sess-owned"
    assert seen["create_job"]["owner_id"] == "alice"
    assert seen["create_job"]["session_id"] == "sess-owned"


def test_analysis_only_chat_response_passes_owner_to_action_lookup(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fetch_action_run(run_id: str, *, owner_id=None):
        seen["fetch"] = (run_id, owner_id)
        return {
            "id": run_id,
            "status": "completed",
            "plan_id": 49,
            "created_at": "2026-03-25T00:00:00Z",
            "started_at": "2026-03-25T00:00:01Z",
            "finished_at": "2026-03-25T00:00:02Z",
            "errors": [],
            "result": {
                "tool_results": [
                    {
                        "name": "code_executor",
                        "summary": "done",
                        "parameters": {"task": "fib"},
                        "result": {"success": True},
                    }
                ]
            },
        }

    async def _fake_generate_tool_analysis(**kwargs):
        assert kwargs["tool_results"][0]["name"] == "code_executor"
        return "owner-scoped analysis"

    monkeypatch.setattr(chat_routes, "fetch_action_run", _fetch_action_run)
    monkeypatch.setattr(chat_routes, "_generate_tool_analysis", _fake_generate_tool_analysis)

    response = asyncio.run(
        chat_routes._build_analysis_only_chat_response(
            user_message="Analyze act_owned",
            context={"analysis_only": True, "source_job_id": "act_owned"},
            session_id="sess-owned",
            owner_id="alice",
            llm_provider="qwen",
        )
    )

    assert response is not None
    assert response.response == "owner-scoped analysis"
    assert seen["fetch"] == ("act_owned", "alice")
