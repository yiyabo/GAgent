import asyncio
from types import SimpleNamespace

import pytest

from app.routers.chat.models import ChatRequest
from app.services.chat_run_worker import (
    _run_explicit_task_execution,
    execute_chat_run,
)
from app.services.plans.plan_models import PlanNode, PlanTree


class _FakeEmitter:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, payload):
        self.events.append(payload)


def test_run_explicit_task_execution_emits_standard_final_payload_and_persists_message(
    monkeypatch,
) -> None:
    node = PlanNode(id=43, plan_id=77, name="Task 43", status="pending")
    tree = PlanTree(id=77, title="plan77", nodes={43: node}, adjacency={None: [43]})
    agent = SimpleNamespace(
        extra_context={"current_task_id": 43, "pending_scope_task_ids": []},
        session_id="session-1",
        history=[],
        plan_session=SimpleNamespace(repo=SimpleNamespace(get_plan_tree=lambda _plan_id: tree)),
    )
    executor = SimpleNamespace(
        execute_task=lambda plan_id, task_id, config=None: SimpleNamespace(status="completed")
    )
    emitter = _FakeEmitter()
    saved_messages = []

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

    monkeypatch.setattr("app.services.chat_run_worker._save_chat_message", _fake_save_chat_message)

    asyncio.run(
        _run_explicit_task_execution(
            agent,
            executor,
            77,
            run_id="run-1",
            cancel_ev=asyncio.Event(),
            emitter=emitter,
        )
    )

    final_event = emitter.events[-1]
    assert final_event["type"] == "final"
    assert final_event["payload"]["response"] == "Executed 1/1 tasks. Completed: [43]."
    assert final_event["payload"]["metadata"]["explicit_task_execution"] is True
    assert final_event["payload"]["metadata"]["status"] == "completed"

    assert len(saved_messages) == 1
    assert saved_messages[0]["session_id"] == "session-1"
    assert saved_messages[0]["role"] == "assistant"
    assert saved_messages[0]["content"] == "Executed 1/1 tasks. Completed: [43]."
    assert saved_messages[0]["metadata"]["explicit_task_execution"] is True


@pytest.mark.parametrize("task_status, expected", [("failed", "failed"), ("skipped", "failed"), ("completed", "succeeded")])
def test_explicit_execution_returns_its_actual_outcome(monkeypatch, task_status, expected):
    agent = SimpleNamespace(extra_context={"current_task_id": 43}, session_id=None, history=[])
    executor = SimpleNamespace(execute_task=lambda *args, **kwargs: SimpleNamespace(status=task_status))
    emitter = _FakeEmitter()
    outcome = asyncio.run(_run_explicit_task_execution(
        agent, executor, 77, run_id="run-outcome", cancel_ev=asyncio.Event(), emitter=emitter,
    ))
    assert outcome.status == expected
    assert emitter.events[-1]["payload"]["metadata"]["status"] == ("completed" if expected == "succeeded" else expected)


def _isolate_worker(monkeypatch, agent):
    import app.services.chat_run_worker as worker

    request = ChatRequest(message="execute", session_id="session-1")
    emitter = _FakeEmitter()
    finished = []
    monkeypatch.setattr(worker, "get_chat_run", lambda run_id: {"request_json": request.model_dump_json()})
    monkeypatch.setattr(worker, "claim_chat_run_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker, "release_chat_run_lease", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "heartbeat_chat_run_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker, "mark_chat_run_started", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker, "mark_chat_run_finished", lambda run_id, status, **kwargs: finished.append(status))
    monkeypatch.setattr(worker, "ChatRunEmitter", lambda run_id: emitter)
    monkeypatch.setattr(worker, "start_owner_lease", lambda *args: None)
    monkeypatch.setattr(worker, "stop_owner_lease", lambda *args: None)
    monkeypatch.setattr(worker, "_capture_quality_snapshot", lambda *args: None)
    monkeypatch.setattr(worker, "_save_chat_message", lambda *args, **kwargs: None)

    async def build(req, **kwargs):
        return agent, req.message

    async def pump(*args, **kwargs):
        return None

    monkeypatch.setattr(worker, "build_agent_for_chat_request", build)
    monkeypatch.setattr(worker, "run_signal_pump", pump)
    return emitter, finished


