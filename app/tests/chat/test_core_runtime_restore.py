"""Cached native plan operations restore the real outer core stream binding."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.database import get_db, init_db
from app.llm import NativeToolCall
from app.routers import chat_routes
from app.routers.chat import agent as agent_module
from app.services.chat_run_state import chat_run_claim
from app.services.deep_think import checkpointing
from app.services.deep_think_agent import DeepThinkAgent, DeepThinkResult
from app.services.execution.async_tool_executor import PendingToolCall, execute_with_concurrency
from app.services.plans.plan_models import PlanTree
from app.services.plans.plan_session import PlanSession
from app.tests.chat import test_unified_stream_event_golden as golden


class CachedLedger:
    run_id = 'restore-proof'
    def __init__(self, payload): self.payload = payload
    def prepare(self, *args, **kwargs):
        return SimpleNamespace(action='replay', step=SimpleNamespace(key=SimpleNamespace(attempt=1)),
            result={'tool_result': dict(self.payload), 'tool_result_text': 'confirmed plan', 'evidence': []})


@pytest.fixture
def core(isolated_app_env, monkeypatch):
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO plans(id,title) VALUES(34,'Old plan'),(77,'Restored plan')")
        conn.execute("INSERT INTO chat_sessions(id,owner_id,plan_id,plan_title) VALUES('sess-golden','legacy-local',34,'Old plan')")
        conn.commit()
    golden._patch_runtime(monkeypatch)
    trees = {id_: PlanTree(id=id_, title=title, nodes={}, adjacency={})
             for id_, title in [(34, 'Old plan'), (77, 'Restored plan')]}
    agent = golden._build_agent(extra_context={'plan_new_requested': True})
    agent.plan_session = PlanSession(repo=SimpleNamespace(get_plan_tree=lambda id_: trees[id_]), plan_id=34)
    agent.plan_tree = trees[34]
    decision = golden._decision('Continue the interrupted plan')
    profile = replace(golden._profile(), available_tools=['plan_operation'])
    agent._resolve_request_routing = lambda _: (decision, profile)
    executions = []
    async def forbidden(name, **params):
        executions.append(name)
        raise AssertionError('cached restore must not call a tool executor')
    monkeypatch.setattr(agent_module, 'execute_tool', forbidden)
    return agent, executions


def brain_class(payload=None, checkpoint_state=None, followup_create=False, observed=None):
    class RestoreBrain(DeepThinkAgent):
        def __init__(self, *, on_runtime_restore=None, request_profile=None, **kwargs):
            super().__init__(on_runtime_restore=on_runtime_restore, request_profile=request_profile, **kwargs)
            self._step_ledger = CachedLedger(payload)
            self._checkpoint_namespace = 'native:restore-proof'
            if observed is not None:
                observed.append(self)
        async def think(self, user_query, context=None, task_context=None):
            if checkpoint_state is not None:
                await self._restore_runtime_state(checkpoint_state)
            else:
                call = NativeToolCall(id='cached-plan', name='plan_operation', arguments={'operation': payload['operation']})
                async def never(): raise AssertionError('the cached executor was invoked')
                await checkpointing.execute_recorded_tool(self, call, 1, 0, never)
            if followup_create:
                reused = await self.tool_executor('plan_operation', {'operation': 'create', 'title': 'Do not create again'})
                assert reused['plan_id'] == 77
            return DeepThinkResult('The existing plan is ready.', [], 1, ['plan_operation'], 0.8, 'restored')
    return RestoreBrain


@pytest.mark.parametrize('operation', ['bind', 'create'])
def test_cached_plan_operation_restores_outer_binding_and_sql_session_without_tool_calls(core, monkeypatch, operation):
    agent, executions = core
    observed = []
    payload = {'success': True, 'operation': operation, 'plan_id': 77, 'plan_title': 'Restored plan'}
    monkeypatch.setattr(chat_routes, 'DeepThinkAgent', brain_class(payload, followup_create=operation == 'create', observed=observed))
    handle = chat_run_claim.set(('restore-proof', 'claim-proof'))
    try:
        events = golden._run(agent, 'Continue the interrupted plan')
    finally:
        chat_run_claim.reset(handle)
    assert executions == []
    assert agent.plan_session.plan_id == 77 and agent.plan_tree.id == 77
    assert agent.extra_context['plan_id'] == 77
    assert observed[0].request_profile['current_plan_id'] == 77
    assert events[-1]['type'] == 'final'
    with get_db() as conn:
        row = conn.execute("SELECT plan_id,plan_title FROM chat_sessions WHERE id='sess-golden'").fetchone()
        assert tuple(row) == (77, 'Restored plan')
    if operation == 'create':
        assert observed[0]._created_plan_this_turn_id == 77


def test_cached_create_with_binding_skipped_preserves_outer_binding(core, monkeypatch):
    agent, executions = core
    payload = {'success': True, 'operation': 'create', 'plan_id': 77, 'binding_skipped': True}
    monkeypatch.setattr(chat_routes, 'DeepThinkAgent', brain_class(payload))
    handle = chat_run_claim.set(('restore-proof', 'claim-proof'))
    try:
        golden._run(agent, 'Continue the interrupted plan')
    finally:
        chat_run_claim.reset(handle)
    assert executions == []
    assert agent.plan_session.plan_id == 34
    with get_db() as conn:
        assert conn.execute("SELECT plan_id FROM chat_sessions WHERE id='sess-golden'").fetchone()[0] == 34


def test_checkpoint_restores_unbound_state_in_outer_core_and_inner_profile(core, monkeypatch):
    agent, executions = core
    observed = []
    monkeypatch.setattr(chat_routes, 'DeepThinkAgent', brain_class(checkpoint_state={
        'bound_plan_id': None, 'bound_plan_title': None, 'created_plan_this_turn_id': None,
    }, observed=observed))
    golden._run(agent, 'Continue the interrupted plan')
    assert executions == []
    assert agent.plan_session.plan_id is None and agent.plan_tree is None
    assert agent.extra_context['plan_id'] is None
    assert observed[0].request_profile['current_plan_id'] is None
    with get_db() as conn:
        assert conn.execute("SELECT plan_id FROM chat_sessions WHERE id='sess-golden'").fetchone()[0] is None


def test_outer_runtime_restore_failure_emits_error_and_never_continues_writes(core, monkeypatch):
    agent, executions = core
    monkeypatch.setattr(agent_module, '_set_session_plan_id', lambda *args: (_ for _ in ()).throw(RuntimeError('session unavailable')))
    monkeypatch.setattr(chat_routes, 'DeepThinkAgent', brain_class({'success': True, 'operation': 'create', 'plan_id': 77}, followup_create=True))
    handle = chat_run_claim.set(('restore-proof', 'claim-proof'))
    try:
        events = golden._run(agent, 'Continue the interrupted plan')
    finally:
        chat_run_claim.reset(handle)
    assert executions == []
    assert any(event['type'] == 'error' for event in events)
    assert not any(event['type'] == 'final' for event in events)


@pytest.mark.parametrize('parallel', [False, True])
def test_runtime_restore_failure_is_not_converted_to_a_recoverable_tool_result(parallel):
    later = []
    async def broken():
        raise checkpointing.ControllerRestoreError('restore failed')
    async def write():
        later.append('write')
        return {'success': True}
    calls = [PendingToolCall(0, 'restore', broken, is_concurrent_safe=parallel)]
    if parallel:
        calls.append(PendingToolCall(1, 'reader', lambda: asyncio.sleep(0, result={'success': True}), is_concurrent_safe=True))
    calls.append(PendingToolCall(2, 'writer', write, is_concurrent_safe=False))
    with pytest.raises(checkpointing.ControllerRestoreError):
        asyncio.run(execute_with_concurrency(calls))
    assert later == []
