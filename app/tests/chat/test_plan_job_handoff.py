import asyncio
import pytest
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


def test_retry_followup_prefers_recent_task_over_stale_session_binding(monkeypatch):
    from app.routers.chat import plan_job_handoff
    from app.routers.chat.agent import StructuredChatAgent
    from app.routers.chat.request_routing import resolve_request_routing

    called = []
    async def enqueue(agent, task_id):
        called.append(task_id)
        return SimpleNamespace(success=True, message="Queued", job={"job_id": "job-4"})
    monkeypatch.setattr(plan_job_handoff, "enqueue_chat_plan_job", enqueue)
    decision = resolve_request_routing(message="重新执行吧", plan_id=185, current_task_id=3)
    agent = SimpleNamespace(
        _resolve_request_routing=lambda _: (decision, None),
        plan_session=SimpleNamespace(plan_id=185), session_id=None,
        extra_context={"current_task_id": 3, "owner_id": "alice"},
        history=[{"role": "assistant", "content": "计划 #185 的 Task #4「综述」当前状态：失败。"},
                 {"role": "user", "content": "重新执行吧"}, {"role": "assistant", "content": ""}],
    )
    async def collect():
        return [item async for item in StructuredChatAgent.process_unified_stream(agent, "重新执行吧")]
    events = asyncio.run(collect())
    assert called == [4] and len(events) == 1 and 'job-4' in events[0]


@pytest.mark.parametrize('message', ['这样啊，你重新执行一下吧', '明白了，帮我重试一下吧', '那你重新运行一遍吧'])
def test_colloquial_retry_uses_existing_task_scope(message):
    from app.routers.chat.plan_execution_intent import resolve_plan_execution_target
    from app.routers.chat.request_routing import resolve_request_routing
    agent = SimpleNamespace(plan_session=SimpleNamespace(plan_id=185), extra_context={'current_task_id':4}, history=[])
    assert resolve_plan_execution_target(agent, resolve_request_routing(message=message, plan_id=185, current_task_id=4)) == (True, [4])


def test_multi_task_handoff_submits_one_scope(monkeypatch):
    from app.routers import plan_routes

    def execute(task_id, *, plan_id, raw_request, request):
        assert task_id == 4 and request.selected_task_ids == [4, 5]
        return SimpleNamespace(success=True, message="queued", job={"job_id": "scope-one"})
    monkeypatch.setattr(plan_routes, "execute_task_with_dependencies", execute)
    agent = SimpleNamespace(extra_context={"owner_id": "alice"}, session_id="session-a",
                            plan_session=SimpleNamespace(plan_id=185))
    assert asyncio.run(enqueue_chat_plan_job(agent, [4, 5])).job['job_id'] == 'scope-one'


def test_unspecified_retry_does_not_default_to_entire_plan():
    from app.routers.chat.plan_execution_intent import resolve_plan_execution_target
    from app.routers.chat.request_routing import resolve_request_routing

    agent = SimpleNamespace(plan_session=SimpleNamespace(plan_id=185), extra_context={}, history=[])
    decision = resolve_request_routing(message='重新执行吧', plan_id=185)
    assert resolve_plan_execution_target(agent, decision) == (True, [])


def test_ordinary_chat_and_unbound_tools_remain_inline():
    from app.routers.chat.plan_execution_intent import resolve_plan_execution_target
    from app.routers.chat.request_routing import resolve_request_routing
    agent = SimpleNamespace(plan_session=SimpleNamespace(plan_id=None), extra_context={}, history=[])
    assert resolve_plan_execution_target(agent, resolve_request_routing(message='重新执行吧')) == (False, [])
    agent.plan_session.plan_id = 185
    agent.extra_context['current_task_id'] = 4
    assert resolve_plan_execution_target(agent, resolve_request_routing(message='读取这个csv给我看看', plan_id=185)) == (False, [])
    assert resolve_plan_execution_target(agent, resolve_request_routing(message='开始执行吧', plan_id=185, current_task_id=4)) == (True, [4])
