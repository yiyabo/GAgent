"""Regression for the p49 live Plan-mode miss and legacy permission leak."""

from __future__ import annotations

import json

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.routers.chat import agent as agent_module
from app.services.deep_think_agent import DeepThinkAgent
from app.tests.chat.test_unified_stream_event_golden import (
    _build_agent,
    _patch_runtime,
    _run,
)

LIVE_QUERY = "调研噬菌体裂解酶的最新研究进展，并制定完整的研究计划。"


@pytest.fixture(autouse=True)
def legacy_model_selection(monkeypatch):
    from app.services.foundation.settings import get_settings

    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", False)


class LegacyProvider:
    def __init__(self):
        self.calls = []

    def next(self, messages):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return {
                "thinking": "Write a Markdown report instead of a structured Plan.",
                "action": {
                    "tool": "file_operations",
                    "params": {
                        "operation": "write",
                        "path": "results/plan.md",
                        "content": "# Plan",
                    },
                },
                "final_answer": None,
            }
        if len(self.calls) == 2:
            return {
                "thinking": "The write was denied, so create the required structured Plan.",
                "action": {
                    "tool": "plan_operation",
                    "params": {"operation": "create", "title": "Research Plan"},
                },
                "final_answer": None,
            }
        return {
            "thinking": "The structured Plan exists now.",
            "action": None,
            "final_answer": {"answer": "结构化研究计划已创建。", "confidence": 0.9},
        }

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next(kwargs["messages"]))

    async def chat_async(self, **kwargs):
        return "unused"


class NativeLegacyProvider(LegacyProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.next(kwargs["messages"])
        action = value["action"]
        call = (
            NativeToolCall(f"call-{len(self.calls)}", action["tool"], action["params"])
            if action
            else NativeToolCall("final", "submit_final_answer", value["final_answer"])
        )
        return NativeStreamResult(
            content=value["thinking"], tool_calls=[call], finish_reason="tool_calls"
        )


@pytest.mark.parametrize("native", [True, False])
def test_legacy_plan_mode_blocks_markdown_and_requires_structured_create(
    monkeypatch, native: bool
) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": True})
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    provider = (NativeLegacyProvider if native else LegacyProvider)()

    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(
                **{**kwargs, "llm_client": provider, "request_profile": request_profile}
            )

    monkeypatch.setattr("app.routers.chat_routes.DeepThinkAgent", Agent)
    operations = []

    async def handler(name, tool_context=None, **params):
        operation = params.get("operation")
        operations.append((name, operation))
        assert operation != "write", "Markdown write escaped the legacy Plan-mode guard"
        return {
            "success": True,
            "operation": "create",
            "plan_id": 35,
            "plan_title": "Research Plan",
            "message": "Plan created",
        }

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    events = _run(agent, LIVE_QUERY)

    assert operations == [("plan_operation", "create")]
    final = events[-1]
    assert final["type"] == "final"
    assert final["payload"]["metadata"]["plan_creation_state"] == "satisfied"
    assert final["payload"]["metadata"]["plan_id"] == 35
    assert final["payload"]["metadata"]["plan_contract_enforced"] is True
