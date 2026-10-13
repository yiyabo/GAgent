import asyncio
from types import SimpleNamespace

from app.routers.chat.plan_job_handoff import enqueue_chat_plan_job
from app.services.request_principal import get_request_owner_id


def test_chat_handoff_uses_plan_route_with_durable_owner_and_async_mode(monkeypatch):
    from app.routers import plan_routes

    seen = {}

    def execute(task_id, *, plan_id, raw_request, request):
        seen.update(task_id=task_id, plan_id=plan_id,
                    owner=get_request_owner_id(raw_request), request=request)
        return SimpleNamespace(success=True, message="queued", job={"job_id": "one"})

    monkeypatch.setattr(plan_routes, "execute_task_with_dependencies", execute)
    agent = SimpleNamespace(extra_context={"owner_id": "alice"}, session_id="session-a",
                            plan_session=SimpleNamespace(plan_id=185))
    response = asyncio.run(enqueue_chat_plan_job(agent, 4))
    assert response.job["job_id"] == "one"
    assert (seen["plan_id"], seen["task_id"], seen["owner"]) == (185, 4, "alice")
    assert seen["request"].async_mode is True
    assert seen["request"].include_dependencies is True
    assert seen["request"].include_subtasks is True


def test_chat_handoff_rejects_missing_durable_owner():
    agent = SimpleNamespace(extra_context={}, session_id="session-a",
                            plan_session=SimpleNamespace(plan_id=185))
    try:
        asyncio.run(enqueue_chat_plan_job(agent, 4))
    except ValueError as exc:
        assert "owner" in str(exc)
    else:
        raise AssertionError("missing owner must not start a job")
