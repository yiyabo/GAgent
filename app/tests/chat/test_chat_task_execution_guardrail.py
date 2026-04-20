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


def test_process_unified_stream_uses_deterministic_execute_shortcut_without_llm() -> None:
    task_id = 66
    agent = _build_agent("请继续执行 Task 66", current_task_id=task_id)
    agent.history = []
    agent.session_id = None
    agent.llm_service = _ExplodingLLM()
    decision = _build_explicit_execute_decision("请继续执行 Task 66", task_id)
    agent._resolve_request_routing = lambda _message: (decision, None)

    async def _fake_execute_structured(structured: LLMStructuredResponse) -> AgentResult:
        action = structured.actions[0]
        assert action.kind == "task_operation"
        assert action.name == "rerun_task"
        assert action.parameters == {"task_id": task_id}
        return AgentResult(
            reply="Task [66] execution status: completed.",
            steps=[
                AgentStep(
                    action=action,
                    success=True,
                    message="Task [66] execution status: completed.",
                    details={"task_id": task_id, "status": "completed"},
                )
            ],
            suggestions=[],
            primary_intent="rerun_task",
            success=True,
        )

    agent.execute_structured = _fake_execute_structured

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in agent.process_unified_stream("请继续执行 Task 66"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())

    first_payload = json.loads(chunks[0].removeprefix("data: ").strip())
    assert first_payload["type"] == "progress_status"
    assert first_payload["phase"] == "planning"
    assert first_payload["status"] == "running"

    payload = json.loads(chunks[-1].removeprefix("data: ").strip())
    assert payload["type"] == "final"
    assert payload["payload"]["metadata"]["deterministic_execute_shortcut"] is True
    assert payload["payload"]["actions"][0]["name"] == "rerun_task"


