"""Plan contract: explicit plan mode is hard; natural language is a hint."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.routers.chat.request_routing import resolve_request_routing
from app.services.deep_think.gating_plans import (
    _ensure_structured_plan_notice,
    _plan_contract_blocks_final,
    _summarize_structured_plan_outcome,
)
from app.services.deep_think.models import (
    DeepThinkResult,
    structured_plan_contract_failed,
)

MESSAGE = "把能纳入的都纳入，然后制定完整的研究计划吧"


def _stub_agent(*, enforced: bool, create_required: bool = True) -> Any:
    return SimpleNamespace(
        request_profile={
            "plan_create_required": create_required,
            "plan_execute_required": False,
            "plan_execute_after_create_required": False,
            "plan_review_required": False,
            "plan_optimize_required": False,
            "plan_new_requested": False,
            "plan_conflict_requires_confirmation": False,
            "plan_contract_enforced": enforced,
            "route_reason_codes": ["intent_chat"],
        },
        _current_plan_id=lambda: None,
        _current_plan_title=lambda: None,
        _plan_contract_flags=lambda: {
            "create_required": create_required,
            "execute_required": False,
            "execute_after_create_required": False,
            "review_required": False,
            "optimize_required": False,
            "new_requested": False,
            "conflict_requires_confirmation": False,
            "contract_enforced": enforced,
        },
        _collect_plan_operation_events=lambda _steps: [],
    )


def test_explicit_plan_mode_is_detected_separately_from_language_hints() -> None:
    hinted = resolve_request_routing(message=MESSAGE)
    assert hinted.plan_create_required is True
    assert hinted.plan_contract_enforced is False

    explicit = resolve_request_routing(message=MESSAGE, context={"plan_mode": True})
    assert explicit.plan_create_required is True
    assert explicit.plan_contract_enforced is True
    assert "plan_contract_enforced" in explicit.route_reason_codes

    disabled = resolve_request_routing(message=MESSAGE, context={"plan_mode": "off"})
    assert disabled.plan_contract_enforced is False


def test_language_hint_missing_plan_is_soft_not_blocking() -> None:
    outcome = _summarize_structured_plan_outcome(_stub_agent(enforced=False), [], user_query=MESSAGE)
    assert outcome["required"] is True and outcome["satisfied"] is False
    assert outcome["hard"] is False and outcome["soft_missing"] is True
    assert _plan_contract_blocks_final(outcome) is False


def test_explicit_plan_mode_missing_plan_blocks_final() -> None:
    outcome = _summarize_structured_plan_outcome(_stub_agent(enforced=True), [], user_query=MESSAGE)
    assert outcome["hard"] is True and outcome["soft_missing"] is False
    assert _plan_contract_blocks_final(outcome) is True


def test_conflict_confirmation_still_replaces_the_answer_without_failing() -> None:
    outcome = {
        "required": True,
        "satisfied": False,
        "hard": False,
        "state": "confirmation_required",
    }
    assert _plan_contract_blocks_final(outcome) is True


@pytest.mark.parametrize("hard,state,expected", [
    (True, "not_called", True),
    (False, "not_called", False),
    (False, "confirmation_required", False),
    (True, "created", False),
])
def test_terminal_failure_requires_hard_contract(hard: bool, state: str, expected: bool) -> None:
    result = DeepThinkResult(
        "answer", [], 1, [], 0.5, "summary",
        structured_plan_required=True,
        structured_plan_satisfied=state == "created",
        structured_plan_state=state,
        structured_plan_hard=hard,
    )
    assert structured_plan_contract_failed(result) is expected


def test_soft_missing_replaces_technical_message_with_actionable_notice() -> None:
    outcome = {
        "required": True,
        "satisfied": False,
        "hard": False,
        "soft_missing": True,
        "state": "not_called",
        "message": "The requested structured plan contract was not satisfied: plan_operation was not called.",
    }
    noted = _ensure_structured_plan_notice(None, "这里是研究方案正文。", outcome=outcome, user_query="制定完整的研究计划吧")
    assert "这里是研究方案正文。" in noted
    assert "没有创建可执行的结构化计划树" in noted
    assert "plan_operation was not called" not in noted
