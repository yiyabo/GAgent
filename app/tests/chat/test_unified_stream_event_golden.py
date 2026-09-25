"""Golden SSE event-sequence lock for ``process_unified_stream``.

``StructuredChatAgent.process_unified_stream`` (1856 lines) is split into phase
helpers by W5c.  These tests freeze the *observable* SSE contract of every
branch/phase before the split — event type, order and the key payload fields —
so any timing, ordering or payload drift introduced by the phase cut fails here.

Covered phases (design/2026-09-24-backend-godfiles-refactor-plan.md §4.8):
routing preamble, full-plan delegation (both delegating branches), direct image
reuse, the rerun/deep-think job creation, the tool/progress callback machine
(through scripted deep-think callbacks), instantiation via the
``chat_routes.DeepThinkAgent`` compat bridge, and the finalize/drain loop
(``final`` event, DB save and the error path).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from app.routers import chat_routes
from app.routers.chat import agent as agent_module
from app.routers.chat.models import AgentResult, AgentStep
from app.routers.chat.request_routing import RequestRoutingDecision, RequestTierProfile
from app.routers.chat_routes import StructuredChatAgent
from app.services.deep_think_agent import DeepThinkResult, ThinkingStep
from app.services.llm.llm_service import LLMProviderError
from app.services.plans.plan_models import PlanNode, PlanTree


class _StubRepo:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self._tree


class _StubPlanSession:
    def __init__(self, *, plan_id: int, tree: PlanTree) -> None:
        self.plan_id = plan_id
        self.repo = _StubRepo(tree)


class _JobStub:
    """Records every ``plan_decomposition_jobs`` call made by the stream."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    def create_job(self, **kwargs: Any) -> None:
        self.calls.append(("create_job", kwargs))

    def mark_running(self, job_id: str) -> None:
        self.calls.append(("mark_running", job_id))

    def register_subscriber(self, job_id: str, loop: Any) -> None:
        self.calls.append(("register_subscriber", job_id))

    def register_runtime_controller(self, job_id: str, controller: Any) -> bool:
        self.calls.append(("register_runtime_controller", job_id))
        return False

    def unregister_runtime_controller(self, job_id: str) -> None:
        self.calls.append(("unregister_runtime_controller", job_id))

    def unregister_subscriber(self, job_id: str, queue: Any) -> None:
        self.calls.append(("unregister_subscriber", job_id))

    def mark_success(self, job_id: str, **kwargs: Any) -> None:
        self.calls.append(("mark_success", job_id))

    def mark_failure(self, job_id: str, error: str, **kwargs: Any) -> None:
        self.calls.append(("mark_failure", job_id, error, kwargs))

    def append_log(self, *args: Any, **kwargs: Any) -> None:
        pass

    def attach_plan(self, *args: Any, **kwargs: Any) -> None:
        pass

    def names(self) -> List[str]:
        return [call[0] for call in self.calls]


def _build_agent(
    *,
    session_id: Optional[str] = "sess-golden",
    task_id: int = 66,
    plan_id: int = 34,
    extra_context: Optional[Dict[str, Any]] = None,
) -> StructuredChatAgent:
    node = PlanNode(id=task_id, plan_id=plan_id, name="Task 66", status="pending")
    tree = PlanTree(id=plan_id, title="plan", nodes={task_id: node}, adjacency={None: [task_id]})
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _StubPlanSession(plan_id=plan_id, tree=tree)
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": task_id}
    if extra_context:
        agent.extra_context.update(extra_context)
    agent.history = []
    agent.session_id = session_id
    agent.max_history_messages = 20
    agent.llm_service = object()
    return agent


def _decision(
    message: str,
    *,
    visibility: str = "progress",
    tier: str = "standard",
    intent: str = "chat",
    **overrides: Any,
) -> RequestRoutingDecision:
    payload: Dict[str, Any] = {
        "request_tier": tier,
        "request_route_mode": "auto_deepthink",
        "route_reason_codes": [],
        "manual_deep_think": False,
        "thinking_visibility": visibility,
        "effective_user_message": message,
        "intent_type": intent,
        "subject_resolution": {},
        "brevity_hint": False,
        "explicit_task_ids": [],
        "explicit_task_override": False,
        "full_plan_execution": False,
    }
    payload.update(overrides)
    return RequestRoutingDecision(**payload)


def _profile(tier: str = "standard", intent: str = "chat") -> RequestTierProfile:
    return RequestTierProfile(
        request_tier=tier,
        thinking_budget=10000,
        max_iterations=8,
        available_tools=["file_operations"],
        output_bias="balanced",
        intent_type=intent,
        explicit_task_ids=[],
        explicit_task_override=False,
    )


