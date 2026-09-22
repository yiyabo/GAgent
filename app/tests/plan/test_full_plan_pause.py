from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import job_routes, plan_routes
from app.services.plans.decomposition_jobs import PlanDecompositionJobManager
from app.services.plans.plan_models import PlanNode, PlanTree


class _PausableJobStore:
    def __init__(self, *, paused: bool = False) -> None:
        self.running_calls = 0
        self.success_calls: list[dict] = []
        self.failure_calls: list[dict] = []
        self.stats_calls: list[dict] = []
        self.gate = threading.Event()
        if not paused:
            self.gate.set()
        self.wait_calls = 0

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

    def is_execution_paused(self, _job_id: str) -> bool:
        return not self.gate.is_set()

    def wait_while_paused(self, _job_id: str, poll_seconds: float = 1.0) -> bool:
        self.wait_calls += 1
        while True:
            if self.gate.wait(timeout=0.05):
                return True


def _build_tree() -> PlanTree:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="pending"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()
    return tree


def _patch_execution_layer(monkeypatch, store, tree, execution_order: list[int]) -> None:
    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        tree.nodes[task_id].status = "completed"
        tree.nodes[task_id].execution_result = json.dumps({"status": "completed", "content": "ok"})
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(
        plan_routes,
        "_resolve_effective_task_states",
        lambda _plan_id, _tree, **kwargs: {
            tid: {"effective_status": "pending"} for tid in tree.nodes
        },
    )


def test_full_plan_job_holds_dispatch_while_paused_then_resumes(monkeypatch) -> None:
    store = _PausableJobStore(paused=True)
    tree = _build_tree()
    execution_order: list[int] = []
    _patch_execution_layer(monkeypatch, store, tree, execution_order)

    worker = threading.Thread(
        target=plan_routes._run_full_plan_job,
        kwargs={"job_id": "job-pause-1", "plan_id": 7, "task_order": [1, 2]},
        daemon=True,
    )
    worker.start()
    time.sleep(0.4)

    assert worker.is_alive()
    assert execution_order == []
    assert store.wait_calls >= 1

    store.gate.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert execution_order == [1, 2]
    assert len(store.success_calls) == 1
    assert store.failure_calls == []


def test_full_plan_job_dispatch_unblocked_when_never_paused(monkeypatch) -> None:
    store = _PausableJobStore(paused=False)
    tree = _build_tree()
    execution_order: list[int] = []
    _patch_execution_layer(monkeypatch, store, tree, execution_order)

    plan_routes._run_full_plan_job(job_id="job-pause-2", plan_id=7, task_order=[1, 2])

    assert execution_order == [1, 2]
    assert store.wait_calls == 0


def test_execution_pause_flag_manager_semantics() -> None:
    manager = PlanDecompositionJobManager()
    job = manager.create_job(plan_id=7, task_id=None, mode="full_plan", job_type="plan_execute")
    manager.mark_running(job.job_id)

    assert manager.is_execution_paused(job.job_id) is False
    assert manager.wait_while_paused(job.job_id, poll_seconds=0.01) is True

    assert manager.set_execution_paused(job.job_id, True) is True
    assert manager.is_execution_paused(job.job_id) is True
    assert job.metadata["execution_paused"] is True
    assert manager.set_execution_paused(job.job_id, True) is True

    assert manager.set_execution_paused(job.job_id, False) is True
    assert manager.is_execution_paused(job.job_id) is False
    assert job.metadata["execution_paused"] is False

    manager.mark_success(job.job_id, result={"ok": True})
    assert manager.set_execution_paused(job.job_id, True) is False
    assert manager.set_execution_paused("missing-job", True) is False


def _build_job_client() -> TestClient:
    app = FastAPI()
    app.include_router(job_routes.job_router)
    return TestClient(app)


@pytest.mark.anyio
def test_control_endpoint_pauses_plan_job_without_runtime_controller(monkeypatch) -> None:
    manager = PlanDecompositionJobManager()
    job = manager.create_job(plan_id=7, task_id=None, mode="full_plan", job_type="plan_execute")
    manager.mark_running(job.job_id)

    monkeypatch.setattr(job_routes, "plan_decomposition_jobs", manager)

    async def _no_ws_route(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(job_routes, "route_control_message", _no_ws_route)

    client = _build_job_client()
    try:
        response = client.post(f"/jobs/{job.job_id}/control", json={"action": "pause"})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert manager.is_execution_paused(job.job_id) is True
        assert job.metadata["execution_paused"] is True

        response = client.post(f"/jobs/{job.job_id}/control", json={"action": "resume"})
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert manager.is_execution_paused(job.job_id) is False
    finally:
        client.close()


def test_control_endpoint_rejects_pause_for_terminal_job(monkeypatch) -> None:
    manager = PlanDecompositionJobManager()
    job = manager.create_job(plan_id=7, task_id=None, mode="full_plan", job_type="plan_execute")
    manager.mark_running(job.job_id)
    manager.mark_success(job.job_id, result={"ok": True})

    monkeypatch.setattr(job_routes, "plan_decomposition_jobs", manager)

    async def _no_ws_route(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(job_routes, "route_control_message", _no_ws_route)

    client = _build_job_client()
    try:
        response = client.post(f"/jobs/{job.job_id}/control", json={"action": "pause"})
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert manager.is_execution_paused(job.job_id) is False
    finally:
        client.close()


def test_board_item_exposes_execution_paused_flag() -> None:
    payload = {
        "status": "running",
        "job_type": "plan_execute",
        "mode": "full_plan",
        "plan_id": None,
        "metadata": {"execution_paused": True},
    }
    item = job_routes._build_plan_execute_board_item("job-board-1", payload)
    assert item.execution_paused is True

    payload_default = {
        "status": "running",
        "job_type": "plan_execute",
        "mode": "full_plan",
        "plan_id": None,
        "metadata": {},
    }
    item_default = job_routes._build_plan_execute_board_item("job-board-2", payload_default)
    assert item_default.execution_paused is False
