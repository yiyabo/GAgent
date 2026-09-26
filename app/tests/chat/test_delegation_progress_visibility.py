"""A delegated run must stay visible in the parent stream in *every* display mode.

``code_executor``'s CLI lanes and ``delegate_task`` hand the work to an external
sub-agent that can run for hours and report only through
``ToolContext.on_progress`` → ``agent.py:on_tool_progress`` →
``_emit_progress_status``.  That emitters was gated on
``thinking_visibility == "progress"``, while production routing has returned
``"visible"`` since ``a9a3597d`` — so a delegated run produced no
``progress_status`` at all in a real turn even though the wiring (S3c,
``test_tool_progress_channel.py``) was in place.

These tests pin the narrow exception that releases it:

* **released** — ``on_tool_progress`` for the tools in
  ``DELEGATION_PROGRESS_TOOLS`` (``code_executor``, ``delegate_task``), in every
  display mode, ``"visible"`` included;
* **still gated in ``visible``** — every other tool's ``on_tool_progress`` and
  *all* ``on_tool_start`` / ``on_tool_result`` events, the delegation tools'
  included;
* **unchanged** — ``thinking_visibility == "progress"`` turns release everything
  as before.

The harness mirrors ``test_tool_progress_channel.py``: scripted deep-think
callbacks driven through the real ``process_unified_stream``, so the assertions
run against the closures production hands to the deep-think agent.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.routers import chat_routes
from app.routers.chat import agent as agent_module
from app.routers.chat.request_routing import (
    RequestRoutingDecision,
    RequestTierProfile,
)
from app.routers.chat_routes import StructuredChatAgent
from app.services.deep_think_agent import DeepThinkResult
from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl.delegation_progress import (
    DELEGATION_PROGRESS_TOOLS,
    build_delegation_progress,
)

RUN_ID = "run-1"
DELEGATION_STARTED = "Delegating to Qwen Code · lane qwen_primary"
DELEGATION_HEARTBEAT = "Sub-agent still running · 12m30s"
DELEGATION_HEARTBEAT_20S = "Sub-agent still running · 20s"
DELEGATION_COMPLETED = "Sub-agent run completed in 12s (2 artifacts)"
STARTED_DETAIL = f"run {RUN_ID} · backend qwen_code"
HEARTBEAT_DETAIL = f"run {RUN_ID} · attempt 1/1"


# ---------------------------------------------------------------------------
# stream harness (see ``test_tool_progress_channel.py`` for the full shape)
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


def _build_stream_agent(*, session_id: str = "sess-visibility") -> StructuredChatAgent:
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


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_module, "plan_decomposition_jobs", _JobStub())
    monkeypatch.setattr(agent_module, "_persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr(agent_module, "_save_chat_message", lambda *a, **k: None)


def _decision(message: str, *, visibility: str = "visible") -> RequestRoutingDecision:
    return RequestRoutingDecision(
        request_tier="standard",
        request_route_mode="auto_deepthink",
        route_reason_codes=[],
        manual_deep_think=False,
        thinking_visibility=visibility,
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
        available_tools=["code_executor", "delegate_task", "file_operations"],
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


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [str(event.get("type")) for event in events]


def _progress(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [event for event in events if event.get("type") == "progress_status"]


CallSpec = tuple


def _scripted_agent_class(calls: List[CallSpec]) -> type:
    """A real class (so the ``chat_routes.DeepThinkAgent`` bridge picks it) that
    replays *calls* against the stream's own callback kwargs."""

    class _ScriptedAgent:
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
            for callback_name, args in calls:
                callback = self.kwargs.get(callback_name)
                assert callback is not None, callback_name
                await callback(*args)
            return DeepThinkResult(
                final_answer="stub answer",
                thinking_steps=[],
                total_iterations=1,
                tools_used=[],
                confidence=1.0,
                thinking_summary="done",
            )

    return _ScriptedAgent


def _drive_scripted(
    monkeypatch: pytest.MonkeyPatch,
    calls: List[CallSpec],
    *,
    visibility: str,
) -> List[Dict[str, Any]]:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _scripted_agent_class(calls))
    agent = _build_stream_agent()
    agent._resolve_request_routing = lambda _message: (_decision("run it", visibility=visibility), _profile())
    return _run(agent, "run it")


# ---------------------------------------------------------------------------
# the tool set is the single source for the exception
# ---------------------------------------------------------------------------


def test_delegation_tool_set_covers_both_delegating_handlers() -> None:
    """The exception is scoped to the handlers that build a reporter."""
    assert DELEGATION_PROGRESS_TOOLS == frozenset({"code_executor", "delegate_task"})
    assert isinstance(DELEGATION_PROGRESS_TOOLS, frozenset)


