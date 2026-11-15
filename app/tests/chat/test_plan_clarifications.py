"""Plan-mode clarifying questions: contract, state, chat loop and prompts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.routers.chat import agent as agent_module
from app.services import plan_clarifications as clarifications
from app.services.deep_think.plan_mode import plan_mode_tool_allowed
from app.services.deep_think_agent import DeepThinkAgent
from app.tests.chat.test_unified_stream_event_golden import (
    _build_agent,
    _patch_runtime,
    _run,
)

QUESTIONS = [
    {
        "id": "host_range",
        "header": "宿主范围",
        "question": "计划聚焦哪类宿主菌？",
        "options": [
            {"label": "葡萄球菌属", "description": "覆盖更多菌株"},
            {"label": "仅金黄色葡萄球菌 (Recommended)", "description": "最快给出候选"},
            {"label": "其他"},
        ],
    }
]


@pytest.fixture
def store(monkeypatch):
    sessions: dict[str, dict[str, Any]] = {}

    def read(session_id):
        return dict(sessions.get(session_id, {}))

    def update(session_id, updater, owner_id):
        sessions[session_id] = updater(dict(sessions.get(session_id, {})))

    monkeypatch.setattr(clarifications, "read_session_metadata", read)
    monkeypatch.setattr(clarifications, "update_session_metadata", update)
    return sessions


@pytest.fixture(autouse=True)
def enable_model_selection(monkeypatch):
    from app.services.foundation.settings import get_settings

    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", True)


# ---------------------------------------------------------------- contract


def test_questions_put_recommendation_first_and_drop_other() -> None:
    [question] = clarifications.normalize_questions(QUESTIONS)
    labels = [option["label"] for option in question["options"]]
    assert labels == ["仅金黄色葡萄球菌", "葡萄球菌属"]
    assert [option["recommended"] for option in question["options"]] == [True, False]
    assert question["id"] == "host_range"
    assert question["header"] == "宿主范围"
    assert question["multi_select"] is False


def test_first_option_is_recommended_by_default_and_ids_are_repaired() -> None:
    questions = clarifications.normalize_questions(
        [
            {"id": "Bad ID", "header": "A very long header text", "question": "Q1?", "options": ["A", "B", "a"]},
            {"id": "q1", "question": "Q2?", "options": ["X", "Y", "Z", "W", "V"]},
        ]
    )
    assert [q["id"] for q in questions] == ["q1", "q2"]
    assert len(questions[0]["header"]) <= clarifications.HEADER_MAX
    assert [o["label"] for o in questions[0]["options"]] == ["A", "B"]
    assert questions[0]["options"][0]["recommended"] is True
    assert len(questions[1]["options"]) == clarifications.MAX_OPTIONS


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ([], "questions_required"),
        ([{"question": "Q", "options": ["A", "B"]}] * 4, "too_many_questions"),
        ([{"question": "Q", "options": ["A", "Other"]}], "options_required"),
        ([{"question": "", "options": ["A", "B"]}], "invalid_question"),
    ],
)
def test_invalid_questions_are_rejected(raw, code) -> None:
    with pytest.raises(clarifications.ClarificationError) as exc:
        clarifications.normalize_questions(raw)
    assert exc.value.code == code


# ------------------------------------------------------------------- state


def test_issue_persists_pending_state_and_limits_rounds(store) -> None:
    first = clarifications.issue_plan_clarification(
        session_id="s1", owner_id="u", bound_plan_id=None, params={"questions": QUESTIONS}, request_id="r1"
    )
    assert first["success"] is True
    assert first["clarification"]["round"] == 1
    assert clarifications.pending_settings(store["s1"]) == {
        "pending_plan_clarification_id": "r1",
        "waiting_user_input": True,
    }
    clarifications.consume_pending_clarification("s1", {"request_id": "r1", "mode": "answered"})
    assert clarifications.pending_settings(store["s1"]) == {}

    second = clarifications.issue_plan_clarification(
        session_id="s1", owner_id="u", bound_plan_id=None, params={"questions": QUESTIONS}
    )
    assert second["clarification"]["round"] == 2
    third = clarifications.issue_plan_clarification(
        session_id="s1", owner_id="u", bound_plan_id=None, params={"questions": QUESTIONS}
    )
    assert third["success"] is False
    assert third["error"] == "clarification_limit_reached"
    assert "assumptions" in third["summary"]

    # A newly bound Plan starts a fresh budget.
    fresh = clarifications.issue_plan_clarification(
        session_id="s1", owner_id="u", bound_plan_id=7, params={"questions": QUESTIONS}
    )
    assert fresh["success"] is True and fresh["clarification"]["round"] == 1


def test_more_context_refunds_a_round_but_only_for_the_pending_request(store) -> None:
    clarifications.issue_plan_clarification(
        session_id="s1", owner_id=None, bound_plan_id=None, params={"questions": QUESTIONS}, request_id="r1"
    )
    clarifications.consume_pending_clarification("s1", {"request_id": "stale", "mode": "more_context"})
    assert clarifications.rounds_used(store["s1"], None) == 1

    clarifications.issue_plan_clarification(
        session_id="s1", owner_id=None, bound_plan_id=None, params={"questions": QUESTIONS}, request_id="r2"
    )
    clarifications.consume_pending_clarification("s1", {"request_id": "r2", "mode": "more_context"})
    assert clarifications.rounds_used(store["s1"], None) == 1
    assert clarifications.PENDING_KEY not in store["s1"]


def test_invalid_request_and_missing_session_do_not_touch_state(store) -> None:
    bad = clarifications.issue_plan_clarification(
        session_id="s1", owner_id=None, bound_plan_id=None, params={"questions": []}
    )
    assert bad["success"] is False and bad["error"] == "questions_required"
    assert "s1" not in store
    missing = clarifications.issue_plan_clarification(
        session_id=None, owner_id=None, bound_plan_id=None, params={"questions": QUESTIONS}
    )
    assert missing["error"] == "ask_user_session_required"


def test_session_settings_expose_pending_card() -> None:
    from app.routers.chat.session_helpers import _extract_session_settings

    settings = _extract_session_settings(
        {clarifications.PENDING_KEY: {"request_id": "r9"}, "plan_mode": True}
    )
    assert settings == {
        "plan_mode": True,
        "pending_plan_clarification_id": "r9",
        "waiting_user_input": True,
    }


def test_response_is_sanitized() -> None:
    response = clarifications.normalize_clarification_response(
        {
            "request_id": "r1",
            "mode": "recommended",
            "answers": [
                {"id": "host_range", "header": "宿主范围", "selected": ["仅金黄色葡萄球菌", ""], "recommended_used": True},
                "junk",
            ],
        }
    )
    assert response == {
        "request_id": "r1",
        "mode": "recommended",
        "answers": [
            {
                "id": "host_range",
                "header": "宿主范围",
                "question": "",
                "selected": ["仅金黄色葡萄球菌"],
                "other": None,
                "recommended_used": True,
            }
        ],
    }
    assert clarifications.normalize_clarification_response({"request_id": "r", "mode": "hack"}) is None


# ------------------------------------------------------------- tool policy


def test_ask_user_is_a_plan_mode_operation_but_the_tool_handler_refuses() -> None:
    assert plan_mode_tool_allowed("plan_operation", {"operation": "ask_user"}) is True
    from tool_box.tools_impl.plan_tools import plan_operation_handler

    result = asyncio.run(plan_operation_handler("ask_user", questions=QUESTIONS))
    assert result["success"] is False
    assert result["error"] == "ask_user_unavailable"


def test_create_records_assumptions_in_plan_metadata(monkeypatch) -> None:
    from tool_box.tools_impl import plan_tools

    captured: dict[str, Any] = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            plan_tree=SimpleNamespace(id=5, nodes={0: None, 1: None}),
            seeded_tasks=[],
            decomposition=None,
            collected_materials=[],
            decomposition_status="skipped",
            root_task_id=0,
            auto_review=None,
        )

    monkeypatch.setattr(plan_tools, "create_plan_and_generate", fake_create)
    monkeypatch.setattr(plan_tools, "PlanDecomposer", lambda repo=None: object())
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: object())
    result = asyncio.run(
        plan_tools._create_plan(
            "Lysin plan", "goal", [], assumptions=["宿主：金黄色葡萄球菌（推荐默认）", "", "宿主：金黄色葡萄球菌（推荐默认）"]
        )
    )
    assert captured["metadata"] == {
        "created_by": "deepthink_agent",
        "assumptions": ["宿主：金黄色葡萄球菌（推荐默认）"],
    }
    assert result["assumptions"] == ["宿主：金黄色葡萄球菌（推荐默认）"]


# --------------------------------------------------------------- chat loop


class AskProvider:
    def __init__(self):
        self.calls: list[Any] = []

    def next(self, messages):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return {
                "thinking": "The host range decides the whole Plan.",
                "action": {
                    "tool": "plan_operation",
                    "params": {"operation": "ask_user", "context": "检索到多类宿主。", "questions": QUESTIONS},
                },
                "final_answer": None,
            }
        return {
            "thinking": "Answer directly.",
            "action": None,
            "final_answer": {"answer": "已按最佳判断回答。", "confidence": 0.8},
        }

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next(kwargs["messages"]))

    async def chat_async(self, **kwargs):
        return "unused"


class NativeAskProvider(AskProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.next(kwargs["messages"])
        action = value["action"]
        call = (
            NativeToolCall(f"ask-{len(self.calls)}", action["tool"], action["params"])
            if action
            else NativeToolCall(f"final-{len(self.calls)}", "submit_final_answer", value["final_answer"])
        )
        return NativeStreamResult(content=value["thinking"], tool_calls=[call], finish_reason="tool_calls")


def _patch_agent(monkeypatch, provider, captured: dict) -> None:
    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            captured["request_profile"] = request_profile
            super().__init__(**{**kwargs, "llm_client": provider, "request_profile": request_profile})

    monkeypatch.setattr("app.routers.chat_routes.DeepThinkAgent", Agent)


@pytest.mark.parametrize("native", [True, False])
def test_plan_mode_question_ends_turn_with_card(monkeypatch, store, native: bool) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": True, "owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    provider = (NativeAskProvider if native else AskProvider)()
    captured: dict = {}
    _patch_agent(monkeypatch, provider, captured)
    executed = []

    async def handler(name, tool_context=None, **params):
        executed.append((name, params))
        return {"success": True}

    monkeypatch.setattr(agent_module, "execute_tool", handler)
    events = _run(agent, "帮我制定裂解酶挖掘计划")

    assert executed == []
    assert len(provider.calls) == 1
    assert captured["request_profile"]["plan_clarification_rounds_left"] == 2
    final = events[-1]
    assert final["type"] == "final"
    metadata = final["payload"]["metadata"]
    card = metadata["plan_clarification"]
    assert card["status"] == "pending" and card["round"] == 1
    assert card["questions"][0]["options"][0]["label"] == "仅金黄色葡萄球菌"
    assert "plan_id" not in metadata or metadata.get("plan_id") is None
    assert "宿主范围" in final["payload"]["response"]
    assert store["sess-golden"][clarifications.PENDING_KEY]["request_id"] == card["request_id"]


@pytest.mark.parametrize("native", [True, False])
def test_question_is_unavailable_outside_plan_mode(monkeypatch, store, native: bool) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(extra_context={"plan_mode": False})
    provider = (NativeAskProvider if native else AskProvider)()
    _patch_agent(monkeypatch, provider, {})
    events = _run(agent, "解释一下裂解酶的结构域")

    assert len(provider.calls) == 2
    assert any("ask_user_unavailable" in str(message) for message in provider.calls[1])
    metadata = events[-1]["payload"]["metadata"]
    assert "plan_clarification" not in metadata
    assert store == {}


def test_answer_turn_receives_binding_answers_in_prompt(monkeypatch, store) -> None:
    _patch_runtime(monkeypatch)
    agent = _build_agent(
        extra_context={
            "plan_mode": True,
            "plan_clarification_response": {
                "request_id": "r1",
                "mode": "recommended",
                "answers": [{"id": "host_range", "header": "宿主范围", "selected": ["仅金黄色葡萄球菌"], "recommended_used": True}],
            },
        }
    )
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    provider = NativeAskProvider()
    provider.calls.append("skip-question")  # go straight to the final answer
    captured: dict = {}
    _patch_agent(monkeypatch, provider, captured)
    _run(agent, "我对规划问题的回答：宿主范围 → 仅金黄色葡萄球菌（推荐）")

    profile = captured["request_profile"]
    assert profile["plan_clarification_response"]["mode"] == "recommended"
    system_prompt = str(provider.calls[1][0]["content"])
    assert "answers your clarification questions" in system_prompt
    assert "宿主范围" in system_prompt and "assumptions" in system_prompt


def test_requirement_block_guides_questions_only_in_plan_mode() -> None:
    from app.services.deep_think.prompts import _build_structured_plan_requirement_block

    def block(profile):
        fake = SimpleNamespace(
            request_profile=profile,
            _plan_contract_flags=lambda: {
                "create_required": profile.get("plan_create_required", False),
                "execute_required": False,
                "execute_after_create_required": False,
                "review_required": False,
                "optimize_required": False,
                "new_requested": False,
                "conflict_requires_confirmation": False,
                "contract_enforced": profile.get("plan_contract_enforced", False),
            },
            _current_plan_id=lambda: None,
        )
        return _build_structured_plan_requirement_block(cast(DeepThinkAgent, fake))

    plan_mode = block({"plan_contract_enforced": True, "plan_create_required": True, "plan_clarification_rounds_left": 2})
    assert "operation=ask_user" in plan_mode
    assert "ask_user first and stop" in plan_mode
    exhausted = block({"plan_contract_enforced": True, "plan_create_required": True, "plan_clarification_rounds_left": 0})
    assert "Do not call ask_user again" in exhausted
    assert "ask_user first and stop" not in exhausted
    normal = block({"plan_create_required": True})
    assert "ask_user" not in normal


def test_run_message_records_answer_and_clears_waiting(monkeypatch, store) -> None:
    from app.routers.chat.models import ChatRequest
    from app.routers.chat.run_routes import _consume_plan_clarification

    clarifications.issue_plan_clarification(
        session_id="s1", owner_id="u", bound_plan_id=None, params={"questions": QUESTIONS}, request_id="r1"
    )
    request = ChatRequest(
        message="回答",
        session_id="s1",
        context={"plan_clarification_response": {"request_id": "r1", "mode": "answered", "answers": []}},
    )
    metadata: dict[str, Any] = {}
    _consume_plan_clarification(request, metadata, session_id="s1", owner_id="u")
    assert metadata["plan_clarification_response"]["request_id"] == "r1"
    assert clarifications.pending_settings(store["s1"]) == {}


def test_result_projection_requires_questions() -> None:
    assert clarifications.plan_clarification_from_result(SimpleNamespace(plan_clarification=None)) == {}
    projected = clarifications.plan_clarification_from_result(
        SimpleNamespace(plan_clarification={"request_id": "r1", "questions": [{"id": "q1"}], "round": 1})
    )
    assert projected["plan_clarification"]["request_id"] == "r1"
    assert projected["plan_clarification"]["max_rounds"] == clarifications.MAX_ROUNDS


def test_answer_prompt_separates_explicit_choices_from_defaults() -> None:
    lines = " ".join(
        clarifications.clarification_prompt_lines(
            {
                "request_id": "r1",
                "mode": "recommended",
                "answers": [
                    {"id": "host", "header": "宿主范围", "recommended_used": True},
                    {"id": "depth", "header": "验证深度", "recommended_used": False},
                ],
            }
        )
    )
    assert "chose these explicitly: 验证深度" in lines
    assert "do not list them as assumptions" in lines
    assert "Only these were accepted as your recommended defaults rather than chosen explicitly: 宿主范围." in lines
    answered = " ".join(
        clarifications.clarification_prompt_lines(
            {"request_id": "r1", "mode": "answered", "answers": [{"id": "depth", "header": "验证深度"}]}
        )
    )
    assert "recommended defaults" not in answered


def test_missing_header_falls_back_to_the_question_not_an_option() -> None:
    [topic, plain] = clarifications.normalize_questions(
        [
            {"question": "研究边界：只做计算分析，还是纳入湿实验？", "options": ["纯计算：挖掘+结构域分析", "计算+湿实验规划"]},
            {"question": "Which host should the Plan target first?", "options": ["S. aureus", "C. acnes"]},
        ]
    )
    assert topic["header"] == "研究边界"
    assert plain["header"] == "Which host s"
    assert clarifications.ask_user_questions_schema()["items"]["required"] == ["header", "question", "options"]