def test_process_unified_stream_persists_deterministic_execute_response_for_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = 66
    agent = _build_agent("请继续执行 Task 66", current_task_id=task_id)
    agent.history = []
    agent.session_id = "session-1"
    agent.llm_service = _ExplodingLLM()
    decision = _build_explicit_execute_decision("请继续执行 Task 66", task_id)
    agent._resolve_request_routing = lambda _message: (decision, None)

    saved_messages: list[dict] = []

    monkeypatch.setattr("app.routers.chat.agent._persist_runtime_context", lambda _agent: None)

    def _fake_save_chat_message(session_id, role, content, metadata=None, *, owner_id=None):
        saved_messages.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata,
                "owner_id": owner_id,
            }
        )

    monkeypatch.setattr("app.routers.chat.agent._save_chat_message", _fake_save_chat_message)

    async def _fake_execute_structured(structured: LLMStructuredResponse) -> AgentResult:
        action = structured.actions[0]
        return AgentResult(
            reply="",
            steps=[
                AgentStep(
                    action=action,
                    success=True,
                    message="",
                    details={"task_id": task_id, "status": "running"},
                )
            ],
            suggestions=[],
            primary_intent="rerun_task",
            success=True,
        )

    agent.execute_structured = _fake_execute_structured

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in agent.process_unified_stream("请继续执行 Task 66"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    events = [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]

    synthetic_steps = [
        event for event in events
        if event.get("type") == "thinking_step"
        and isinstance(event.get("step"), dict)
        and event["step"].get("iteration") == 0
    ]
    assert [step["step"]["status"] for step in synthetic_steps] == ["thinking", "done"]
    assert synthetic_steps[0]["step"]["display_text"] == "准备任务上下文"
    assert synthetic_steps[1]["step"]["display_text"] == "任务上下文已就绪"

    payload = json.loads(chunks[-1].removeprefix("data: ").strip())
    assert payload["type"] == "final"
    assert payload["payload"]["response"] == "任务 66 已开始执行。"
    assert payload["payload"]["metadata"]["actions"][0]["name"] == "rerun_task"

    assert len(saved_messages) == 1
    saved = saved_messages[0]
    assert saved["session_id"] == "session-1"
    assert saved["role"] == "assistant"
    assert saved["content"] == "任务 66 已开始执行。"
    assert saved["metadata"]["deterministic_execute_shortcut"] is True
    assert saved["metadata"]["status"] == "running"
    assert saved["metadata"]["actions"][0]["parameters"] == {"task_id": task_id}


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
async def test_rerun_task_uses_async_wrapper_without_blocking_event_loop() -> None:
    task_id = 23
    agent = _build_agent("请执行 Task 23", current_task_id=task_id)
    tree = agent.plan_session.repo.get_plan_tree(34)
    agent.plan_session.ensure = lambda: tree
    agent.plan_session.refresh = lambda: tree
    agent.plan_session.current_tree = lambda: tree
    agent.session_id = "session-1"
    agent.history = []
    agent.max_history_messages = 80
    agent.extra_context.setdefault("recent_tool_results", [])

    def _slow_execute_task(plan_id: int, rerun_task_id: int, config=None):
        assert plan_id == 34
        assert rerun_task_id == task_id
        assert config is not None
        time.sleep(0.05)
        return SimpleNamespace(
            status="completed",
            to_dict=lambda: {"status": "completed", "task_id": rerun_task_id},
        )

    agent.plan_executor = SimpleNamespace(execute_task=_slow_execute_task)
    action = LLMAction(
        kind="task_operation",
        name="rerun_task",
        parameters={"task_id": task_id},
        order=1,
    )

    pending = asyncio.create_task(agent._handle_task_action(action))

    await asyncio.sleep(0.01)

    assert pending.done() is False

    step = await pending

    assert step.success is True
    assert step.message == "Task [23] execution status: completed."
    assert step.details["status"] == "completed"
    assert step.details["task_id"] == task_id
    assert step.details["result"] == {"status": "completed", "task_id": task_id}
    assert step.details["job"]["job_type"] == "plan_execute"


def test_process_unified_stream_bridges_rerun_task_thinking_events() -> None:
    task_id = 66
    agent = _build_agent("请继续执行 Task 66", current_task_id=task_id)
    agent.history = []
    agent.session_id = "session-bridge"
    agent.conversation_id = "conversation-bridge"
    agent.llm_service = _ExplodingLLM()
    decision = _build_explicit_execute_decision("请继续执行 Task 66", task_id)
    agent._resolve_request_routing = lambda _message: (decision, None)

    async def _fake_execute_structured(structured: LLMStructuredResponse) -> AgentResult:
        action = structured.actions[0]
        job_id = str(agent.extra_context.get("_rerun_task_execution_job_id") or "")
        assert job_id
        plan_decomposition_jobs.append_log(
            job_id,
            "info",
            "DeepThink step update",
            {
                "sub_type": "thinking_step",
                "step": {
                    "iteration": 1,
                    "status": "running",
                    "display_text": "分析任务目标",
                    "thought": "先检查任务上下文与已有产出。",
                    "kind": "reasoning",
                },
            },
        )
        plan_decomposition_jobs.append_log(
            job_id,
            "info",
            "DeepThink delta update",
            {
                "sub_type": "thinking_delta",
                "iteration": 1,
                "delta": "检查已有证据与文件输出。",
            },
        )
        await asyncio.sleep(0)
        return AgentResult(
            reply="Deterministic execute-task shortcut: task 66.",
            steps=[
                AgentStep(
                    action=action,
                    success=True,
                    message="Task [66] execution status: completed.",
                    details={
                        "status": "completed",
                        "task_id": task_id,
                        "result": {
                            "plan_id": 34,
                            "task_id": task_id,
                            "status": "completed",
                            "content": "Task 66 completed with promoted artifacts.",
                            "metadata": {
                                "thinking_process": {
                                    "status": "completed",
                                    "total_iterations": 1,
                                    "steps": [{"iteration": 1, "display_text": "分析任务目标"}],
                                },
                                "session_artifact_paths": ["raw_files/task_66/report.md"],
                            },
                            "raw_response": json.dumps(
                                {
                                    "status": "completed",
                                    "content": "Task 66 completed with promoted artifacts.",
                                    "metadata": {
                                        "thinking_process": {
                                            "status": "completed",
                                            "total_iterations": 1,
                                            "steps": [{"iteration": 1, "display_text": "分析任务目标"}],
                                        },
                                        "session_artifact_paths": ["raw_files/task_66/report.md"],
                                    },
                                    "session_artifact_paths": ["raw_files/task_66/report.md"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    },
                )
            ],
            suggestions=[],
            primary_intent="rerun_task",
            success=True,
        )

    agent.execute_structured = _fake_execute_structured

    async def _collect() -> list[dict]:
        events: list[dict] = []
        async for chunk in agent.process_unified_stream("请继续执行 Task 66"):
            events.append(json.loads(chunk.removeprefix("data: ").strip()))
        return events

    events = asyncio.run(_collect())

    assert any(event["type"] == "thinking_step" for event in events)
    assert any(event["type"] == "thinking_delta" for event in events)
    final_event = events[-1]
    assert final_event["type"] == "final"
    assert final_event["payload"]["response"] == "Task 66 completed with promoted artifacts."
    assert final_event["payload"]["metadata"]["deterministic_execute_shortcut"] is True
    assert final_event["payload"]["metadata"]["thinking_process"]["total_iterations"] == 1
    assert final_event["payload"]["metadata"]["session_artifact_paths"] == ["raw_files/task_66/report.md"]


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
