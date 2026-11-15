"""Production-loop journeys for the explicit client-driven Plan mode."""

from __future__ import annotations

import json

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.routers import chat_routes
from app.routers.chat import agent as agent_module
from app.services.deep_think_agent import DeepThinkAgent
from app.tests.chat.test_unified_stream_event_golden import (
    _build_agent,
    _patch_runtime,
    _run,
)


@pytest.fixture(autouse=True)
def enable_model_selection(monkeypatch):
    from app.services.foundation.settings import get_settings

    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", True)


class PlanExecutionProvider:
    def __init__(self, operation: str | None = "execute_all"):
        self.operation = operation
        self.calls: list[list[dict]] = []

    def next(self, messages):
        self.calls.append(messages)
        if len(self.calls) == 1 and self.operation:
            return {
                "thinking": "Select the Plan operation.",
                "action": {
                    "tool": "plan_operation",
                    "params": {"operation": self.operation, "plan_id": 34},
                },
                "final_answer": None,
            }
        return {
            "thinking": "Return the planning result.",
            "action": None,
            "final_answer": {
                "answer": "计划已准备好，请批准后执行。",
                "confidence": 0.9,
            },
        }

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next(kwargs["messages"]))

    async def chat_async(self, **kwargs):
        return "Completed the selected operation."


class NativePlanExecutionProvider(PlanExecutionProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.next(kwargs["messages"])
        action = value["action"]
        call = (
            NativeToolCall(f"op_{len(self.calls)}", action["tool"], action["params"])
            if action
            else NativeToolCall(
                f"final_{len(self.calls)}", "submit_final_answer", value["final_answer"]
            )
        )
        return NativeStreamResult(
            content=value["thinking"], tool_calls=[call], finish_reason="tool_calls"
        )


def _patch_agent(monkeypatch, provider, captured: dict) -> None:
    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            captured.update(kwargs)
            super().__init__(
                **{**kwargs, "llm_client": provider, "request_profile": request_profile}
            )

    monkeypatch.setattr(chat_routes, "DeepThinkAgent", Agent)


@pytest.mark.parametrize("native", [True, False])
def test_plan_mode_blocks_execute_operation_in_both_model_loops(
    monkeypatch, native: bool
) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": True, "owner_id": "alice"})
    provider = (NativePlanExecutionProvider if native else PlanExecutionProvider)()
    captured: dict = {}
    _patch_agent(monkeypatch, provider, captured)
    executed = []

    async def handler(name, tool_context=None, **params):
        executed.append((name, params))
        return {
            "success": True,
            "operation": "execute_all",
            "background_submitted": True,
            "job_id": "must-not-start",
        }

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    events = _run(agent, "执行整个计划")

    assert executed == []
    assert len(provider.calls) == 2
    assert "plan_operation" in captured["available_tools"]
    assert any("plan_mode_read_only" in str(message) for message in provider.calls[1])
    assert events[-1]["type"] == "final"
    assert "background_plan_job_id" not in events[-1]["payload"]["metadata"]


@pytest.mark.parametrize("native", [True, False])
def test_plan_mode_still_allows_read_only_plan_lookup(
    monkeypatch, native: bool
) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": True})
    provider = (NativePlanExecutionProvider if native else PlanExecutionProvider)("get")
    _patch_agent(monkeypatch, provider, {})
    operations = []

    async def handler(name, tool_context=None, **params):
        operations.append(params["operation"])
        return {"success": True, "operation": "get", "plan_id": 34, "task_tree": []}

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    events = _run(agent, "读取当前计划状态")

    assert operations == ["get"]
    assert events[-1]["type"] == "final"


@pytest.mark.parametrize("native", [True, False])
def test_plan_mode_allows_structured_plan_creation(monkeypatch, native: bool) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": True})
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    provider = (NativePlanExecutionProvider if native else PlanExecutionProvider)(
        "create"
    )
    _patch_agent(monkeypatch, provider, {})
    created = []

    async def handler(name, tool_context=None, **params):
        created.append(params)
        return {
            "success": True,
            "operation": "create",
            "plan_id": 35,
            "plan_title": "Research plan",
            "message": "Plan created",
        }

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    events = _run(agent, "制定完整研究计划")

    assert [item["operation"] for item in created] == ["create"]
    assert events[-1]["type"] == "final"
    assert "background_plan_job_id" not in events[-1]["payload"]["metadata"]


def test_plan_mode_filters_mutating_tools_from_model_offer(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        "app.routers.chat.request_routing.get_all_tools",
        lambda: [
            "web_search",
            "file_operations",
            "plan_operation",
            "phagescope_data",
            "execute_code",
            "code_executor",
            "bio_tools",
            "delegate_task",
        ],
    )
    agent = _build_agent(extra_context={"plan_mode": True})
    provider = PlanExecutionProvider(None)
    captured: dict = {}
    _patch_agent(monkeypatch, provider, captured)

    events = _run(agent, "先研究并规划")

    assert captured["available_tools"] == [
        "web_search",
        "file_operations",
        "plan_operation",
        "phagescope_data",
    ]
    assert events[-1]["type"] == "final"
