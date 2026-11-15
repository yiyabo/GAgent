"""Plan-mode offers suggest, never execute or silently change permissions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.routers.chat.model_selection import resolve_model_routing
from app.routers.chat.request_routing import resolve_request_routing
from app.services.plan_mode_offers import (
    build_plan_mode_offer,
    plan_mode_offer_from_result,
)


def test_offer_is_a_pending_receipt_with_original_request() -> None:
    result = build_plan_mode_offer(
        reason="Multi-stage research and execution",
        query="研究裂解酶并验证候选",
        settings={},
        bound_plan_id=None,
        offer_id="offer-test",
    )
    assert result["success"] is True
    assert result["operation"] == "offer"
    assert result["offer"] == {
        "offer_id": "offer-test",
        "status": "pending",
        "reason": "Multi-stage research and execution",
        "query": "研究裂解酶并验证候选",
    }


@pytest.mark.parametrize(
    ("settings", "bound_plan_id", "suppressed"),
    [
        ({"plan_offer_dismissed": True}, None, "dismissed"),
        ({"plan_mode": True}, None, "already_enabled"),
        ({}, 12, "already_bound"),
    ],
)
def test_offer_is_suppressed_without_another_prompt(
    settings: dict, bound_plan_id: int | None, suppressed: str
) -> None:
    result = build_plan_mode_offer(
        reason="Complex task",
        query="do work",
        settings=settings,
        bound_plan_id=bound_plan_id,
    )
    assert result["success"] is True
    assert result["suppressed"] == suppressed
    assert "offer" not in result


def test_offer_is_lifted_into_final_message_metadata() -> None:
    offer = build_plan_mode_offer(
        reason="Complex task",
        query="do work",
        settings={},
        bound_plan_id=None,
        offer_id="offer-meta",
    )
    metadata = plan_mode_offer_from_result(
        SimpleNamespace(plan_mode_offer=offer["offer"])
    )
    assert metadata["plan_mode_offer"]["offer_id"] == "offer-meta"
    assert metadata["plan_mode_offer"]["status"] == "pending"


def test_offer_handoff_stops_before_execution() -> None:
    from app.services.deep_think.background_handoff import conclude_background_plan

    offer = build_plan_mode_offer(
        reason="Complex task",
        query="do work",
        settings={},
        bound_plan_id=None,
        offer_id="offer-stop",
    )
    agent = SimpleNamespace(
        _chat_plan_handoff_result=offer,
        _chat_plan_handoff_query="do work",
        on_thinking=None,
        on_final_delta=None,
        _stream_final_answer=None,
        _summarize_structured_plan_outcome=lambda _steps, user_query: {
            "required": False,
            "satisfied": False,
        },
    )

    async def run() -> Any:
        return await conclude_background_plan(agent, [], [], 1)

    import asyncio

    result = asyncio.run(run())
    assert result.termination_reason == "plan_mode_offer"
    assert result.structured_plan_required is False
    assert "Plan" in result.final_answer


@pytest.mark.parametrize("model_selection", [True, False])
def test_accepted_offer_requires_structured_plan_creation(
    model_selection: bool,
) -> None:
    context = {
        "plan_mode": True,
        "plan_offer_accepted": True,
        "plan_offer_id": "offer-accept",
    }
    decision = (
        resolve_model_routing(message="研究并验证候选", context=context)
        if model_selection
        else resolve_request_routing(message="研究并验证候选", context=context)
    )
    assert decision.plan_create_required is True
    assert decision.plan_contract_enforced is True
    assert decision.plan_execute_required is False


async def test_plan_operation_offer_uses_the_existing_tool_boundary() -> None:
    from tool_box.tools_impl.plan_tools import plan_operation_handler

    result = await plan_operation_handler(
        "offer",
        reason="Multi-stage research and execution",
        query="研究并验证候选",
        tool_context=SimpleNamespace(session_id=None, plan_id=None, extra={}),
    )
    assert result["operation"] == "offer"
    assert result["offer"]["status"] == "pending"
    assert result["offer"]["query"] == "研究并验证候选"