# ---------------------------------------------------------------------------
# released: on_tool_progress for a delegating tool, in a visible turn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "payload", "expected"),
    [
        (
            "code_executor",
            {"stage": "started", "message": DELEGATION_STARTED, "detail": STARTED_DETAIL},
            {
                "type": "progress_status",
                "phase": "gathering",
                "label": DELEGATION_STARTED,
                "details": STARTED_DETAIL,
                "iteration": None,
                "tool": "code_executor",
                "status": "active",
            },
        ),
        (
            "code_executor",
            {"stage": "running", "message": DELEGATION_HEARTBEAT, "detail": HEARTBEAT_DETAIL},
            {
                "type": "progress_status",
                "phase": "gathering",
                "label": DELEGATION_HEARTBEAT,
                "details": HEARTBEAT_DETAIL,
                "iteration": None,
                "tool": "code_executor",
                "status": "active",
            },
        ),
        (
            "delegate_task",
            {"stage": "started", "message": DELEGATION_STARTED},
            {
                "type": "progress_status",
                "phase": "gathering",
                "label": DELEGATION_STARTED,
                "details": None,
                "iteration": None,
                "tool": "delegate_task",
                "status": "active",
            },
        ),
        (
            "delegate_task",
            {"stage": "completed", "message": DELEGATION_COMPLETED},
            {
                "type": "progress_status",
                "phase": "gathering",
                "label": DELEGATION_COMPLETED,
                "details": None,
                "iteration": None,
                "tool": "delegate_task",
                "status": "completed",
            },
        ),
    ],
)
def test_visible_turn_releases_delegation_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    payload: Dict[str, Any],
    expected: Dict[str, Any],
) -> None:
    """``thinking_visibility="visible"`` + ``on_progress`` + delegating tool → event."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

    events = _drive_scripted(
        monkeypatch,
        [("on_tool_progress", (tool_name, payload))],
        visibility="visible",
    )

    progress = _progress(events)
    assert progress == [expected]
    # The turn still opens on ``control_ack`` and ends on ``final``; nothing else
    # was released alongside.
    assert _types(events) == ["control_ack", "progress_status", "final"]


def test_visible_turn_threads_the_iteration_into_the_released_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The released event is the normal payload, iteration included."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from app.services.deep_think_agent import ThinkingStep

    step = ThinkingStep(
        iteration=3,
        thought="calling the sub-agent",
        action=None,
        action_result=None,
        self_correction=None,
        display_text=None,
        kind="reasoning",
        status="active",
    )

    events = _drive_scripted(
        monkeypatch,
        [
            ("on_thinking", (step,)),
            ("on_tool_progress", ("code_executor", {"stage": "running", "message": DELEGATION_HEARTBEAT})),
        ],
        visibility="visible",
    )

    progress = _progress(events)
    assert [event["label"] for event in progress] == [DELEGATION_HEARTBEAT]
    assert progress[0]["iteration"] == 3
    assert progress[0]["tool"] == "code_executor"
    # ``thinking_step`` production is untouched by the exception.
    assert _types(events) == ["control_ack", "thinking_step", "progress_status", "final"]


# ---------------------------------------------------------------------------
# still gated: every other tool's progress, and every start/result event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["file_operations", "bio_tools", "web_search"])
def test_visible_turn_keeps_other_tool_progress_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

    events = _drive_scripted(
        monkeypatch,
        [("on_tool_progress", (tool_name, {"stage": "running", "message": "working"}))],
        visibility="visible",
    )

    assert _progress(events) == []
    assert _types(events) == ["control_ack", "final"]


@pytest.mark.parametrize("tool_name", ["code_executor", "delegate_task", "file_operations"])
def test_visible_turn_keeps_tool_start_and_result_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    """The exception is ``on_tool_progress`` only — start/result stay gated,
    even for the delegating tools."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

    events = _drive_scripted(
        monkeypatch,
        [
            ("on_tool_start", (tool_name, {"task": "produce a report"})),
            ("on_tool_result", (tool_name, {"success": True, "summary": "done"})),
        ],
        visibility="visible",
    )

    assert _progress(events) == []
    assert _types(events) == ["control_ack", "final"]


def test_visible_turn_keeps_a_retrying_tool_result_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry/failure branches of ``on_tool_result`` are gated too."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

    events = _drive_scripted(
        monkeypatch,
        [
            ("on_tool_result", ("code_executor", {"success": False, "retrying": True, "error": "boom"})),
            ("on_tool_result", ("code_executor", {"success": False, "error": "boom"})),
        ],
        visibility="visible",
    )

    assert _progress(events) == []
    assert _types(events) == ["control_ack", "final"]


# ---------------------------------------------------------------------------
# unchanged: a progress-mode turn still releases everything
# ---------------------------------------------------------------------------


