"""Regression for the legacy confirmation endpoint's background action pipeline."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks

from app.routers.chat import confirmation, routes
from app.routers.chat.models import AgentStep, ConfirmActionRequest
from app.services.llm.structured_response import (
    LLMAction,
    LLMReply,
    LLMStructuredResponse,
)


@pytest.fixture(autouse=True)
def isolated_pending_confirmations() -> Iterator[None]:
    confirmation._pending_confirmations.clear()
    yield
    confirmation._pending_confirmations.clear()


def _install_agent(
    monkeypatch: pytest.MonkeyPatch, *, failed: bool = False
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class PlanSessionStub:
        def __init__(self, *, repo: Any, plan_id: int | None) -> None:
            seen["plan_id"] = plan_id

        def refresh(self) -> None:
            seen["refreshed"] = True

        def detach(self) -> None:
            seen["detached"] = True

    class AgentStub:
        def __init__(self, **kwargs: Any) -> None:
            seen["agent_kwargs"] = kwargs

        async def execute_structured(self, structured: LLMStructuredResponse) -> Any:
            seen["structured"] = structured
            steps = [
                AgentStep(
                    action=action,
                    success=not failed,
                    message="done" if not failed else "failed",
                    details={},
                )
                for action in structured.sorted_actions()
            ]
            return SimpleNamespace(success=not failed, steps=steps)

    monkeypatch.setattr(routes, "PlanSession", PlanSessionStub)
    monkeypatch.setattr(routes, "get_structured_chat_agent_cls", lambda: AgentStub)
    monkeypatch.setattr(routes, "_derive_conversation_id", lambda _: "conv_test")
    return seen


async def test_confirmation_schedules_structured_actions_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_agent(monkeypatch)
    actions = [
        LLMAction(
            kind="task_operation",
            name="add_task",
            parameters={"name": "second"},
            order=2,
        ),
        LLMAction(
            kind="plan_operation",
            name="create_plan",
            parameters={"name": "first"},
            order=1,
        ),
    ]
    confirmation._store_pending_confirmation(
        "confirm_test",
        "session_test",
        actions,
        LLMStructuredResponse(
            llm_reply=LLMReply(message="Ready to execute."), actions=actions
        ),
        plan_id=7,
        extra_context={"request_id": "req_test"},
    )
    background = BackgroundTasks()
    response = await routes.confirm_pending_action(
        ConfirmActionRequest(confirmation_id="confirm_test", confirmed=True), background
    )
    assert response.success is True and response.executed is True
    assert response.result and response.result["tracking_id"].startswith("act_")
    assert confirmation._get_pending_confirmation("confirm_test") is None
    assert "structured" not in seen  # The endpoint only schedules work.

    await background()
    structured = seen["structured"]
    assert [action.name for action in structured.sorted_actions()] == [
        "create_plan",
        "add_task",
    ]
    assert seen["agent_kwargs"]["session_id"] == "session_test"
    assert seen["agent_kwargs"]["extra_context"] == {"request_id": "req_test"}
    assert seen["plan_id"] == 7 and seen["refreshed"] is True


async def test_confirmed_action_failure_is_reported_as_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _install_agent(monkeypatch, failed=True)
    actions = [LLMAction(kind="task_operation", name="delete_task", order=1)]
    with caplog.at_level(logging.INFO, logger=routes.logger.name):
        await routes._execute_confirmed_actions(
            tracking_id="act_failed",
            session_id="session_test",
            actions=actions,
            plan_id=None,
            extra_context={},
        )
    assert seen["structured"].actions[0].name == "delete_task"
    assert (
        "[CONFIRMATION] Actions failed: act_failed failed=['delete_task']"
        in caplog.text
    )
    assert "[CONFIRMATION] Actions completed: act_failed" not in caplog.text


async def test_cancelled_confirmation_never_schedules_actions() -> None:
    actions = [LLMAction(kind="plan_operation", name="delete_plan", order=1)]
    confirmation._store_pending_confirmation(
        "confirm_cancel", "session_test", actions, None
    )
    background = BackgroundTasks()
    response = await routes.confirm_pending_action(
        ConfirmActionRequest(confirmation_id="confirm_cancel", confirmed=False),
        background,
    )
    assert response.success is True and response.executed is False
    assert background.tasks == []
    assert confirmation._get_pending_confirmation("confirm_cancel") is None