def test_failed_explicit_execution_marks_run_failed(monkeypatch):
    agent = SimpleNamespace(
        extra_context={"explicit_task_override": True, "current_task_id": 43, "pending_scope_task_ids": [44]},
        session_id="session-1", history=[], plan_session=SimpleNamespace(plan_id=77),
        plan_executor=SimpleNamespace(execute_task=lambda *args, **kwargs: SimpleNamespace(status="failed")),
    )
    emitter, finished = _isolate_worker(monkeypatch, agent)
    asyncio.run(execute_chat_run("run-explicit-failed"))
    assert finished == ["failed"]
    assert emitter.events[-1]["type"] == "final"
    assert emitter.events[-1]["payload"]["metadata"]["status"] == "failed"


def test_error_stream_is_not_marked_succeeded(monkeypatch):
    async def stream(*args, event_sink, **kwargs):
        await event_sink({"type": "error", "message": "provider unavailable"})
        yield ""

    agent = SimpleNamespace(extra_context={}, process_unified_stream=stream)
    emitter, finished = _isolate_worker(monkeypatch, agent)
    asyncio.run(execute_chat_run("run-stream-failed"))
    assert finished == ["failed"]
    assert emitter.events[-1]["message"] == "provider unavailable"


def test_worker_without_claim_does_not_start_execution(monkeypatch):
    import app.services.chat_run_worker as worker

    monkeypatch.setattr(worker, "claim_chat_run_lease", lambda *args, **kwargs: False)

    def unexpected(*args, **kwargs):
        raise AssertionError("a losing worker must not read or execute the run")

    monkeypatch.setattr(worker, "get_chat_run", unexpected)
    monkeypatch.setattr(worker, "start_owner_lease", unexpected)
    asyncio.run(worker.execute_chat_run("owned-elsewhere"))


def test_lost_lease_cancels_work_without_terminal_write(monkeypatch):
    import app.services.chat_run_worker as worker
    from app.services.cancellation import current_cancel_token
    from app.services.chat_run_state import chat_run_claim

    seen = {}

    async def stream(*args, **kwargs):
        seen["token"] = current_cancel_token()
        await asyncio.Event().wait()
        yield ""

    agent = SimpleNamespace(extra_context={}, process_unified_stream=stream)
    emitter, finished = _isolate_worker(monkeypatch, agent)
    monkeypatch.setattr(worker, "heartbeat_chat_run_lease", lambda *args, **kwargs: False)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(asyncio.wait_for(worker.execute_chat_run("run-lease-lost"), timeout=2))
    assert seen["token"].cancelled
    assert seen["token"].reason == "chat_run_lease_lost"
    assert finished == []
    assert [event["type"] for event in emitter.events] == ["start"]
    assert chat_run_claim.get() is None


def test_explicit_execution_final_payload_preserves_tool_facts() -> None:
    from app.services.chat_run_worker import _build_explicit_execution_final_payload

    payload = _build_explicit_execution_final_payload(
        summary="Executed task.",
        plan_id=77,
        completed_ids=[43],
        failed_id=None,
        total_tasks=1,
        tools_used=["code_executor"],
        tool_failures=["timeout"],
    )

    metadata = payload["payload"]["metadata"]
    assert metadata["tools_used"] == ["code_executor"]
    assert metadata["tool_failures"] == ["timeout"]


