import json

import pytest

from app.routers.chat.agent import (
    _build_deep_think_task_context,
    _refresh_deep_think_runtime_context,
)
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse
from app.services.plans.plan_models import PlanNode, PlanTree


class _DummyRepo:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self._tree


class _DummyPlanSession:
    def __init__(self, *, plan_id: int, tree: PlanTree) -> None:
        self.plan_id = plan_id
        self.repo = _DummyRepo(tree)


def _build_agent(user_message: str, *, current_task_id: int = 23) -> StructuredChatAgent:
    node = PlanNode(
        id=current_task_id,
        plan_id=34,
        name="",
        status="pending",
    )
    tree = PlanTree(
        id=34,
        title="plan",
        nodes={current_task_id: node},
        adjacency={None: [current_task_id]},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=34, tree=tree)
    agent.extra_context = {"current_task_id": current_task_id}
    agent._current_user_message = user_message
    return agent


class _ExplodingLLM:
    async def chat_async(self, *args, **kwargs):
        raise AssertionError("LLM should not be called for deterministic execute shortcut")


class _DummyDeepThink:
    def __init__(self) -> None:
        self.request_profile = {}


def test_followthrough_guardrail_injects_rerun_action_for_execute_intent():
    agent = _build_agent("pleaseexecutetask 23")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message=", . "),
        actions=[],
    )

    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert result.actions == []


def test_followthrough_guardrail_keeps_status_query_without_promise():
    agent = _build_agent("completed？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="taskmedium. "),
        actions=[],
    )

    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert result.actions == []


def test_followthrough_guardrail_executes_when_reply_promises_start():
    agent = _build_agent("completed？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message=", executetask. "),
        actions=[],
    )

    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert result.actions == []


def test_followthrough_guardrail_replaces_verify_only_plan_for_execute_request():
    agent = _build_agent("继续执行 Task 66", current_task_id=66)
    agent.extra_context.update(
        {
            "request_tier": "execute",
            "intent_type": "execute_task",
            "explicit_task_override": True,
            "explicit_task_ids": [66],
            "followthrough_guardrail_enabled": True,
        }
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="先验证当前任务状态。"),
        actions=[
            LLMAction(
                kind="task_operation",
                name="verify_task",
                parameters={"task_id": 66},
                order=1,
            )
        ],
    )
    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.kind == "task_operation"
    assert action.name == "rerun_task"
    assert action.parameters == {"task_id": 66}


def test_deterministic_execute_shortcut_builds_rerun_action():
    agent = _build_agent("继续执行 Task 39", current_task_id=39)
    agent.extra_context.update(
        {
            "request_tier": "execute",
            "intent_type": "execute_task",
            "explicit_task_override": True,
            "pending_scope_task_ids": [40],
        }
    )

    structured = agent._build_deterministic_execute_task_structured()

    assert structured is not None
    assert len(structured.actions) == 1
    action = structured.actions[0]
    assert action.kind == "task_operation"
    assert action.name == "rerun_task"
    assert action.parameters == {"task_id": 39}
    assert action.metadata["origin"] == "explicit_execute_shortcut"


@pytest.mark.asyncio
async def test_invoke_llm_uses_deterministic_execute_shortcut():
    agent = _build_agent("继续执行 Task 39", current_task_id=39)
    agent.extra_context.update(
        {
            "request_tier": "execute",
            "intent_type": "execute_task",
            "explicit_task_override": True,
            "pending_scope_task_ids": [40],
        }
    )
    agent.llm_service = _ExplodingLLM()

    structured = await agent._invoke_llm("继续执行 Task 39")

    assert len(structured.actions) == 1
    action = structured.actions[0]
    assert action.name == "rerun_task"
    assert action.parameters["task_id"] == 39


def test_refresh_deep_think_runtime_context_updates_bound_task() -> None:
    node39 = PlanNode(
        id=39,
        plan_id=34,
        name="KEGG 通路富集分析执行",
        status="completed",
        instruction="执行 Task 39",
        path="/39",
    )
    node40 = PlanNode(
        id=40,
        plan_id=34,
        name="PPI 网络构建与关键模块识别",
        status="pending",
        instruction="执行 Task 40",
        path="/40",
    )
    tree = PlanTree(
        id=34,
        title="plan",
        nodes={39: node39, 40: node40},
        adjacency={None: [39, 40]},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=34, tree=tree)
    agent.plan_tree = tree
    agent.extra_context = {
        "current_task_id": 39,
        "pending_scope_task_ids": [40],
        "explicit_task_override": True,
        "explicit_task_ids": [39, 40],
    }

    task_context = _build_deep_think_task_context(agent, user_message="继续执行")
    assert task_context is not None
    assert task_context.task_id == 39

    deep_think = _DummyDeepThink()
    agent.extra_context["current_task_id"] = 40
    agent.extra_context["pending_scope_task_ids"] = []

    _refresh_deep_think_runtime_context(
        agent,
        dt_agent=deep_think,
        task_context=task_context,
        user_message="继续执行",
    )

    assert deep_think.request_profile["current_task_id"] == 40
    assert deep_think.request_profile["pending_scope_task_ids"] == []
    assert task_context.task_id == 40
    assert task_context.task_name == "PPI 网络构建与关键模块识别"


def test_build_deep_think_task_context_includes_preceding_scope_outputs() -> None:
    root = PlanNode(
        id=8,
        plan_id=68,
        name="Task 8",
        status="pending",
    )
    node42 = PlanNode(
        id=42,
        plan_id=68,
        name="3.2.3.2 GSEA 分析执行",
        status="completed",
        instruction="执行 GSEA 分析",
        parent_id=8,
        position=1,
        path="/8/42",
        execution_result=json.dumps(
            {
                "status": "completed",
                "artifact_paths": [
                    "/tmp/results/plan68_task42/gsea_results.rds",
                    "/tmp/results/plan68_task42/gsea_summary.json",
                ],
            },
            ensure_ascii=False,
        ),
    )
    node43 = PlanNode(
        id=43,
        plan_id=68,
        name="3.2.3.3 GSEA 结果处理与导出",
        status="pending",
        instruction="处理并导出 GSEA 结果",
        parent_id=8,
        position=2,
        path="/8/43",
    )
    tree = PlanTree(
        id=68,
        title="plan",
        nodes={8: root, 42: node42, 43: node43},
        adjacency={None: [8], 8: [42, 43]},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=68, tree=tree)
    agent.plan_tree = tree
    agent.extra_context = {
        "current_task_id": 43,
        "explicit_task_ids": [8],
        "explicit_task_override": True,
    }

    task_context = _build_deep_think_task_context(agent, user_message="继续执行 Task 8")

    assert task_context is not None
    assert task_context.task_id == 43
    assert task_context.dependency_outputs
    assert task_context.dependency_outputs[0]["task_id"] == 42
    assert task_context.dependency_outputs[0]["relationship"] == "preceding_scope_task"
    assert "/tmp/results/plan68_task42/gsea_summary.json" in task_context.dependency_outputs[0]["artifact_paths"]