def test_progress_mode_turn_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

    events = _drive_scripted(
        monkeypatch,
        [
            ("on_tool_start", ("file_operations", {"operation": "list", "path": "."})),
            ("on_tool_result", ("file_operations", {"success": True, "summary": "listed 3 files"})),
            ("on_tool_progress", ("file_operations", {"stage": "running", "message": "listing"})),
            ("on_tool_progress", ("code_executor", {"stage": "running", "message": DELEGATION_HEARTBEAT})),
        ],
        visibility="progress",
    )

    # Opening planning event, the ack, then one event per callback above.
    assert _types(events) == [
        "progress_status",
        "control_ack",
        "progress_status",
        "progress_status",
        "progress_status",
        "progress_status",
        "final",
    ]
    assert (events[0]["phase"], events[0]["tool"], events[0]["iteration"]) == ("planning", None, 0)
    assert (events[2]["tool"], events[2]["phase"], events[2]["status"]) == (
        "file_operations",
        "gathering",
        "active",
    )
    assert (events[3]["tool"], events[3]["phase"], events[3]["status"]) == (
        "file_operations",
        "synthesizing",
        "completed",
    )
    assert (events[4]["tool"], events[4]["label"]) == ("file_operations", "listing")
    assert (events[5]["tool"], events[5]["label"]) == ("code_executor", DELEGATION_HEARTBEAT)


# ---------------------------------------------------------------------------
# end to end: a real prompt-lane delegation in a visible turn
# ---------------------------------------------------------------------------


class _ToolCallingThinkingAgent:
    """Deep-think stand-in driving one ``code_executor`` call per turn.

    It goes through ``tool_executor`` (the agent's ``tool_wrapper``) exactly like
    a real cycle, so ``tool_wrapper`` → ``_handle_tool_action`` →
    ``action_handlers.handle_tool_action`` → the ``code_executor`` branch is the
    production lane.
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
            {"success": bool(result.get("success")), "summary": str(result.get("summary") or "")},
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
    class _ScriptedToolAgent(_ToolCallingThinkingAgent):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)

    return _ScriptedToolAgent


def test_visible_mode_delegated_run_reports_progress_into_the_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``visible`` turn whose tool delegates to a CLI sub-agent.

    The reports travel the production route — worker thread → owning loop →
    ``_bind_chat_tool_progress`` → ``on_tool_progress`` → the stream — and land
    as ``progress_status`` events even though the turn is not a ``progress``
    turn.  ``on_tool_start`` / ``on_tool_result`` for the same tool stay gated,
    so the only ``code_executor`` progress events are the delegation's own.
    """
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _tool_calling_agent_class())

    owner_thread = threading.get_ident()
    captured: Dict[str, Any] = {}

    async def _stub_execute_tool(tool_name: str, **params: Any) -> Dict[str, Any]:
        context = params.get("tool_context")
        captured["context"] = context

        def _worker() -> None:
            assert threading.get_ident() != owner_thread
            captured["owner_loop_running"] = context.on_progress_loop.is_running()
            # A fake clock lets the heartbeat fire without sleeping: this is the
            # row shape a long delegation adds every interval.
            clock_state = {"t": 0.0}

            async def _delegate() -> None:
                reporter = build_delegation_progress(
                    context,
                    run_id=RUN_ID,
                    backend="qwen_code",
                    lane="qwen_primary",
                    heartbeat_seconds=15.0,
                    clock=lambda: clock_state["t"],
                )
                await reporter.report("started", DELEGATION_STARTED, detail=STARTED_DETAIL)
                clock_state["t"] = 20.0
                assert await reporter.heartbeat(attempt=1, total_attempts=1) is True
                await reporter.report("completed", DELEGATION_COMPLETED)

            asyncio.run(_delegate())

        await asyncio.to_thread(_worker)
        return {"success": True, "summary": "stub CLI finished", "run_id": RUN_ID}

    monkeypatch.setattr(chat_routes, "execute_tool", _stub_execute_tool)
    monkeypatch.setattr(agent_module, "execute_tool", _stub_execute_tool)

    agent = _build_stream_agent()
    agent._resolve_request_routing = lambda _message: (_decision("run the task", visibility="visible"), _profile())

    events = _run(agent, "run the task")

    context = captured.get("context")
    assert context is not None, "the prompt lane must hand code_executor a ToolContext"
    assert context.on_progress is not None
    assert captured["owner_loop_running"] is True

    progress = [event for event in _progress(events) if event.get("tool") == "code_executor"]
    assert [event["label"] for event in progress] == [
        DELEGATION_STARTED,
        DELEGATION_HEARTBEAT_20S,
        DELEGATION_COMPLETED,
    ]
    assert progress[0] == {
        "type": "progress_status",
        "phase": "gathering",
        "label": DELEGATION_STARTED,
        "details": STARTED_DETAIL,
        "iteration": None,
        "tool": "code_executor",
        "status": "active",
    }
    # The heartbeat row — one per interval for as long as the delegation runs.
    assert progress[1] == {
        "type": "progress_status",
        "phase": "gathering",
        "label": DELEGATION_HEARTBEAT_20S,
        "details": HEARTBEAT_DETAIL,
        "iteration": None,
        "tool": "code_executor",
        "status": "active",
    }
    assert progress[2] == {
        "type": "progress_status",
        "phase": "gathering",
        "label": DELEGATION_COMPLETED,
        "details": None,
        "iteration": None,
        "tool": "code_executor",
        "status": "completed",
    }

    # ``on_tool_start`` / ``on_tool_result`` for the same tool added nothing, and
    # the turn still ends on the normal ``final`` event.
    assert _types(events) == ["control_ack", "progress_status", "progress_status", "progress_status", "final"]


