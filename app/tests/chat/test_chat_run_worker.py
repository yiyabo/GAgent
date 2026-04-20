import asyncio
from types import SimpleNamespace

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
    monkeypatch.setattr("app.services.chat_run_worker.mark_chat_run_started", lambda run_id: None)
    monkeypatch.setattr("app.services.chat_run_worker.mark_chat_run_finished", lambda run_id, status, error=None: None)
    monkeypatch.setattr(
        "app.services.chat_run_worker.build_agent_for_chat_request",
        lambda req, save_user_message=False: (agent, req.message),
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