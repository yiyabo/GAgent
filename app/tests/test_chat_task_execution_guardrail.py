import asyncio
import json

import pytest

from app.routers.chat.agent import (
    _build_deep_think_task_context,
    _refresh_deep_think_runtime_context,
)
from app.routers.chat.guardrail_handlers import resolve_full_plan_executable_targets
from app.routers.chat.models import AgentResult, AgentStep
from app.routers.chat.request_routing import RequestRoutingDecision
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


def _build_local_manuscript_agent() -> StructuredChatAgent:
    task81 = PlanNode(
        id=81,
        plan_id=68,
        name="任务合并优化-报告生成",
        status="completed",
        instruction="将分散的QC报告任务合并为综合QC报告。",
        path="/81",
        metadata={"dependencies": [3]},
    )
    task66 = PlanNode(
        id=66,
        plan_id=68,
        name="数据来源与预处理方法描述",
        status="completed",
        instruction="撰写方法部分。",
        path="/66",
        metadata={
            "paper_mode": True,
            "paper_section": "method",
            "paper_context_paths": [
                "methods/data_source_preprocessing.md",
                "methods/not_a_file/",
            ],
        },
        execution_result=json.dumps(
            {
                "artifact_paths": [
                    "/tmp/results/plan68_task66/data_source_preprocessing.md",
                ]
            },
            ensure_ascii=False,
        ),
    )
    task70 = PlanNode(
        id=70,
        plan_id=68,
        name="5.1.3.1 单细胞图谱概览与细胞组成结果撰写",
        status="completed",
        instruction="撰写结果部分。",
        path="/70",
        metadata={
            "paper_mode": True,
            "paper_section": "result",
            "paper_context_paths": [
                "manuscript/results/5.1.3.1_atlas_composition.md",
                "results/1.2_qc/",
            ],
        },
        execution_result=json.dumps(
            {
                "artifact_paths": [
                    "/tmp/results/plan68_task70/qc_summary.csv",
                    "/tmp/results/plan68_task70/figure.png",
                ]
            },
            ensure_ascii=False,
        ),
    )
    tree = PlanTree(
        id=68,
        title="plan68",
        nodes={66: task66, 70: task70, 81: task81},
        adjacency={None: [66, 70, 81]},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=68, tree=tree)
    agent.plan_tree = tree
    agent.extra_context = {
        "current_task_id": 81,
        "request_tier": "execute",
        "intent_type": "execute_task",
    }
    agent._current_user_message = ""
    agent.history = []
    agent.session_id = None
    agent.llm_service = _ExplodingLLM()
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


