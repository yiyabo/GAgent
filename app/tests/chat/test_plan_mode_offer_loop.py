"""Full chat-loop coverage for Plan-mode offers."""

from __future__ import annotations

import json

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.routers.chat import agent as agent_module
from app.services.deep_think_agent import DeepThinkAgent
from app.services.plan_mode_offers import build_plan_mode_offer
from app.tests.chat.test_unified_stream_event_golden import (
    _build_agent,
    _patch_runtime,
    _run,
)


@pytest.fixture(autouse=True)
def enable_model_selection(monkeypatch):
    from app.services.foundation.settings import get_settings

    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", True)


class OfferProvider:
    def __init__(self):
        self.calls = []

    def next(self, messages):
        self.calls.append(messages)
        return {
            "thinking": "This needs several execution stages, so ask before planning.",
            "action": {
                "tool": "plan_operation",
                "params": {
                    "operation": "offer",
                    "reason": "This needs multi-stage research and execution.",
                    "query": "研究裂解酶并验证候选",
                },
            },
            "final_answer": None,
        }

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next(kwargs["messages"]))

    async def chat_async(self, **kwargs):
        return "unused"


class NativeOfferProvider(OfferProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.next(kwargs["messages"])
        action = value["action"]
        call = NativeToolCall("offer-1", action["tool"], action["params"])
        return NativeStreamResult(
            content=value["thinking"], tool_calls=[call], finish_reason="tool_calls"
        )


@pytest.mark.parametrize("native", [True, False])
def test_offer_finishes_with_pending_card_and_no_execution(
    monkeypatch, native: bool
) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": False})
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    provider = (NativeOfferProvider if native else OfferProvider)()

    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(
                **{**kwargs, "llm_client": provider, "request_profile": request_profile}
            )

    monkeypatch.setattr("app.routers.chat_routes.DeepThinkAgent", Agent)
    calls = []

    async def handler(name, tool_context=None, **params):
        calls.append((name, params))
        return build_plan_mode_offer(
            reason=params["reason"],
            query=params["query"],
            settings={},
            bound_plan_id=None,
            offer_id="offer-loop",
        )

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    events = _run(agent, "研究裂解酶并验证候选")

    assert [call[1]["operation"] for call in calls] == ["offer"]
    assert len(provider.calls) == 1
    final = events[-1]
    assert final["type"] == "final"
    offer = final["payload"]["metadata"]["plan_mode_offer"]
    assert offer == {
        "offer_id": "offer-loop",
        "status": "pending",
        "reason": "This needs multi-stage research and execution.",
        "query": "研究裂解酶并验证候选",
    }
    assert "background_job_id" not in final["payload"]["metadata"]
    assert "Plan" in final["payload"]["response"]