def _collect(chunks: List[str]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for chunk in chunks:
        text = chunk.strip()
        assert text.startswith("data: "), chunk
        events.append(json.loads(text[len("data: ") :]))
    return events


def _run(agent: StructuredChatAgent, message: str, **kwargs: Any) -> List[Dict[str, Any]]:
    async def _drive() -> List[str]:
        return [chunk async for chunk in agent.process_unified_stream(message, **kwargs)]

    return _collect(asyncio.run(_drive()))


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [str(event.get("type")) for event in events]


async def _fake_execute_tool(name: str, **params: Any) -> Dict[str, Any]:
    return {"success": True, "summary": "stub tool output", "output": "stub"}


class _ThinkingStub:
    """Deep-think stand-in that replays a scripted callback sequence."""

    def __init__(self, script: List[str], **kwargs: Any) -> None:
        self.kwargs = kwargs
        self._script = script

    def pause(self) -> None:
        return

    def resume(self) -> None:
        return

    def skip_step(self) -> None:
        return

    async def think(
        self, user_query: str, context: Optional[Dict[str, Any]] = None, task_context: Any = None
    ) -> DeepThinkResult:
        emitted_steps: List[ThinkingStep] = []
        for name in self._script:
            if name == "thinking_step":
                step = ThinkingStep(
                    iteration=1,
                    thought="first thought",
                    action=None,
                    action_result=None,
                    self_correction=None,
                    display_text=None,
                    kind="reasoning",
                    status="active",
                )
                emitted_steps.append(step)
                await self.kwargs["on_thinking"](step)
            elif name == "thinking_delta":
                await self.kwargs["on_thinking_delta"](1, "thinking...")
            elif name == "tool_start":
                await self.kwargs["on_tool_start"]("file_operations", {"operation": "list", "path": "."})
            elif name == "tool_result":
                await self.kwargs["on_tool_result"](
                    "file_operations", {"success": True, "summary": "listed 3 files"}
                )
            elif name == "final_delta":
                await self.kwargs["on_final_delta"]("Hello ")
                await self.kwargs["on_final_delta"]("world")
        return DeepThinkResult(
            final_answer="Hello world",
            thinking_steps=emitted_steps,
            total_iterations=1,
            tools_used=["file_operations"],
            confidence=1.0,
            thinking_summary="done",
        )


class _ErrorStub:
    def __init__(self, error: Exception, **kwargs: Any) -> None:
        self._error = error

    def pause(self) -> None:
        return

    def resume(self) -> None:
        return

    def skip_step(self) -> None:
        return

    async def think(self, *args: Any, **kwargs: Any) -> DeepThinkResult:
        raise self._error


def _thinking_agent_class(script: List[str]) -> type:
    """Return a real *class* so the ``chat_routes.DeepThinkAgent`` compat bridge picks it."""

    class _ScriptedThinkingAgent(_ThinkingStub):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(script, **kwargs)

    return _ScriptedThinkingAgent


def _error_agent_class(error: Exception) -> type:
    class _ScriptedErrorAgent(_ErrorStub):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(error, **kwargs)

    return _ScriptedErrorAgent


def _patch_runtime(monkeypatch: Any) -> _JobStub:
    jobs = _JobStub()
    monkeypatch.setattr(agent_module, "plan_decomposition_jobs", jobs)
    monkeypatch.setattr(agent_module, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(agent_module, "_persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr(agent_module, "_save_chat_message", lambda *args, **kwargs: None)
    return jobs


# ---------------------------------------------------------------------------
# direct image reuse branch
# ---------------------------------------------------------------------------


def test_image_reuse_branch_emits_single_final_event(tmp_path, monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    image = tmp_path / "completeness_pie_chart.png"
    image.write_bytes(b"\x89PNG\r\n")
    agent = _build_agent(
        extra_context={
            "recent_image_artifacts": [
                {"display_name": image.name, "path": str(image), "source_tool": "code_executor"}
            ]
        }
    )
    decision = _decision("展示图片")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    events = _run(agent, "展示图片")

    assert _types(events) == ["final"]
    payload = events[0]["payload"]
    assert payload["response"] == "这里是刚才那张图片。"
    assert payload["actions"] == []
    assert payload["metadata"]["status"] == "completed"
    assert payload["metadata"]["thinking_display_mode"] == "final_answer"
    assert payload["metadata"]["artifact_gallery"][0]["path"] == str(image)


def test_image_ambiguity_branch_emits_clarification_final_event(tmp_path, monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"\x89PNG\r\n")
    second.write_bytes(b"\x89PNG\r\n")
    agent = _build_agent(
        extra_context={
            "recent_image_artifacts": [
                {"display_name": first.name, "path": str(first)},
                {"display_name": second.name, "path": str(second)},
            ]
        }
    )
    decision = _decision("展示图片")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    events = _run(agent, "展示图片")

    assert _types(events) == ["final"]
    assert "当前会话里有多张图片" in events[0]["payload"]["response"]
    assert events[0]["payload"]["metadata"]["thinking_display_mode"] == "final_answer"


# ---------------------------------------------------------------------------
# deterministic execute / rerun-job branch
# ---------------------------------------------------------------------------


def test_deterministic_execute_branch_event_sequence(monkeypatch) -> None:
    jobs = _patch_runtime(monkeypatch)

    async def _fake_execute_structured(structured: Any) -> AgentResult:
        return AgentResult(
            reply="Task [66] execution status: completed.",
            steps=[
                AgentStep(
                    action=structured.actions[0],
                    success=True,
                    message="done",
                    details={"task_id": 66, "status": "completed"},
                )
            ],
            suggestions=[],
            primary_intent="rerun_task",
            success=True,
        )

    agent = _build_agent()
    decision = _decision(
        "继续执行 Task 66",
        tier="execute",
        intent="execute_task",
        explicit_task_ids=[66],
        explicit_task_override=True,
    )
    agent._resolve_request_routing = lambda _message: (decision, _profile("execute", "execute_task"))
    agent.execute_structured = _fake_execute_structured

    events = _run(agent, "继续执行 Task 66")

    assert _types(events) == ["progress_status", "thinking_step", "thinking_step", "final"]
    progress = events[0]
    assert progress["phase"] == "planning"
    assert progress["status"] == "running"
    assert progress["iteration"] == 0
    assert [event["step"]["status"] for event in events[1:3]] == ["thinking", "done"]
    final = events[3]["payload"]
    assert final["metadata"]["deterministic_execute_shortcut"] is True
    assert final["actions"][0]["name"] == "rerun_task"
    assert final["actions"][0]["parameters"] == {"task_id": 66}
    assert jobs.names() == ["create_job", "register_subscriber"]
    created = [call for call in jobs.calls if call[0] == "create_job"][0][1]
    assert created["job_type"] == "plan_execute"
    assert created["mode"] == "single_task"
    assert created["task_id"] == 66
    assert created["params"] == {
        "session_id": "sess-golden",
        "task_id": 66,
        "mode": "rerun_task",
    }
    assert created["metadata"]["source"] == "deterministic_execute_shortcut"
    assert created["metadata"]["target_task_name"] == "Task 66"


# ---------------------------------------------------------------------------
# full-plan delegation branches
# ---------------------------------------------------------------------------


def _stub_full_plan(agent: StructuredChatAgent) -> Dict[str, Any]:
    seen: Dict[str, Any] = {}

    async def _branch(**kwargs: Any) -> AsyncIterator[str]:
        seen.update(kwargs)
        yield 'data: {"type": "full_plan_start"}\n\n'
        yield 'data: {"type": "full_plan_task_complete"}\n\n'

    agent._run_full_plan_via_executor = _branch
    return seen


def test_routing_full_plan_delegate_streams_executor_events() -> None:
    agent = _build_agent()
    decision = _decision("执行整个计划", tier="execute", intent="execute_task", full_plan_execution=True)
    agent._resolve_request_routing = lambda _message: (decision, _profile("execute", "execute_task"))
    _stub_full_plan(agent)

    events = _run(agent, "执行整个计划")

    assert _types(events) == ["full_plan_start", "full_plan_task_complete"]


def test_context_full_plan_delegate_streams_executor_events() -> None:
    agent = _build_agent(extra_context={"_full_plan_executor_delegate": True})
    decision = _decision("执行整个计划")
    agent._resolve_request_routing = lambda _message: (decision, _profile())
    _stub_full_plan(agent)

    events = _run(agent, "执行整个计划")

    assert _types(events) == ["full_plan_start", "full_plan_task_complete"]


# ---------------------------------------------------------------------------
# deep-think main path
# ---------------------------------------------------------------------------


def test_deep_think_visible_thinking_event_sequence(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        chat_routes,
        "DeepThinkAgent",
        _thinking_agent_class(
            ["thinking_step", "thinking_delta", "tool_start", "tool_result", "final_delta"]
        ),
    )
    agent = _build_agent()
    decision = _decision("查一下目录", visibility="visible")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    events = _run(agent, "查一下目录")

    assert _types(events) == ["control_ack", "thinking_step", "thinking_delta", "delta", "delta", "final"]
    assert events[0]["available"] is False
    assert events[0]["paused"] is False
    assert events[1]["step"]["thought"] == "first thought"
    assert events[1]["step"]["status"] == "active"
    assert events[2] == {"type": "thinking_delta", "iteration": 1, "delta": "thinking..."}
    assert [event["content"] for event in events[3:5]] == ["Hello ", "world"]
    final = events[5]["payload"]
    assert final["response"] == "Hello world"
    assert final["llm_reply"]["message"] == "Hello world"
    assert final["metadata"]["status"] == "completed"
    assert final["metadata"]["thinking_display_mode"] == "final_answer"
    assert final["metadata"]["iterations"] == 1
    assert final["metadata"]["tools_used"] == ["file_operations"]
    assert final["metadata"]["thinking_process"]["steps"][0]["iteration"] == 1


def test_deep_think_progress_event_sequence(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        chat_routes,
        "DeepThinkAgent",
        _thinking_agent_class(["thinking_step", "tool_start", "tool_result", "final_delta"]),
    )
    agent = _build_agent()
    decision = _decision("查一下目录", visibility="progress")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    events = _run(agent, "查一下目录")

    assert _types(events) == [
        "progress_status",
        "control_ack",
        "progress_status",
        "progress_status",
        "progress_status",
        "delta",
        "delta",
        "final",
    ]
    planning = events[0]
    assert (planning["phase"], planning["status"], planning["iteration"]) == ("planning", "active", 0)
    assert events[2]["phase"] == "planning"
    assert events[2]["iteration"] == 1
    gathering = events[3]
    assert (gathering["phase"], gathering["status"], gathering["tool"]) == (
        "gathering",
        "active",
        "file_operations",
    )
    assert gathering["details"] == "."
    synthesizing = events[4]
    assert (synthesizing["phase"], synthesizing["status"]) == ("synthesizing", "completed")
    assert events[7]["payload"]["metadata"]["thinking_display_mode"] == "final_answer"
    assert events[7]["payload"]["metadata"]["thinking_visibility"] == "progress"


def test_deep_think_error_event_payload_and_job_failure(monkeypatch) -> None:
    jobs = _patch_runtime(monkeypatch)
    error = LLMProviderError(
        "provider blew up",
        error_code="rate_limit",
        category="llm_provider",
        provider="qwen",
        retryable=True,
        status_code=429,
    )
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _error_agent_class(error))
    agent = _build_agent()
    decision = _decision("boom")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    events = _run(agent, "boom")

    assert _types(events) == ["progress_status", "control_ack", "error"]
    assert events[2] == {
        "type": "error",
        "message": "provider blew up",
        "error_type": "LLMProviderError",
        "error_code": "rate_limit",
        "category": "llm_provider",
        "provider": "qwen",
        "retryable": True,
        "status_code": 429,
    }
    failure = [call for call in jobs.calls if call[0] == "mark_failure"]
    assert len(failure) == 1
    assert failure[0][2] == "provider blew up"
    assert failure[0][3]["result"]["error_code"] == "rate_limit"
    assert jobs.names().index("mark_failure") < jobs.names().index("unregister_runtime_controller")


# ---------------------------------------------------------------------------
# event_sink + job lifecycle
# ---------------------------------------------------------------------------


def test_event_sink_receives_the_same_payloads_as_sse_lines(monkeypatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        chat_routes,
        "DeepThinkAgent",
        _thinking_agent_class(["thinking_step", "final_delta"]),
    )
    agent = _build_agent()
    decision = _decision("查一下目录", visibility="visible")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    sink: List[Dict[str, Any]] = []

    async def _event_sink(payload: Dict[str, Any]) -> None:
        sink.append(payload)

    events = _run(agent, "查一下目录", event_sink=_event_sink)

    assert [event.get("type") for event in sink] == _types(events)
    assert sink == events


def test_deep_think_job_lifecycle_calls(monkeypatch) -> None:
    jobs = _patch_runtime(monkeypatch)
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _thinking_agent_class(["final_delta"]))
    agent = _build_agent()
    decision = _decision("查一下目录")
    agent._resolve_request_routing = lambda _message: (decision, _profile())

    _run(agent, "查一下目录", run_id="job-42")

    created = [call for call in jobs.calls if call[0] == "create_job"][0][1]
    assert created["job_id"] == "job-42"
    assert created["job_type"] == "chat_deep_think"
    assert created["mode"] == "chat_deep_think"
    assert created["task_id"] is None
    assert created["plan_id"] == 34
    assert created["params"] == {"session_id": "sess-golden"}
    assert created["metadata"] == {
        "session_id": "sess-golden",
        "origin": "chat_deep_think",
        "message_preview": "查一下目录",
    }
    assert jobs.names() == [
        "create_job",
        "mark_running",
        "register_subscriber",
        "register_runtime_controller",
        "mark_success",
        "unregister_runtime_controller",
    ]