def test_resolve_full_plan_targets_skips_tasks_blocked_by_running_dependencies():
    tree = PlanTree(
        id=34,
        title="plan",
        nodes={
            1: PlanNode(id=1, plan_id=34, name="Running upstream", status="running"),
            2: PlanNode(id=2, plan_id=34, name="Blocked downstream", status="pending", dependencies=[1]),
            3: PlanNode(id=3, plan_id=34, name="Independent work", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    assert resolve_full_plan_executable_targets(tree) == [3]


def test_full_plan_routing_clears_stale_scope_flags_before_shortcut():
    tree = PlanTree(
        id=34,
        title="plan",
        nodes={
            23: PlanNode(id=23, plan_id=34, name="Final synthesis", status="pending"),
        },
    )
    tree.rebuild_adjacency()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=34, tree=tree)
    agent.extra_context = {
        "explicit_scope_all_blocked": True,
        "pending_scope_task_ids": [99, 100],
    }
    agent.history = []

    decision = RequestRoutingDecision(
        request_tier="execute",
        request_route_mode="manual_deepthink",
        route_reason_codes=["full_plan_execution"],
        manual_deep_think=False,
        thinking_visibility="progress",
        effective_user_message="请执行整个计划",
        intent_type="execute_task",
        capability_floor="tools",
        subject_resolution={},
        brevity_hint=False,
        explicit_task_ids=[],
        explicit_task_override=False,
        full_plan_execution=True,
    )

    agent._update_routing_context(decision)

    assert agent.extra_context.get("current_task_id") == 23
    assert "explicit_scope_all_blocked" not in agent.extra_context
    assert "pending_scope_task_ids" not in agent.extra_context
    structured = agent._build_deterministic_execute_task_structured()
    assert structured is not None


def test_invoke_llm_uses_deterministic_execute_shortcut():
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

    structured = asyncio.run(agent._invoke_llm("继续执行 Task 39"))

    assert len(structured.actions) == 1
    action = structured.actions[0]
    assert action.name == "rerun_task"
    assert action.parameters["task_id"] == 39


def test_deterministic_local_manuscript_shortcut_builds_manuscript_writer_action():
    agent = _build_local_manuscript_agent()

    structured = agent._build_deterministic_local_manuscript_structured(
        "请基于已完成任务整合生成最终论文草稿，不要查文献，也不要重新分析。"
    )

    assert structured is not None
    assert len(structured.actions) == 1
    action = structured.actions[0]
    assert action.kind == "tool_operation"
    assert action.name == "manuscript_writer"
    assert action.parameters["draft_only"] is True
    assert action.parameters["output_path"] == "manuscript/manuscript_draft.md"
    assert "methods/data_source_preprocessing.md" in action.parameters["context_paths"]
    assert "manuscript/results/5.1.3.1_atlas_composition.md" in action.parameters["context_paths"]
    assert "/tmp/results/plan68_task70/qc_summary.csv" in action.parameters["context_paths"]
    assert "results/1.2_qc/" not in action.parameters["context_paths"]
    assert "/tmp/results/plan68_task70/figure.png" not in action.parameters["context_paths"]
    assert action.metadata["origin"] == "local_manuscript_assembly_shortcut"


def test_invoke_llm_uses_deterministic_local_manuscript_shortcut():
    agent = _build_local_manuscript_agent()

    structured = asyncio.run(
        agent._invoke_llm(
            "请基于已完成任务整合生成最终论文草稿，不要查文献，也不要重新分析。"
        )
    )

    assert len(structured.actions) == 1
    action = structured.actions[0]
    assert action.name == "manuscript_writer"
    assert action.parameters["draft_only"] is True
    assert action.parameters["output_path"] == "manuscript/manuscript_draft.md"


def test_process_unified_stream_uses_local_manuscript_shortcut():
    agent = _build_local_manuscript_agent()
    prompt = "请基于已完成任务整合生成最终论文草稿，不要查文献，也不要重新分析。"
    captured: dict[str, LLMStructuredResponse] = {}

    async def _fake_execute_structured(structured: LLMStructuredResponse) -> AgentResult:
        captured["structured"] = structured
        action = structured.actions[0]
        step = AgentStep(
            action=action,
            success=True,
            message="Manuscript writer succeeded. Draft: manuscript/manuscript_draft.md; analysis memo: manuscript/manuscript_draft.md.analysis.md.",
            details={
                "summary": "Manuscript writer succeeded. Draft: manuscript/manuscript_draft.md; analysis memo: manuscript/manuscript_draft.md.analysis.md.",
                "parameters": dict(action.parameters),
                "result": {
                    "tool": "manuscript_writer",
                    "success": True,
                    "output_path": "manuscript/manuscript_draft.md",
                    "analysis_path": "manuscript/manuscript_draft.md.analysis.md",
                    "run_stats": {
                        "source_file_count": 12,
                        "method_sources": 4,
                        "result_sources": 4,
                        "supplementary_sources": 4,
                    },
                },
            },
        )
        return AgentResult(
            reply="我会基于已完成任务的现有产物直接整合本地论文草稿。",
            steps=[step],
            suggestions=[],
            primary_intent="manuscript_writer",
            success=True,
            bound_plan_id=68,
            plan_outline="plan68",
            plan_persisted=False,
            actions_summary=[],
            errors=[],
        )

    agent.execute_structured = _fake_execute_structured  # type: ignore[method-assign]

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in agent.process_unified_stream(prompt):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())

    assert captured["structured"].actions[0].name == "manuscript_writer"
    assert captured["structured"].actions[0].parameters["draft_only"] is True
    final_payload = json.loads(chunks[-1].removeprefix("data: ").strip())
    assert final_payload["type"] == "final"
    assert final_payload["payload"]["metadata"]["shortcut_used"] == "local_manuscript_assembly"
    assert final_payload["payload"]["actions"][0]["name"] == "manuscript_writer"
    assert "BLOCKED_DEPENDENCY" not in final_payload["payload"]["llm_reply"]["message"]
    assert "已完成本地论文草稿整合" in final_payload["payload"]["llm_reply"]["message"]
    assert "`manuscript/manuscript_draft.md`" in final_payload["payload"]["llm_reply"]["message"]
    assert (
        final_payload["payload"]["response"]
        == final_payload["payload"]["metadata"]["final_summary"]
    )
    assert (
        final_payload["payload"]["metadata"]["analysis_text"]
        == final_payload["payload"]["llm_reply"]["message"]
    )


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
