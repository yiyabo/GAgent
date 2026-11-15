"""LLM-reviewed Plan offers: recall filter, gate parsing, and chat-loop wiring."""

from __future__ import annotations

import asyncio

import pytest

from app.services.plan_mode_offer_gate import (
    PlanOfferGateDecision,
    _parse_decision,
    is_offer_candidate,
    review_plan_offer,
)
from app.services.plan_mode_offer_policy import offer_state_allows
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
        "帮我制定一份完整的研究计划",
        "请规划一下这个项目",
        "create a complete research plan",
        "Roadmap for the project",
    ],
)
def test_plan_vocabulary_is_a_gate_candidate(message: str) -> None:
    assert is_offer_candidate(message)


@pytest.mark.parametrize(
    "message",
    [
        "介绍一下解聚酶",
        "读一下这个文件的内容",
        "直接继续",
        "Continue directly",
        "执行 task 5",
        "analyze these proteins",
    ],
)
def test_messages_without_plan_vocabulary_skip_the_gate(message: str) -> None:
    assert not is_offer_candidate(message)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"plan_mode": True, "bound_plan_id": None, "dismissed": False},
        {"plan_mode": False, "bound_plan_id": 12, "dismissed": False},
        {"plan_mode": False, "bound_plan_id": None, "dismissed": True},
    ],
)
def test_offer_is_suppressed_by_state(kwargs: dict) -> None:
    assert not offer_state_allows(**kwargs)


def test_parse_decision_accepts_json_and_fences() -> None:
    assert _parse_decision('{"decision":"offer","reason":"explicit"}') == (
        True,
        "explicit",
    )
    assert _parse_decision(
        '```json\n{"decision":"proceed","reason":"background"}\n```'
    ) == (
        False,
        "background",
    )
    assert _parse_decision('prefix {"decision":"offer"} suffix') == (True, "")


@pytest.mark.parametrize(
    "raw",
    ["", "no json", '{"decision":"maybe"}', '{"decision":"offer",'],
)
def test_parse_decision_rejects_invalid_replies(raw: str) -> None:
    with pytest.raises(ValueError):
        _parse_decision(raw)


class _FakeLLM:
    def __init__(
        self, reply: str = "", *, error: Exception | None = None, delay: float = 0.0
    ):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    async def chat_async(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


def test_review_uses_one_bounded_llm_call() -> None:
    llm = _FakeLLM('{"decision":"offer","reason":"用户明确要求制定计划"}')
    decision = asyncio.run(review_plan_offer(LIVE_QUERY, llm))

    assert decision == PlanOfferGateDecision(
        offer=True, reason="用户明确要求制定计划", source="llm"
    )
    assert len(llm.prompts) == 1
    assert LIVE_QUERY in llm.prompts[0]
    assert llm.kwargs[0]["max_tokens"] <= 256
    assert llm.kwargs[0]["retries"] == 0


def test_review_proceeds_for_background_mention() -> None:
    llm = _FakeLLM('{"decision":"proceed","reason":"计划只是引用背景"}')
    decision = asyncio.run(
        review_plan_offer("我们的申请书里写着：下一步计划制定完整的研究方案。", llm)
    )
    assert decision.offer is False
    assert decision.source == "llm"


def test_review_fails_open_when_provider_errors() -> None:
    llm = _FakeLLM(error=RuntimeError("provider down"))
    decision = asyncio.run(review_plan_offer(LIVE_QUERY, llm))
    assert decision == PlanOfferGateDecision(
        offer=False, reason="gate_unavailable", source="fallback"
    )


def test_review_fails_open_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr("app.services.plan_mode_offer_gate.GATE_TIMEOUT_SECONDS", 0.01)
    llm = _FakeLLM('{"decision":"offer"}', delay=0.2)
    decision = asyncio.run(review_plan_offer(LIVE_QUERY, llm))
    assert decision.offer is False
    assert decision.source == "fallback"


def _patch_gate(monkeypatch, decision: PlanOfferGateDecision) -> list[str]:
    calls: list[str] = []

    async def fake(message: str, _llm_service):
        calls.append(message)
        return decision

    monkeypatch.setattr("app.services.plan_mode_offer_gate.review_plan_offer", fake)
    return calls


def test_offer_returns_before_any_agent_loop(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        "app.services.plan_mode_offers.session_offer_settings", lambda _session_id: {}
    )
    calls = _patch_gate(monkeypatch, PlanOfferGateDecision(True, "explicit", "llm"))
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None

    events = _run(agent, LIVE_QUERY)

    assert calls == [LIVE_QUERY]
    assert events[-1]["type"] == "final"
    payload = events[-1]["payload"]
    assert payload["actions"] == []
    metadata = payload["metadata"]
    offer = metadata["plan_mode_offer"]
    assert offer["status"] == "pending"
    assert offer["query"] == LIVE_QUERY
    assert metadata["plan_creation_state"] == "offer"
    assert metadata["plan_offer_gate"] == "llm"
    assert "plan_id" not in metadata or metadata["plan_id"] is None
    assert "background_job_id" not in metadata
    assert "Plan" in payload["response"]


def test_dismissed_session_skips_the_gate_entirely(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        "app.services.plan_mode_offers.session_offer_settings",
        lambda _session_id: {"plan_offer_dismissed": True},
    )
    calls = _patch_gate(monkeypatch, PlanOfferGateDecision(True, "explicit", "llm"))
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None

    events = _run(agent, LIVE_QUERY)

    assert calls == []
    assert "plan_mode_offer" not in events[-1]["payload"]["metadata"]


def test_bound_plan_skips_the_gate_entirely(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    calls = _patch_gate(monkeypatch, PlanOfferGateDecision(True, "explicit", "llm"))
    agent = _build_agent(extra_context={"owner_id": "alice"})

    _run(agent, LIVE_QUERY)

    assert calls == []