def test_execute_chat_run_uses_unified_stream_for_single_explicit_task(monkeypatch) -> None:
    request = ChatRequest(
        message="执行任务43",
        session_id="session-1",
        context={"plan_id": 77},
    )
    agent = SimpleNamespace(
        extra_context={
            "explicit_task_override": True,
            "current_task_id": 43,
            "pending_scope_task_ids": [],
        },
        session_id="session-1",
        plan_session=SimpleNamespace(plan_id=77),
        plan_executor=object(),
    )
    called = {"process": 0, "direct": 0}

    async def _fake_process_unified_stream(*args, **kwargs):
        called["process"] += 1
        if False:
            yield None

    async def _fake_run_explicit_task_execution(*args, **kwargs):
        called["direct"] += 1

    agent.process_unified_stream = _fake_process_unified_stream

    monkeypatch.setattr(
        "app.services.chat_run_worker.get_chat_run",
        lambda run_id: {"request_json": request.model_dump_json()},
    )
    monkeypatch.setattr("app.services.chat_run_worker.mark_chat_run_started", lambda run_id, **kwargs: True)
    monkeypatch.setattr("app.services.chat_run_worker.mark_chat_run_finished", lambda run_id, status, **kwargs: True)
    monkeypatch.setattr("app.services.chat_run_worker.claim_chat_run_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.services.chat_run_worker.release_chat_run_lease", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.chat_run_worker.heartbeat_chat_run_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.services.chat_run_worker._capture_quality_snapshot", lambda run_id: None)

    async def _noop_pump(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.chat_run_worker.run_signal_pump", _noop_pump)
    async def _fake_build_agent(req, **kwargs):
        return (agent, req.message)

    monkeypatch.setattr(
        "app.services.chat_run_worker.build_agent_for_chat_request",
        _fake_build_agent,
    )
    monkeypatch.setattr("app.services.chat_run_worker.ChatRunEmitter", lambda run_id: _FakeEmitter())
    monkeypatch.setattr("app.services.chat_run_worker.start_owner_lease", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.chat_run_worker.stop_owner_lease", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.chat_run_worker._run_explicit_task_execution", _fake_run_explicit_task_execution)
    monkeypatch.setattr("app.services.chat_run_worker.hub.ensure_cancel_event", lambda run_id: asyncio.Event())
    monkeypatch.setattr("app.services.chat_run_worker.hub.ensure_steer_queue", lambda run_id: asyncio.Queue())
    monkeypatch.setattr("app.services.chat_run_worker.hub.register_worker_task", lambda run_id, task: None)
    monkeypatch.setattr("app.services.chat_run_worker.hub.forget_worker_task", lambda run_id: None)
    monkeypatch.setattr("app.services.chat_run_worker.hub.cleanup_run_signals", lambda run_id: None)
    monkeypatch.setattr("app.services.chat_run_worker.hub.drain_steer_messages", lambda run_id: [])

    asyncio.run(execute_chat_run("run-1"))

    assert called == {"process": 1, "direct": 0}

def test_execute_chat_run_binds_the_run_cancel_token_into_the_tool_context(
    monkeypatch,
) -> None:
    """The run's cancel token must be visible to the delegation that runs inside.

    ``code_executor`` / ``delegate_task`` supervise their CLI subprocess in a
    worker thread (``asyncio.to_thread``), so the token has to be bound in the
    run's context — and a stop request must flip the very object the delegation
    holds, without touching the loop-side ``asyncio.Event`` plumbing.
    """
    from app.services import cancellation
    from app.services import chat_run_hub as hub

    run_id = "run-cancel-token"
    request = ChatRequest(message="hello", session_id="session-1")
    seen: dict = {}

    async def _fake_process_unified_stream(*args, **kwargs):
        # What a delegation running inside this run would read.
        seen["token"] = cancellation.current_cancel_token()
        seen["registry_token"] = hub.cancel_token(run_id)
        hub.request_cancel(run_id)  # the user pressed stop
        if False:
            yield None

    agent = SimpleNamespace(
        extra_context={},
        session_id="session-1",
        history=[],
        process_unified_stream=_fake_process_unified_stream,
    )

    monkeypatch.setattr(
        "app.services.chat_run_worker.get_chat_run",
        lambda run_id: {"request_json": request.model_dump_json()},
    )
    monkeypatch.setattr("app.services.chat_run_worker.mark_chat_run_started", lambda run_id, **kwargs: True)
    monkeypatch.setattr(
        "app.services.chat_run_worker.mark_chat_run_finished",
        lambda run_id, status, **kwargs: True,
    )

    async def _fake_build_agent(req, **kwargs):
        return (agent, req.message)

    monkeypatch.setattr(
        "app.services.chat_run_worker.build_agent_for_chat_request", _fake_build_agent
    )
    monkeypatch.setattr("app.services.chat_run_worker.ChatRunEmitter", lambda run_id: _FakeEmitter())
    monkeypatch.setattr("app.services.chat_run_worker.start_owner_lease", lambda *a, **k: None)
    monkeypatch.setattr("app.services.chat_run_worker.stop_owner_lease", lambda *a, **k: None)
    monkeypatch.setattr("app.services.chat_run_worker._capture_quality_snapshot", lambda run_id: None)
    # Keep this test off the database entirely.
    monkeypatch.setattr("app.services.chat_run_worker.claim_chat_run_lease", lambda *a, **k: True)
    monkeypatch.setattr("app.services.chat_run_worker.release_chat_run_lease", lambda *a, **k: True)
    monkeypatch.setattr("app.services.chat_run_worker.heartbeat_chat_run_lease", lambda *a, **k: True)

    async def _noop_pump(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.chat_run_worker.run_signal_pump", _noop_pump)

    try:
        asyncio.run(execute_chat_run(run_id))

        token = seen.get("token")
        assert token is not None, "the run never reached the stream"
        # The delegation sees the run's registered token and a stop request flips
        # that exact object...
        assert token is seen.get("registry_token")
        assert token.cancelled is True
        assert token.reason == "chat_run_cancelled"
        # ...and the binding is gone once the run is over.
        assert cancellation.current_cancel_token() is None
        assert hub.cancel_token(run_id) is None
    finally:
        hub.cleanup_run_signals(run_id)
        cancellation.set_cancel_token(None)
