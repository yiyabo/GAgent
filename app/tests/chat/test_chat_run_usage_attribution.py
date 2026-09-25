"""Chat-run usage attribution: the chat lane's end of the parent/child link.

A background chat run used to bind only ``session_id``/``plan_id``/``task_id``
into the ambient LLM usage context, so every LLM call the run made itself was
booked with ``run_id = NULL`` — and a delegated sub-agent, which resolves its
``parent_run_id`` from that context, had no parent to link to.

These tests pin the contract end to end: the run worker binds its own run id
for the duration of the run (through the real ``build_agent_for_chat_request``),
a delegation booked across the worker-thread hop sees it as its parent, the
run's own calls carry it, and the binding is released when the run ends or
absent when the caller has no run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.routers.chat.models import ChatRequest

_CHILD_RUN_ID = "20260926_101500_000000_cafebabe"
_SESSION_ID = "session-usage"

_RECORDED_AGENTS: List["_RecordingAgent"] = []


@pytest.fixture(autouse=True)
def _isolated_usage_context():
    from app.llm import _usage_context

    _usage_context.set(None)
    _RECORDED_AGENTS.clear()
    yield
    _usage_context.set(None)
    _RECORDED_AGENTS.clear()


@pytest.fixture
def ledger_db(tmp_path: Path):
    """Point the shared SQLite pool at a throwaway ledger instead of the repo DB."""
    from app.database_pool import close_connection_pool, initialize_connection_pool

    initialize_connection_pool(db_path=str(tmp_path / "llm_usage_ledger.db"))
    try:
        yield tmp_path / "llm_usage_ledger.db"
    finally:
        close_connection_pool()


def _ambient_run_id() -> Optional[str]:
    from app.llm import _usage_context

    ctx = _usage_context.get()
    return ctx.get("run_id") if isinstance(ctx, dict) else None


def _usage_rows() -> List[Dict[str, Any]]:
    from app.database_pool import get_db

    with get_db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM llm_usage_log WHERE session_id = ?", (_SESSION_ID,)
            )
        ]


def _book_delegated_run_row() -> None:
    """Book one delegated CLI run the way the code_executor lane does.

    ``parent_run_id`` is deliberately left unset: the lane derives it from the
    ambient usage context, which is the link under test.
    """
    from tool_box.tools_impl import code_executor as code_executor_module

    code_executor_module._record_external_cli_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=1200,
        completion_tokens=300,
        session_id=_SESSION_ID,
        plan_id=None,
        task_id=None,
        call_purpose="qwen_code_cli_execution",
        duration_ms=4200.0,
        run_id=_CHILD_RUN_ID,
        tool_name="code_executor",
        call_status="ok",
    )


def _delegate_through_tool_executor() -> Dict[str, Any]:
    """The production delegation hop, run from a worker thread."""
    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        UnifiedToolExecutor,
    )

    return UnifiedToolExecutor().execute_sync(
        "code_executor",
        {"task": "write the report"},
        context=ToolExecutionContext(session_id=_SESSION_ID),
    )


class _RecordingAgent:
    """Stand-in for StructuredChatAgent that records its ambient attribution."""

    def __init__(self, **kwargs: Any) -> None:
        self.session_id = kwargs.get("session_id")
        self.plan_session = kwargs.get("plan_session")
        self.extra_context = dict(kwargs.get("extra_context") or {})
        self.history = list(kwargs.get("history") or [])
        self.seen: Dict[str, Any] = {}
        _RECORDED_AGENTS.append(self)

    async def process_unified_stream(self, message: str, *, run_id=None, **kwargs: Any):
        self.seen["run_id_param"] = run_id
        self.seen["ambient_run_id"] = _ambient_run_id()

        # A call the run makes itself.  The deep-think loop merges its own
        # purpose into the context; the run id has to survive that merge.
        from app.llm import _log_usage, update_usage_context

        update_usage_context(
            call_purpose="deep_think_iteration",
            phase="deep_think",
            tool_name="deep_think",
        )
        _log_usage("qwen", "qwen-test", 100, 50, 150)

        # A tool call that delegates to a CLI sub-agent from a worker thread.
        await asyncio.to_thread(_delegate_through_tool_executor)
        if False:  # pragma: no cover - makes this an async generator
            yield None


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def emit(self, payload: Dict[str, Any]) -> None:
        self.events.append(payload)


def _stub_stream_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the chat-lane collaborators so the real builder can run off-DB."""
    from app.routers.chat import stream_context

    monkeypatch.setattr(stream_context, "_resolve_plan_binding", lambda *a, **k: None)
    monkeypatch.setattr(stream_context, "_get_session_settings", lambda *a, **k: {})
    monkeypatch.setattr(stream_context, "_load_session_runtime_context", lambda *a, **k: {})
    monkeypatch.setattr(stream_context, "_get_session_current_task", lambda *a, **k: None)
    monkeypatch.setattr(stream_context, "get_structured_chat_agent_cls", lambda: _RecordingAgent)


