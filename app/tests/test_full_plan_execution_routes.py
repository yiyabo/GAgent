from __future__ import annotations

import threading
from types import SimpleNamespace

from starlette.requests import Request

from app.routers import plan_routes
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.request_principal import RequestPrincipal


def _build_request(owner_id: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/plans/7/execute-full",
        "raw_path": b"/plans/7/execute-full",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"x-forwarded-user", owner_id.encode("utf-8"))],
        "client": ("testclient", 50000),
        "server": ("test", 80),
        "state": {},
    }
    request = Request(scope)
    request.state.principal = RequestPrincipal(
        user_id=owner_id,
        email=f"{owner_id}@example.com",
        auth_source="test",
    )
    return request


def _tree() -> PlanTree:
    nodes = {
        1: PlanNode(id=1, plan_id=7, name="Step 1", status="pending"),
        2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
    }
    tree = PlanTree(id=7, title="Plan 7", nodes=nodes)
    tree.rebuild_adjacency()
    return tree


class _JobStoreStub:
    def __init__(self) -> None:
        self.running_calls = 0
        self.success_calls: list[dict] = []
        self.failure_calls: list[dict] = []
        self.stats_calls: list[dict] = []

    def mark_running(self, _job_id: str) -> None:
        self.running_calls += 1

    def update_stats(self, _job_id: str, stats: dict) -> None:
        self.stats_calls.append(dict(stats))

    def mark_success(self, _job_id: str, **kwargs) -> None:
        self.success_calls.append(dict(kwargs))

    def mark_failure(self, _job_id: str, error: str, **kwargs) -> None:
        payload = dict(kwargs)
        payload["error"] = error
        self.failure_calls.append(payload)


def test_execute_full_plan_rejects_duplicate_run_before_creating_job(monkeypatch) -> None:
    tree = _tree()
    create_job_calls: list[dict] = []

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "create_job",
        lambda **kwargs: create_job_calls.append(kwargs),
    )

    lock = threading.Lock()
    assert lock.acquire(blocking=False) is True
    plan_routes._task_execution_locks.clear()
    plan_routes._task_execution_locks[(7, 0)] = lock
    try:
        response = plan_routes.execute_full_plan(
            7,
            _build_request("alice"),
            plan_routes.ExecuteFullPlanRequest(async_mode=True),
        )
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass
        plan_routes._task_execution_locks.clear()

    assert response.success is False
    assert "already being executed" in response.message
    assert create_job_calls == []


def test_run_full_plan_job_continues_after_exception_when_stop_on_failure_disabled(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        if task_id == 1:
            raise RuntimeError("boom")
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)

    plan_routes._run_full_plan_job(
        job_id="job-1",
        plan_id=7,
        task_order=[1, 2],
        stop_on_failure=False,
    )

    assert execution_order == [1, 2]
    assert store.success_calls == []
    assert len(store.failure_calls) == 1
    result = store.failure_calls[0]["result"]
    assert result["executed_task_ids"] == [2]
    assert result["failed_task_ids"] == [1]


def test_run_full_plan_job_skips_tasks_completed_after_queue_build(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="completed"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)

    plan_routes._run_full_plan_job(
        job_id="job-2",
        plan_id=7,
        task_order=[1, 2],
        stop_on_failure=False,
    )

    assert execution_order == [2]
    assert store.failure_calls == []
    assert len(store.success_calls) == 1
    result = store.success_calls[0]["result"]
    assert result["executed_task_ids"] == [1, 2]
    assert result["failed_task_ids"] == []
    assert result["skipped_task_ids"] == []
    assert result["steps"][0]["task_id"] == 1
    assert result["steps"][0]["status"] == "already_completed"