# ---------------------------------------------------------------------------
# end to end: the generic-execution tools reach the executor through tool_wrapper
# ---------------------------------------------------------------------------

GENERIC_EXECUTION_CALLS = (
    ("execute_code", {"code": "print(1 + 1)"}),
    ("delegate_task", {"goal": "audit the repo"}),
    ("load_skill", {"name": "gget"}),
)


class _GenericToolThinkingAgent:
    """Deep-think stand-in driving one *generic* tool call per turn.

    ``execute_code``, ``delegate_task`` and ``load_skill`` carry no per-tool
    parameter normalizer, so before the ``_GENERIC_EXECUTION_TOOLS`` branch of
    ``action_handlers.handle_tool_action`` they fell into its ``unsupported_tool``
    step — advertised to the model, unreachable at execution.  This drives them
    through ``tool_executor`` (the agent's ``tool_wrapper``) like a real cycle.
    """

    tool_name = ""
    params: Dict[str, Any] = {}

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
        await self.kwargs["on_tool_start"](self.tool_name, dict(self.params))
        result = await self.kwargs["tool_executor"](self.tool_name, dict(self.params))
        await self.kwargs["on_tool_result"](
            self.tool_name,
            {
                "success": bool(result.get("success")),
                "summary": str(result.get("summary") or ""),
            },
        )
        return DeepThinkResult(
            final_answer="stub answer",
            thinking_steps=[],
            total_iterations=1,
            tools_used=[self.tool_name],
            confidence=1.0,
            thinking_summary="done",
        )


def _generic_tool_agent_class(tool_name: str, params: Dict[str, Any]) -> type:
    """The production constructor kwargs are fixed, so bind the call in a class."""

    class _ScriptedGenericAgent(_GenericToolThinkingAgent):
        pass

    _ScriptedGenericAgent.tool_name = tool_name
    _ScriptedGenericAgent.params = dict(params)
    return _ScriptedGenericAgent


def _generic_tools_profile() -> RequestTierProfile:
    return RequestTierProfile(
        request_tier="standard",
        thinking_budget=10000,
        max_iterations=8,
        available_tools=["execute_code", "delegate_task", "load_skill"],
        output_bias="balanced",
        intent_type="chat",
        explicit_task_ids=[],
        explicit_task_override=False,
    )


@pytest.mark.parametrize("tool_name,params", GENERIC_EXECUTION_CALLS)
def test_generic_execution_tools_reach_the_executor(
    tool_name: str,
    params: Dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tool_wrapper`` hands these three to the executor instead of rejecting them.

    The env gates are what put ``execute_code`` / ``delegate_task`` in the tool
    pool, and ``_enforce_capability_guard`` rejects anything outside it, so they
    have to be on for the turn to look like production.
    """
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        chat_routes,
        "DeepThinkAgent",
        _generic_tool_agent_class(tool_name, params),
    )

    captured: Dict[str, Any] = {}

    async def _stub_execute_tool(registered_tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        captured["tool_name"] = registered_tool_name
        captured["params"] = kwargs
        return {"success": True, "summary": f"{registered_tool_name} stub ran", "tool": registered_tool_name}

    monkeypatch.setattr(chat_routes, "execute_tool", _stub_execute_tool)
    monkeypatch.setattr(agent_module, "execute_tool", _stub_execute_tool)

    agent = _build_stream_agent()
    agent._resolve_request_routing = lambda _message: (
        _decision("run the tool"),
        _generic_tools_profile(),
    )

    events = _run(agent, "run the tool")

    assert captured.get("tool_name") == tool_name, (
        f"{tool_name} never reached the executor; events were {_types(events)}"
    )
    # The deep-think wrapper strips `tool_context` out of the action parameters,
    # so the lane has to rebuild it: execute_code needs it for kernel routing.
    assert captured["params"].get("tool_context") is not None, (
        f"{tool_name} reached the executor without a ToolContext"
    )
    for key, value in params.items():
        assert captured["params"].get(key) == value
    assert "unsupported_tool" not in json.dumps(events)
