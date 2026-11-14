"""Explicit research-plan requests should create a persistent plan tree."""
from __future__ import annotations

import pytest

from app.routers.chat.request_routing import (
    build_request_tier_profile,
    resolve_request_routing,
)

EXACT_USER_MESSAGE = "把能纳入的都纳入，然后制定完整的研究计划吧"


def test_live_research_plan_request_requires_creation_without_execution() -> None:
    decision = resolve_request_routing(message=EXACT_USER_MESSAGE)
    assert decision.plan_create_required is True
    assert decision.plan_execute_required is False
    assert "plan_create_request" in decision.route_reason_codes
    profile = build_request_tier_profile(decision, default_max_iterations=64)
    assert "plan_operation" in profile.available_tools


@pytest.mark.parametrize(
    "message",
    [
        "请制定一份完整的研究计划。",
        "帮我制定可执行的研究计划吧",
        "给我一个完整的研究计划",
        "制定详细研究计划吧",
    ],
)
def test_direct_research_plan_variants_require_creation(message: str) -> None:
    assert resolve_request_routing(message=message).plan_create_required is True


@pytest.mark.parametrize(
    "message",
    [
        "请勿制定完整的研究计划。",
        "不要制定完整的研究计划。",
        "不需要创建完整研究计划。",
        "如何制定完整的研究计划？",
    ],
)
def test_negated_or_explanatory_research_plan_mention_is_not_creation(message: str) -> None:
    assert resolve_request_routing(message=message).plan_create_required is False
