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
    def __init__(
        self,
        operation: str | None = "execute_all",
        answer: str = "计划已准备好，请批准后执行。",
    ):
        self.operation = operation
        self.answer = answer
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
                "answer": self.answer,
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


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("model_selected", [True, False])
@pytest.mark.parametrize(
    ("plan_mode", "first_message"),
    [
        (
            False,
            "我在做内部的梧桐-47 周报复盘。原始数据是周一处理 12 个请求、周二 18 个。"
            "交付要用中文，明确两天合计，写一句样本小的局限。只使用这两项输入，禁止外部检索。"
            "先在聊天里复述目标和限制，我下一条再决定是否创建计划。",
        ),
        (
            True,
            "The weekly counts are Monday 12 and Tuesday 18. Create a Plan? Not yet, discuss first. "
            "Please recap the goal and the two-day limitation; no external sources.",
        ),
    ],
    ids=["decide-next-turn", "not-yet-with-plan-mode-on"],
)
def test_chat_defers_plan_creation_until_next_turn(
    monkeypatch, native, model_selected, plan_mode, first_message
) -> None:
    from app.services.foundation.settings import get_settings

    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", model_selected)
    _patch_runtime(monkeypatch)
    monkeypatch.setattr("app.services.plan_mode_offers.session_offer_settings", lambda _session_id: {})
    activated = []
    monkeypatch.setattr(
        "app.services.plan_mode_offer_acceptance.persist_plan_mode_enabled",
        lambda _session_id, *, enabled: activated.append(enabled),
    )
    agent = _build_agent(extra_context={"owner_id": "alice", "plan_mode": plan_mode})
    monkeypatch.setattr(type(agent.plan_session), "bind", lambda session, plan_id: setattr(session, "plan_id", plan_id), raising=False)
    monkeypatch.setattr(agent_module, "_set_session_plan_id", lambda _session_id, _plan_id: None)
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    agent.extra_context.pop("current_task_id", None)
    provider_type = NativePlanExecutionProvider if native else PlanExecutionProvider
    recap = "梧桐-47：周一 12 件、周二 18 件，共 30 件。用中文，注明样本小，只用这两项数据。"
    first_provider = provider_type("create", answer=recap)
    _patch_agent(monkeypatch, first_provider, {})
    dispatched = []

    async def handler(name, tool_context=None, **params):
        dispatched.append((name, params.get("operation")))
        return {"success": True, "operation": "create", "plan_id": 35, "plan_title": "梧桐-47"}

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    first = _run(agent, first_message)[-1]
    assert first["type"] == "final"
    assert recap in first["payload"]["response"]
    assert "plan_mode_offer" not in first["payload"]["metadata"]
    assert agent.plan_session.plan_id is None
    assert activated == dispatched == []
    assert any("plan_edit_not_requested" in str(message) for message in first_provider.calls[1])

    _patch_agent(monkeypatch, provider_type("create"), {})
    second = _run(agent, "现在请创建计划，用刚才给出的约束。")[-1]
    assert second["type"] == "final"
    assert second["payload"]["metadata"]["plan_contract_enforced"] is True
    assert activated == [True]
    assert dispatched == [("plan_operation", "create")]
    assert agent.plan_session.plan_id == 35


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
        "delegate_task",
    ]
    assert events[-1]["type"] == "final"
