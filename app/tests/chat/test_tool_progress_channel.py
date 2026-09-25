"""The chat lane's tools must be able to report progress into the turn's stream.

Every tool call a chat turn makes — native function-calling and the prompt /
structured lane alike — is executed by ``action_handlers.handle_tool_action``,
which builds its own ``ToolContext``.  That context carried no ``on_progress``,
so a handler whose work is delegated to an external CLI (``code_executor``'s
qwen/claude lanes through ``delegation_progress``, the local execution lane,
``bio_tools``) reported into nothing: no ``progress_status`` reached the client
for the whole delegation, even though the native lane wires the same callback in
``services/deep_think/dispatch.py``.

These tests pin the wiring: the action lane finds the turn's channel on the
agent, the reports land as the ``progress_status`` payload the frontend already
renders (``type`` / ``phase`` / ``label`` / ``details`` / ``iteration`` /
``tool`` / ``status``), cross-thread delivery still works, a bare agent degrades
to no channel, and a channel that raises never fails the tool.

Note on the turn mode: the stream's ``_emit_progress_status`` is display-mode
gated — only `thinking_visibility == "progress"` turns release every tool's
events, and since a9a3597d production routing returns ``"visible"``.  The one
narrow exception is the delegation lane itself: ``on_tool_progress`` releases
the tools in ``DELEGATION_PROGRESS_TOOLS`` in every mode (pinned by
``test_delegation_progress_visibility.py``).  The end-to-end test below
therefore drives a ``progress``-mode turn so the whole channel — start, result
and the delegation's own reports — is observable in one turn, like the golden
stream tests do; the wiring itself is mode-independent.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.routers import chat_routes
from app.routers.chat import action_handlers
from app.routers.chat import agent as agent_module
from app.routers.chat.action_handlers import (
    _build_chat_tool_context,
    handle_tool_action,
)
from app.routers.chat.models import AgentStep
from app.routers.chat.request_routing import (
    RequestRoutingDecision,
    RequestTierProfile,
)
from app.routers.chat_routes import StructuredChatAgent
from app.services.deep_think_agent import DeepThinkResult
from app.services.llm.structured_response import LLMAction
from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl.delegation_progress import build_delegation_progress

RUN_ID = "run-1"
# The wording the real CLI lane reports (``tools_impl/code_executor.py``).
DELEGATION_STARTED = "Delegating to Qwen Code · lane qwen_primary"
DELEGATION_COMPLETED = "Sub-agent run completed in 12s (2 artifacts)"
STARTED_DETAIL = f"run {RUN_ID} · backend qwen_code"
COMPLETED_DETAIL = f"run {RUN_ID} · 2 artifacts"


# ---------------------------------------------------------------------------
# light agent stub (the shape ``handle_tool_action`` needs from a tool lane)
# ---------------------------------------------------------------------------


class _AgentStub:
    """Only the attributes the tool lane touches; nothing is persisted."""

    def __init__(self, *, session_id: str = "sess-progress", plan_id: Optional[int] = None) -> None:
        self.session_id = session_id
        self.extra_context: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self.plan_session = SimpleNamespace(plan_id=plan_id, repo=None)
        self.plan_tree = None

    def _sync_task_status_after_tool_execution(self, **_kwargs: Any) -> None:
        return None

    async def _prepare_code_executor_params(
        self,
        *,
        action: LLMAction,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        task = str(params.get("task") or "")
        return {"task": task, "require_task_context": False}, task


def _code_executor_action(task: str = "produce a report") -> LLMAction:
    return LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={"task": task},
        order=1,
    )


# ---------------------------------------------------------------------------
# the channel the action lane builds for a tool
# ---------------------------------------------------------------------------


async def test_tool_context_carries_the_turn_progress_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``on_progress`` is published by the stream and reaches ``ToolContext``."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    loop = asyncio.get_running_loop()
    received: List[Tuple[str, Dict[str, Any]]] = []

    async def on_tool_progress(tool_name: str, data: Dict[str, Any]) -> None:
        received.append((tool_name, data))

    agent = _AgentStub()
    agent._tool_progress_emitter = on_tool_progress
    agent._tool_progress_loop = loop

    context = _build_chat_tool_context(agent, "code_executor")

    assert context is not None
    assert context.on_progress is not None
    assert context.on_progress_loop is loop
    assert context.session_id == "sess-progress"

    await context.on_progress({"stage": "started", "message": DELEGATION_STARTED})

    assert received == [
        ("code_executor", {"stage": "started", "message": DELEGATION_STARTED})
    ]


async def test_tool_context_without_a_channel_degrades_to_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare agent is not an error: the tool simply gets no channel."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

    # No session at all → no context (unchanged behaviour).
    assert _build_chat_tool_context(object(), "code_executor") is None
    assert _build_chat_tool_context(_AgentStub(session_id=""), "code_executor") is None

    # A session but no channel attribute (other callers build agents themselves).
    context = _build_chat_tool_context(_AgentStub(), "code_executor")
    assert context is not None
    assert context.on_progress is None
    assert context.on_progress_loop is None

    # A non-callable attribute must not be invoked.
    unusable = _AgentStub()
    unusable._tool_progress_emitter = "not-callable"  # type: ignore[assignment]
    unusable._tool_progress_loop = asyncio.get_running_loop()
    context = _build_chat_tool_context(unusable, "code_executor")
    assert context is not None
    assert context.on_progress is None
    assert context.on_progress_loop is None


async def test_a_stale_owner_loop_falls_back_to_the_running_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed owner loop is unusable; the current loop takes over."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    closed = asyncio.new_event_loop()
    closed.close()

    async def on_tool_progress(_tool_name: str, _data: Dict[str, Any]) -> None:
        return None

    agent = _AgentStub()
    agent._tool_progress_emitter = on_tool_progress
    agent._tool_progress_loop = closed

    context = _build_chat_tool_context(agent, "code_executor")

    assert context is not None
    assert context.on_progress is not None
    assert context.on_progress_loop is asyncio.get_running_loop()


# ---------------------------------------------------------------------------
# the tool lane actually hands the channel to the handler
# ---------------------------------------------------------------------------


async def test_prompt_lane_hands_the_channel_to_code_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    agent = _AgentStub()
    seen: Dict[str, Any] = {}
    reported: List[Dict[str, Any]] = []

    async def on_tool_progress(_tool_name: str, data: Dict[str, Any]) -> None:
        reported.append(data)

    agent._tool_progress_emitter = on_tool_progress
    agent._tool_progress_loop = asyncio.get_running_loop()

    async def _stub_execute_tool(tool_name: str, **params: Any) -> Dict[str, Any]:
        seen["tool_name"] = tool_name
        seen["params"] = params
        return {"success": True, "summary": "stub code_executor output"}

    monkeypatch.setattr(action_handlers, "execute_tool", _stub_execute_tool)

    step = await handle_tool_action(agent, _code_executor_action())

    assert isinstance(step, AgentStep)
    assert step.success is True
    assert seen["tool_name"] == "code_executor"
    context = seen["params"]["tool_context"]
    assert context is not None
    assert context.on_progress_loop is asyncio.get_running_loop()

    await context.on_progress({"stage": "started", "message": DELEGATION_STARTED})

    assert reported == [{"stage": "started", "message": DELEGATION_STARTED}]


async def test_a_raising_progress_callback_never_fails_the_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local lane reports unguarded; the chat lane must absorb the raise."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    agent = _AgentStub()
    attempts: List[str] = []

    async def _raising(_tool_name: str, _data: Dict[str, Any]) -> None:
        attempts.append("invoked")
        raise RuntimeError("progress channel is broken")

    agent._tool_progress_emitter = _raising
    agent._tool_progress_loop = asyncio.get_running_loop()

    async def _stub_execute_tool(_tool_name: str, **params: Any) -> Dict[str, Any]:
        # ``code_executor_backend._execute_task_locally._report`` awaits the
        # callback directly — an exception there used to reach the tool call.
        await params["tool_context"].on_progress(
            {"stage": "started", "message": "Generating code for task"}
        )
        return {"success": True, "summary": "stub code_executor output"}

    monkeypatch.setattr(action_handlers, "execute_tool", _stub_execute_tool)

    step = await handle_tool_action(agent, _code_executor_action())

    assert attempts == ["invoked"], "the callback must actually have been invoked"
    assert isinstance(step, AgentStep)
    assert step.success is True
    assert step.details["result"]["success"] is True


# ---------------------------------------------------------------------------
# end to end: a delegated run becomes progress_status events in the stream
# ---------------------------------------------------------------------------


class _StubRepo:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree

    def get_plan_tree(self, _plan_id: int) -> PlanTree:
        return self._tree


class _StubPlanSession:
    def __init__(self, *, plan_id: int, tree: PlanTree) -> None:
        self.plan_id = plan_id
        self.repo = _StubRepo(tree)


class _JobStub:
    """Swallows the job bookkeeping the stream performs mid-turn."""

    def create_job(self, **_kwargs: Any) -> None:
        return None

    def mark_running(self, _job_id: str) -> None:
        return None

    def register_subscriber(self, _job_id: str, _loop: Any) -> None:
        return None

    def register_runtime_controller(self, _job_id: str, _controller: Any) -> bool:
        return False

    def unregister_runtime_controller(self, _job_id: str) -> None:
        return None

    def unregister_subscriber(self, _job_id: str, _queue: Any) -> None:
        return None

    def mark_success(self, _job_id: str, **_kwargs: Any) -> None:
        return None

    def mark_failure(self, _job_id: str, _error: str, **_kwargs: Any) -> None:
        return None

    def append_log(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def attach_plan(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _build_stream_agent(*, session_id: str = "sess-progress") -> StructuredChatAgent:
    node = PlanNode(id=66, plan_id=34, name="Task 66", status="pending")
    tree = PlanTree(id=34, title="plan", nodes={66: node}, adjacency={None: [66]})
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _StubPlanSession(plan_id=34, tree=tree)
    agent.plan_tree = tree
    agent.extra_context = {}
    agent.history = []
    agent.session_id = session_id
    agent.max_history_messages = 20
    agent.llm_service = object()
    agent._sync_task_status_after_tool_execution = lambda **_kwargs: None
    return agent


def _decision(message: str) -> RequestRoutingDecision:
    return RequestRoutingDecision(
        request_tier="standard",
        request_route_mode="auto_deepthink",
        route_reason_codes=[],
        manual_deep_think=False,
        thinking_visibility="progress",
        effective_user_message=message,
        intent_type="chat",
        subject_resolution={},
        brevity_hint=False,
        explicit_task_ids=[],
        explicit_task_override=False,
        full_plan_execution=False,
    )


def _profile() -> RequestTierProfile:
    return RequestTierProfile(
        request_tier="standard",
        thinking_budget=10000,
        max_iterations=8,
        available_tools=["code_executor"],
        output_bias="balanced",
        intent_type="chat",
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


def _run(agent: StructuredChatAgent, message: str) -> List[Dict[str, Any]]:
    async def _drive() -> List[str]:
        return [chunk async for chunk in agent.process_unified_stream(message)]

    return _collect(asyncio.run(_drive()))


class _ToolCallingThinkingAgent:
    """Deep-think stand-in that drives one ``code_executor`` call per turn.

    It goes through ``tool_executor`` (the agent's ``tool_wrapper``) exactly like
    a real cycle, so the whole prompt lane — ``tool_wrapper`` →
    ``_handle_tool_action`` → ``action_handlers.handle_tool_action`` → the
    ``code_executor`` branch — is the production one.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def skip_step(self) -> None:
        return None

    async def think(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Any = None,
    ) -> DeepThinkResult:
        params = {"task": "produce a report", "allowed_tools": "Bash,Write,Read"}
        await self.kwargs["on_tool_start"]("code_executor", dict(params))
        result = await self.kwargs["tool_executor"]("code_executor", dict(params))
        await self.kwargs["on_tool_result"](
            "code_executor",
            {
                "success": bool(result.get("success")),
                "summary": str(result.get("summary") or ""),
            },
        )
        return DeepThinkResult(
            final_answer="stub answer",
            thinking_steps=[],
            total_iterations=1,
            tools_used=["code_executor"],
            confidence=1.0,
            thinking_summary="done",
        )


def _tool_calling_agent_class() -> type:
    """A real *class* so the ``chat_routes.DeepThinkAgent`` compat bridge picks it."""

    class _ScriptedToolAgent(_ToolCallingThinkingAgent):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)

    return _ScriptedToolAgent