def _stub_tool_execution(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Book a delegated row instead of running the real code executor."""
    import tool_box

    seen: Dict[str, Any] = {}

    async def _fake_execute_tool(tool_name, **kwargs):  # noqa: ANN001
        seen["thread_ambient_run_id"] = _ambient_run_id()
        _book_delegated_run_row()
        return {"success": True}

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)
    return seen


def _stub_chat_run_worker(
    monkeypatch: pytest.MonkeyPatch,
    request: ChatRequest,
) -> None:
    from app.services import chat_run_worker as worker

    monkeypatch.setattr(
        worker,
        "get_chat_run",
        lambda run_id: {
            "session_id": _SESSION_ID,
            "request_json": request.model_dump_json(),
        },
    )
    monkeypatch.setattr(worker, "mark_chat_run_started", lambda run_id: None)
    monkeypatch.setattr(
        worker, "mark_chat_run_finished", lambda run_id, status, error=None: None
    )
    monkeypatch.setattr(worker, "ChatRunEmitter", lambda run_id: _RecordingEmitter())
    monkeypatch.setattr(worker, "start_owner_lease", lambda *a, **k: None)
    monkeypatch.setattr(worker, "stop_owner_lease", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_capture_quality_snapshot", lambda run_id: None)
    monkeypatch.setattr(worker, "claim_chat_run_lease", lambda *a, **k: True)
    monkeypatch.setattr(worker, "release_chat_run_lease", lambda *a, **k: True)
    monkeypatch.setattr(worker, "heartbeat_chat_run_lease", lambda *a, **k: True)

    async def _noop_pump(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(worker, "run_signal_pump", _noop_pump)


def _prepare_chat_run(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Install a chat run whose builder is real and whose collaborators are not."""
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    request = ChatRequest(message="delegate the report", session_id=_SESSION_ID)
    _stub_stream_context(monkeypatch)
    delegation = _stub_tool_execution(monkeypatch)
    _stub_chat_run_worker(monkeypatch, request)
    return delegation


def test_chat_run_attributes_its_own_llm_calls_to_the_run(monkeypatch, ledger_db) -> None:
    from app.services.chat_run_worker import execute_chat_run

    run_id = "dt_usage_run"
    _prepare_chat_run(monkeypatch)

    asyncio.run(execute_chat_run(run_id))

    assert len(_RECORDED_AGENTS) == 1
    agent = _RECORDED_AGENTS[0]
    # The run id reaches the agent through the stream call and the ambient
    # context, where the run's own LLM calls read it.
    assert agent.seen["run_id_param"] == run_id
    assert agent.seen["ambient_run_id"] == run_id

    own_rows = [row for row in _usage_rows() if row["call_purpose"] == "deep_think_iteration"]
    assert len(own_rows) == 1
    assert own_rows[0]["run_id"] == run_id
    assert own_rows[0]["parent_run_id"] is None


def test_delegation_row_links_back_to_the_chat_run(monkeypatch, ledger_db) -> None:
    """The link S3a defines: child rows carry their own run id and the parent's."""
    from app.services.chat_run_worker import execute_chat_run

    run_id = "dt_usage_run"
    delegation = _prepare_chat_run(monkeypatch)

    asyncio.run(execute_chat_run(run_id))

    # The ambient run survived the worker-thread hop the delegation takes.
    assert delegation["thread_ambient_run_id"] == run_id

    child_rows = [row for row in _usage_rows() if row["run_id"] == _CHILD_RUN_ID]
    assert len(child_rows) == 1
    assert child_rows[0]["run_id"] == _CHILD_RUN_ID
    assert child_rows[0]["parent_run_id"] == run_id
    assert child_rows[0]["tool_name"] == "code_executor"
    assert child_rows[0]["call_status"] == "ok"


def test_chat_run_releases_its_usage_context_when_it_ends(monkeypatch, ledger_db) -> None:
    """Nothing of the finished run's attribution leaks into later work."""
    from app.llm import _usage_context, clear_usage_context, set_usage_context
    from app.services.chat_run_worker import execute_chat_run

    run_id = "dt_usage_run"
    _prepare_chat_run(monkeypatch)

    async def _main() -> None:
        outer = set_usage_context(
            session_id="outer-session", call_purpose="unit_outer", run_id="outer_run"
        )
        try:
            await execute_chat_run(run_id)
            assert _ambient_run_id() == "outer_run"
        finally:
            clear_usage_context(outer)

    asyncio.run(_main())

    assert _ambient_run_id() is None


def test_chat_request_without_a_run_id_keeps_null_attribution(monkeypatch, ledger_db) -> None:
    """A caller with no run (the legacy no-session stream lane) books as before."""
    from app.repository.llm_usage import init_llm_usage_table
    from app.routers.chat import stream_context

    init_llm_usage_table()
    _stub_stream_context(monkeypatch)
    request = ChatRequest(message="hi", session_id=_SESSION_ID)

    async def _main() -> None:
        agent, _message = await stream_context.build_agent_for_chat_request(request)
        assert isinstance(agent, _RecordingAgent)
        assert _ambient_run_id() is None
        _book_delegated_run_row()

    asyncio.run(_main())

    rows = _usage_rows()
    assert len(rows) == 1
    assert rows[0]["run_id"] == _CHILD_RUN_ID
    assert rows[0]["parent_run_id"] is None
