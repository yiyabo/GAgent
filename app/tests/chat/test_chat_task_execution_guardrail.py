import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.routers.chat.agent import (
    _build_deep_think_task_context,
    _refresh_deep_think_runtime_context,
)
from app.routers.chat.action_handlers import _prepare_rerun_task_execution
from app.routers.chat.guardrail_handlers import resolve_full_plan_executable_targets
from app.routers.chat.models import AgentResult, AgentStep
from app.routers.chat.request_routing import RequestRoutingDecision
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse
from app.services.plans.decomposition_jobs import plan_decomposition_jobs
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


def _build_explicit_execute_decision(message: str, task_id: int) -> RequestRoutingDecision:
    return RequestRoutingDecision(
        request_tier="execute",
        request_route_mode="manual_deepthink",
        route_reason_codes=["explicit_task_override"],
        manual_deep_think=False,
        thinking_visibility="progress",
        effective_user_message=message,
        intent_type="execute_task",
        subject_resolution={},
        brevity_hint=False,
        explicit_task_ids=[task_id],
        explicit_task_override=True,
        full_plan_execution=False,
    )


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


def test_get_structured_response_uses_deterministic_execute_shortcut_without_llm() -> None:
    task_id = 66
    agent = _build_agent("请继续执行 Task 66", current_task_id=task_id)
    agent.history = []
    agent.llm_service = _ExplodingLLM()
    decision = _build_explicit_execute_decision("请继续执行 Task 66", task_id)
    agent._resolve_request_routing = lambda _message: (decision, None)

    structured = asyncio.run(agent.get_structured_response("请继续执行 Task 66"))

    assert len(structured.actions) == 1
    action = structured.actions[0]
    assert action.kind == "task_operation"
    assert action.name == "rerun_task"
    assert action.parameters == {"task_id": task_id}


def test_process_unified_stream_submits_background_task_without_llm(monkeypatch) -> None:
    from app.routers.chat import plan_job_handoff
    async def enqueue(agent, task_id):
        assert task_id == 66
        return SimpleNamespace(success=True, message="queued", job={"job_id": "task-66-job"})
    monkeypatch.setattr(plan_job_handoff, "enqueue_chat_plan_job", enqueue)
    agent = _build_agent("请继续执行 Task 66", current_task_id=66)
    agent.history = []
    agent.session_id = None
    agent.llm_service = _ExplodingLLM()
    decision = _build_explicit_execute_decision("请继续执行 Task 66", 66)
    agent._resolve_request_routing = lambda _: (decision, None)
    async def collect():
        return [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in agent.process_unified_stream("请继续执行 Task 66")]
    events = asyncio.run(collect())
    assert [event["type"] for event in events] == ["final"]
    assert events[0]["payload"]["metadata"]["background_job_id"] == "task-66-job"


def test_process_unified_stream_persists_background_receipt_for_refresh(monkeypatch) -> None:
    from app.routers.chat import plan_job_handoff
    async def enqueue(agent, task_id):
        return SimpleNamespace(success=True, message="queued", job={"job_id": "task-66-job"})
    monkeypatch.setattr(plan_job_handoff, "enqueue_chat_plan_job", enqueue)
    saved = []
    monkeypatch.setattr("app.routers.chat.agent._persist_runtime_context", lambda _: None)
    monkeypatch.setattr("app.routers.chat.agent._save_chat_message", lambda sid, role, text, metadata=None, **kw: saved.append((text, metadata)))
    agent = _build_agent("请继续执行 Task 66", current_task_id=66)
    agent.history = []
    agent.session_id = "session-1"
    decision = _build_explicit_execute_decision("请继续执行 Task 66", 66)
    agent._resolve_request_routing = lambda _: (decision, None)
    async def collect():
        return [chunk async for chunk in agent.process_unified_stream("请继续执行 Task 66")]
    asyncio.run(collect())
    assert len(saved) == 1 and saved[0][1]["background_job_id"] == "task-66-job"
    assert "已提交后台" in saved[0][0] and "正在排队" in saved[0][0]


def test_prepare_rerun_task_execution_disables_skills_for_explicit_execute_shortcut() -> None:
    task_id = 23
    agent = _build_agent("请执行 Task 23", current_task_id=task_id)
    tree = agent.plan_session.repo.get_plan_tree(34)
    agent.plan_session.ensure = lambda: tree
    agent.plan_session.refresh = lambda: tree
    agent.plan_session.current_tree = lambda: tree
    agent.session_id = "session-1"
    agent.history = []
    agent.max_history_messages = 80
    agent.plan_executor = object()
    agent.extra_context.setdefault("recent_tool_results", [])

    action = LLMAction(
        kind="task_operation",
        name="rerun_task",
        parameters={"task_id": task_id},
        order=1,
        metadata={"origin": "explicit_execute_shortcut"},
    )

    tree, resolved_task_id, config = _prepare_rerun_task_execution(agent, action)

    assert tree.id == 34
    assert resolved_task_id == task_id
    assert config.enable_skills is False
    assert config.skill_trace_enabled is False
    assert config.session_context["explicit_execute_shortcut"] is True


@pytest.mark.asyncio
async def test_rerun_task_submission_does_not_block_event_loop(monkeypatch) -> None:
    from app.routers.chat import plan_job_handoff
    agent = _build_agent("执行任务23", current_task_id=23)
    agent.plan_executor = SimpleNamespace(execute_task=lambda *a, **k: pytest.fail("inline execution"))
    def submit(agent, action, target):
        assert target == 23
        time.sleep(.05)
        return AgentStep(action=action, success=True, message="queued", details={"background_submitted": True})
    monkeypatch.setattr(plan_job_handoff, "submit_plan_action", submit)
    action = LLMAction(kind="task_operation", name="rerun_task", parameters={"task_id": 23}, order=1)
    pending = asyncio.create_task(agent._handle_task_action(action))
    await asyncio.sleep(.01)
    assert not pending.done()
    step = await pending
    assert step.success and step.details["background_submitted"]


def test_plan_task_thinking_is_not_relayed_into_chat(monkeypatch) -> None:
    from app.routers.chat import plan_job_handoff
    async def enqueue(agent, target):
        return SimpleNamespace(success=True, message="queued", job={"job_id": "isolated-plan-job"})
    monkeypatch.setattr(plan_job_handoff, "enqueue_chat_plan_job", enqueue)
    agent = _build_agent("执行任务66", current_task_id=66)
    agent.history = []
    agent.session_id = None
    decision = _build_explicit_execute_decision("执行任务66", 66)
    agent._resolve_request_routing = lambda _: (decision, None)
    async def collect():
        return [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in agent.process_unified_stream("执行任务66")]
    events = asyncio.run(collect())
    assert [event["type"] for event in events] == ["final"]
    assert events[0]["payload"]["metadata"]["background_job_id"] == "isolated-plan-job"


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
        subject_resolution={},
        brevity_hint=False,
        explicit_task_ids=[],
        explicit_task_override=False,
        full_plan_execution=True,
    )

    agent._update_routing_context(decision)

    # With the new PlanExecutor delegation, full_plan_execution sets
    # _full_plan_executor_delegate instead of explicit_task_override.
    # current_task_id / task_id / pending_scope_task_ids are NOT set.
    assert agent.extra_context.get("_full_plan_executor_delegate") is True
    assert "explicit_scope_all_blocked" not in agent.extra_context
    assert "pending_scope_task_ids" not in agent.extra_context
    assert "explicit_task_override" not in agent.extra_context
    assert "explicit_task_ids" not in agent.extra_context


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