def test_delegated_code_executor_run_reports_progress_into_the_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(agent_module, "plan_decomposition_jobs", _JobStub())
    monkeypatch.setattr(agent_module, "_persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr(agent_module, "_save_chat_message", lambda *a, **k: None)
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _tool_calling_agent_class())

    owner_thread = threading.get_ident()
    captured: Dict[str, Any] = {}

    async def _stub_execute_tool(tool_name: str, **params: Any) -> Dict[str, Any]:
        context = params.get("tool_context")
        captured["context"] = context

        def _worker() -> None:
            # The shape the CLI lanes run in: the delegation is supervised from a
            # worker thread whose own loop is not the loop that owns the callback.
            assert threading.get_ident() != owner_thread
            captured["owner_loop_running"] = context.on_progress_loop.is_running()

            async def _delegate() -> None:
                reporter = build_delegation_progress(
                    context,
                    run_id=RUN_ID,
                    backend="qwen_code",
                    lane="qwen_primary",
                )
                await reporter.report(
                    "started", DELEGATION_STARTED, detail=STARTED_DETAIL
                )
                await reporter.report(
                    "completed", DELEGATION_COMPLETED, detail=COMPLETED_DETAIL
                )

            asyncio.run(_delegate())

        await asyncio.to_thread(_worker)
        return {"success": True, "summary": "stub CLI finished", "run_id": RUN_ID}

    monkeypatch.setattr(chat_routes, "execute_tool", _stub_execute_tool)
    monkeypatch.setattr(agent_module, "execute_tool", _stub_execute_tool)

    agent = _build_stream_agent()
    agent._resolve_request_routing = lambda _message: (_decision("run the task"), _profile())

    events = _run(agent, "run the task")

    context = captured.get("context")
    assert context is not None, "the prompt lane must hand code_executor a ToolContext"
    assert context.on_progress is not None
    # The channel's owner loop is the stream's loop and it was live while the
    # delegation reported — the cross-thread hop below delivers onto it.
    assert context.on_progress_loop is not None
    assert captured["owner_loop_running"] is True
    assert context.session_id == "sess-progress"

    progress = [
        event
        for event in events
        if event.get("type") == "progress_status" and event.get("tool") == "code_executor"
    ]
    labels = [event.get("label") for event in progress]
    assert DELEGATION_STARTED in labels, labels
    assert DELEGATION_COMPLETED in labels, labels
    assert labels.index(DELEGATION_STARTED) < labels.index(DELEGATION_COMPLETED)

    started = progress[labels.index(DELEGATION_STARTED)]
    assert started == {
        "type": "progress_status",
        "phase": "gathering",
        "label": DELEGATION_STARTED,
        "details": STARTED_DETAIL,
        "iteration": None,
        "tool": "code_executor",
        "status": "active",
    }

    completed = progress[labels.index(DELEGATION_COMPLETED)]
    assert completed == {
        "type": "progress_status",
        "phase": "gathering",
        "label": DELEGATION_COMPLETED,
        "details": COMPLETED_DETAIL,
        "iteration": None,
        "tool": "code_executor",
        "status": "completed",
    }

    # The turn still ends on the normal ``final`` event.
    assert [event.get("type") for event in events][-1] == "final"


def test_stream_without_a_tool_call_keeps_its_event_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn that never enters the tool lane is untouched by the wiring."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(agent_module, "plan_decomposition_jobs", _JobStub())
    monkeypatch.setattr(agent_module, "_persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr(agent_module, "_save_chat_message", lambda *a, **k: None)

    class _AnswerOnlyThinkingAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def pause(self) -> None:
            return None

        def resume(self) -> None:
            return None

        def skip_step(self) -> None:
            return None

        async def think(
            self,
            user_query: str,
            context: Optional[Dict[str, Any]] = None,
            task_context: Any = None,
        ) -> DeepThinkResult:
            return DeepThinkResult(
                final_answer="stub answer",
                thinking_steps=[],
                total_iterations=1,
                tools_used=[],
                confidence=1.0,
                thinking_summary="done",
            )

    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _AnswerOnlyThinkingAgent)

    agent = _build_stream_agent()
    agent._resolve_request_routing = lambda _message: (_decision("hello"), _profile())

    events = _run(agent, "hello")

    assert [event.get("type") for event in events] == [
        "progress_status",
        "control_ack",
        "final",
    ]
