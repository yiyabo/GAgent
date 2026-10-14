"""Background plans have bounded task/plan scopes without cross-task cancellation."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.cancellation import CancelToken, current_cancel_token, set_cancel_token, reset_cancel_token
from app.services.foundation.settings import get_settings
from app.services.plans import execution_budget as pb
from app.services.plans.plan_executor import PlanExecutor, ExecutionConfig
from app.services.plans.plan_models import PlanNode
from app.services.plans.executor_text_utils import _run_coroutine_sync
from app.services.run_budget import RunBudget, RunDeadlineExceeded, bind_run_budget, reset_run_budget, current_run_budget, run_stage


@pytest.fixture
def policy(monkeypatch):
    settings = SimpleNamespace(plan_execution_budget_enabled=True, plan_task_budget_seconds=4,
        plan_job_budget_seconds=12, plan_close_reserve_seconds=.02, plan_synthesis_reserve_seconds=.1)
    monkeypatch.setattr(pb, 'get_settings', lambda: settings)
    return settings


def test_disabled_budget_keeps_execution_intent_without_creating_a_clock(policy):
    policy.plan_execution_budget_enabled = False
    with pb.plan_job_scope(), pb.plan_task_scope(1, 2):
        assert current_run_budget() is None
        profile = pb.request_profile(1, 2, {})
        assert profile['intent_type'] == 'execute_task'
        assert not profile.get('plan_budget_enabled')
        assert 'execution_budget' not in profile


def test_task_retry_keeps_deadline_and_sibling_is_not_closed(policy):
    with pb.plan_job_scope() as job:
        with pb.plan_task_scope(1, 2) as first:
            deadline = first.deadline_at
            profile = pb.request_profile(1, 2, {'session_id': 's', 'owner_id': 'u'})
            assert profile['intent_type'] == 'execute_task'
            assert profile['execution_budget']['synthesis_reserve_seconds'] == .1
        assert first.cancel_token.closed
        assert not job.cancel_token.cancelled
        with pb.plan_task_scope(1, 2) as retry:
            assert retry.deadline_at <= deadline + .002
            assert not retry.cancel_token.cancelled
        with pb.plan_task_scope(1, 3) as sibling:
            assert sibling.cancel_token is not first.cancel_token
            assert not sibling.cancel_token.cancelled
    assert job.cancel_token.closed
    assert current_run_budget() is None


def test_parent_deadline_wins_and_child_close_does_not_close_it(policy):
    parent = RunBudget(1, .02, CancelToken())
    handle = bind_run_budget(parent)
    try:
        with pb.plan_job_scope() as job, pb.plan_task_scope(1, 2) as task:
            assert task.deadline_at <= job.deadline_at <= parent.deadline_at + .002
        assert not parent.cancel_token.cancelled
        assert current_run_budget() is parent
    finally:
        reset_run_budget(handle)


async def test_parent_cancel_wakes_child_thread_wait():
    parent = CancelToken()
    child = CancelToken(parent=parent)
    waiter = asyncio.create_task(asyncio.to_thread(child.wait, 10))
    parent.set('user_cancelled')
    assert await asyncio.wait_for(waiter, .5)
    assert child.reason == 'user_cancelled'
    assert not parent.closed


async def test_plan_executor_scope_crosses_sync_bridge_and_hard_deadline(policy, monkeypatch, tmp_path):
    from app.services.plans import artifact_contracts
    monkeypatch.setattr(artifact_contracts, '_repo_root', lambda: tmp_path)
    policy.plan_task_budget_seconds = .08
    policy.plan_synthesis_reserve_seconds = 0
    writes = []
    executor = object.__new__(PlanExecutor)
    executor._repo = SimpleNamespace(update_task=lambda *a, **kw: writes.append(kw))
    node = PlanNode(id=2, plan_id=1, name='blocked tool', status='running')
    settled = []

    async def tool():
        assert current_run_budget() is not None
        try:
            await asyncio.Event().wait()
        finally:
            settled.append(True)

    def run(*args):
        return _run_coroutine_sync(run_stage(tool(), stage='plan-test-tool', timeout=100))

    executor._run_task_unbound = run
    with pb.plan_job_scope() as job:
        with pytest.raises(RunDeadlineExceeded):
            executor._run_task(1, node, None, ExecutionConfig())
        assert not job.cancel_token.cancelled
        with pytest.raises(RunDeadlineExceeded):
            executor._run_task(1, node, None, ExecutionConfig())
    assert settled == [True]  # retry never starts another mutating operation
    receipt = json.loads(writes[-1]['execution_result'])
    assert receipt['status'] == 'failed'
    assert receipt['metadata']['termination_reason'] == 'run_deadline_exceeded'


def test_job_deadline_is_not_success_and_terminal_job_is_not_resurrected(policy):
    job = SimpleNamespace(status='running', stats={'executed': 1})
    failures = []
    manager = SimpleNamespace(get_job=lambda _: job, mark_failure=lambda *a, **kw: failures.append((a, kw)))

    @pb.bounded_plan_job(lambda: manager)
    def job_worker(**kwargs):
        raise RunDeadlineExceeded('deadline')

    job_worker(job_id='j', plan_id=1)
    assert len(failures) == 1
    assert failures[0][1]['result']['termination_reason'] == 'run_deadline_exceeded'
    job.status = 'failed'
    job_worker(job_id='j', plan_id=1)
    assert len(failures) == 1


async def test_active_change_crosses_soft_window_without_replay(policy):
    policy.plan_task_budget_seconds = .25
    policy.plan_synthesis_reserve_seconds = .04
    calls = []
    with pb.plan_task_scope(1, 2) as budget:
        budget.started_at -= .17
        async def change():
            calls.append('started')
            await asyncio.sleep(.03)
            calls.append('settled')
        await run_stage(change(), stage='already-started-change')
        assert budget.should_finalize()
        assert not budget.cancel_token.cancelled
    assert calls == ['started', 'settled']


def test_plan_pause_observes_budget_without_a_live_model(policy):
    import threading
    from app.services.plans.decomposition_jobs import PlanDecompositionJobManager
    manager = object.__new__(PlanDecompositionJobManager)
    manager._lock = threading.RLock()
    manager._jobs = {'job': SimpleNamespace()}
    manager._execution_pause = {'job': threading.Event()}
    policy.plan_job_budget_seconds = .07
    with pb.plan_job_scope():
        with pytest.raises(RunDeadlineExceeded):
            manager.wait_while_paused('job', poll_seconds=.01)
