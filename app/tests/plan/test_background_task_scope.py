import threading
from types import SimpleNamespace

from fastapi import Request
from app.routers import plan_routes
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.artifact_preflight import ArtifactPreflightResult
from app.services.request_principal import RequestPrincipal


def test_selected_scope_returns_before_worker_and_rejects_overlapping_run(monkeypatch):
    nodes = {i: PlanNode(id=i, plan_id=185, name=f'Task {i}', status='pending') for i in (2, 4, 5, 6)}
    nodes[2].status = 'completed'
    nodes[4].dependencies = [2]
    nodes[5].dependencies = [4]
    tree = PlanTree(id=185, title='Review', nodes=nodes, adjacency={None: list(nodes)})
    monkeypatch.setattr(plan_routes, '_load_authorized_plan_tree', lambda *a: tree)
    monkeypatch.setattr(plan_routes, '_resolve_effective_task_states', lambda *a: {
        i: {'effective_status': node.status} for i, node in nodes.items()})
    monkeypatch.setattr(plan_routes._artifact_preflight_service, 'validate_plan',
                        lambda *a, **k: ArtifactPreflightResult(plan_id=185))
    jobs = []
    def create(**kwargs):
        jobs.append(kwargs)
        return SimpleNamespace(job_id='job-scope', status='queued',
                               to_payload=lambda: {'job_id': 'job-scope', 'status': 'queued'})
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, 'create_job', create)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, 'append_log', lambda *a: None)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def worker(**kwargs):
        assert kwargs['task_order'] == [4, 5] and kwargs['owner_id'] == 'alice'
        started.set()
        release.wait(3)
        finished.set()
    monkeypatch.setattr(plan_routes, '_run_task_chain_job', worker)
    raw = Request({'type': 'http', 'state': {'principal': RequestPrincipal(user_id='alice', email=None)}})
    plan_routes._task_execution_locks.clear()
    try:
        result = plan_routes.execute_task_with_dependencies(4, plan_id=185, raw_request=raw,
            request=plan_routes.ExecuteTaskRequest(selected_task_ids=[4, 5], include_subtasks=False))
        assert result.success and started.wait(1) and not finished.is_set()
        assert result.dependency_plan.execution_order == [4, 5]
        assert jobs[0]['metadata']['requested_task_ids'] == [4, 5]
        assert 6 not in jobs[0]['metadata']['task_order']
        duplicate = plan_routes.execute_task_with_dependencies(5, plan_id=185, raw_request=raw,
            request=plan_routes.ExecuteTaskRequest(include_subtasks=False))
        assert not duplicate.success and len(jobs) == 1
        assert plan_routes._acquire_plan_execution_lock(185, 0) is None
    finally:
        release.set()
        assert finished.wait(2)
        plan_routes._task_execution_locks.clear()


def test_full_plan_lock_blocks_individual_task_submission():
    plan_routes._task_execution_locks.clear()
    full = plan_routes._acquire_plan_execution_lock(185, 0)
    try:
        assert full is not None
        assert plan_routes._acquire_plan_execution_lock(185, 4) is None
    finally:
        plan_routes._release_plan_execution_lock(185, 0, full)
