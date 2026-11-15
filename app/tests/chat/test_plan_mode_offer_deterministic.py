"""Deterministic Plan-mode offer: model discretion is no longer the only trigger."""

from __future__ import annotations

import pytest

from app.services.plan_mode_offer_policy import (
    offer_reason,
    offer_text,
    should_offer_plan_mode,
)
from app.tests.chat.test_unified_stream_event_golden import (
    _build_agent,
    _patch_runtime,
    _run,
)

LIVE_QUERY = "调研噬菌体裂解酶最新进展，并制定完整研究计划，之后执行小规模验证。"


@pytest.mark.parametrize(
    "message",
    [
        LIVE_QUERY,
        "调研噬菌体裂解酶的最新研究进展，并制定完整的研究计划。",
        "Research recent endolysin progress and create a complete research plan.",
    ],
)
def test_multi_stage_requests_offer_plan_mode(message: str) -> None:
    assert should_offer_plan_mode(
        message, plan_mode=False, bound_plan_id=None, dismissed=False
    )


@pytest.mark.parametrize(
    "message",
    [
        "介绍一下解聚酶",
        "读一下这个文件的内容",
        "直接继续",
        "Continue directly",
        "执行 task 5",
        "调研现有噬菌体数据，分析候选并验证其中一批。",
    ],
)
def test_simple_or_continuation_turns_do_not_offer(message: str) -> None:
    assert not should_offer_plan_mode(
        message, plan_mode=False, bound_plan_id=None, dismissed=False
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"plan_mode": True, "bound_plan_id": None, "dismissed": False},
        {"plan_mode": False, "bound_plan_id": 12, "dismissed": False},
        {"plan_mode": False, "bound_plan_id": None, "dismissed": True},
    ],
)
def test_offer_is_suppressed_by_state(kwargs: dict) -> None:
    assert not should_offer_plan_mode(LIVE_QUERY, **kwargs)


def test_reason_and_text_are_localized() -> None:
    assert offer_text(LIVE_QUERY).startswith("这个请求包含多阶段")
    assert offer_text("Research and run a validation").startswith("This request spans")
    assert "多阶段" in offer_reason(LIVE_QUERY)


def test_offer_returns_before_any_model_call(monkeypatch) -> None:
    _patch_runtime(monkeypatch)

    calls = []

    def fake_settings(_session_id):
        calls.append("settings")
        return {}

    monkeypatch.setattr(
        "app.services.plan_mode_offers.session_offer_settings", fake_settings
    )
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None

    events = _run(agent, LIVE_QUERY)

    assert calls == ["settings"]
    final = events[-1]
    assert final["type"] == "final"
    metadata = final["payload"]["metadata"]
    offer = metadata["plan_mode_offer"]
    assert offer["status"] == "pending"
    assert offer["query"] == LIVE_QUERY
    assert metadata["plan_creation_state"] == "offer"
    assert "plan_id" not in metadata or metadata["plan_id"] is None
    assert "background_job_id" not in metadata
    assert "Plan" in final["payload"]["response"]


def test_dismissed_session_is_not_offered_again(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        "app.services.plan_mode_offers.session_offer_settings",
        lambda _session_id: {"plan_offer_dismissed": True},
    )
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None

    events = _run(agent, LIVE_QUERY)

    assert "plan_mode_offer" not in events[-1]["payload"]["metadata"]
