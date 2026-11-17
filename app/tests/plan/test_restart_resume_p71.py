"""p71: work interrupted by a restart (deploy) is resumed automatically."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.database import init_db
from app.repository import plan_storage
from app.repository.plan_repository import PlanRepository
from app.services.plans import restart_resume


@pytest.fixture
def repo(isolated_app_env):
    init_db()
    return PlanRepository()


def _plan_with_tasks(repo: PlanRepository) -> tuple[int, list[int]]:
    plan = repo.create_plan("Resume me", owner="owner-1")
    ids = [repo.create_task(plan.id, name=f"T{i}", instruction=f"do {i}").id for i in range(3)]
    return plan.id, ids


def _record(plan_id: int, job_id: str, *, mode: str, status: str, task_order: list[int], **extra: Any) -> None:
    plan_storage.record_decomposition_job(
        plan_id, job_id=job_id, job_type="plan_execute", mode=mode, target_task_id=task_order[-1],
        owner_id="owner-1", session_id="sess-1", status=status,
        params={"deep_think": True, "paper_mode": False, "steps": len(task_order), **extra.get("params", {})},
        metadata={"task_order": task_order, **extra.get("metadata", {})},
    )


def test_interrupted_chain_and_full_plan_jobs_are_listed(repo):
    plan_id, ids = _plan_with_tasks(repo)
    _record(plan_id, "job-chain", mode="task_chain", status="running", task_order=ids)
    _record(plan_id, "job-full", mode="full_plan", status="queued", task_order=ids)
    _record(plan_id, "job-done", mode="task_chain", status="succeeded", task_order=ids)
    _record(plan_id, "job-single", mode="single_task", status="running", task_order=ids)

    rows = {row["job_id"]: row for row in plan_storage.list_interrupted_plan_jobs()}

    assert set(rows) == {"job-chain", "job-full"}
    assert rows["job-chain"]["metadata"]["task_order"] == ids
    assert rows["job-chain"]["owner_id"] == "owner-1" and rows["job-chain"]["session_id"] == "sess-1"


class _Facade:
    """Records launches instead of running agents."""

    def __init__(self, repo: PlanRepository) -> None:
        from app.services.plans.decomposition_jobs import plan_decomposition_jobs

        self._plan_repo = repo
        self.plan_decomposition_jobs = plan_decomposition_jobs
        self.threading = threading
        self.calls: list[tuple[str, dict]] = []
        self.locks: dict[tuple[int, int], threading.Lock] = {}
        self.done = threading.Event()

    def _acquire_plan_execution_lock(self, plan_id: int, task_id: int = 0):
        key = (plan_id, task_id)
        if key in self.locks:
            return None
        self.locks[key] = threading.Lock()
        return self.locks[key]

    def _release_plan_execution_lock(self, plan_id: int, task_id: int, lock) -> None:
        self.locks.pop((plan_id, task_id), None)

    def _run_task_chain_job(self, **kwargs) -> None:
        self.calls.append(("chain", kwargs))
        self.done.set()

    def _run_full_plan_job(self, **kwargs) -> None:
        self.calls.append(("full", kwargs))
        self.done.set()


def test_interrupted_chain_is_relaunched_with_same_order_owner_and_lineage(repo):
    plan_id, ids = _plan_with_tasks(repo)
    _record(plan_id, "job-old", mode="task_chain", status="running", task_order=ids)
    rows = plan_storage.list_interrupted_plan_jobs()
    plan_storage.fix_stale_jobs_on_startup()
    facade = _Facade(repo)

    outcomes = restart_resume.resume_interrupted_plan_jobs(rows, facade=facade)

    assert outcomes[0]["status"] == "resumed"
    assert facade.done.wait(5)
    kind, kwargs = facade.calls[0]
    assert kind == "chain"
    assert kwargs["task_order"] == ids and kwargs["target_task_id"] == ids[-1]
    assert kwargs["owner_id"] == "owner-1" and kwargs["session_id"] == "sess-1"
    new_job = plan_storage.load_decomposition_job(plan_id, outcomes[0]["new_job_id"])
    assert new_job is not None
    assert new_job["metadata"]["resumed_from_job_id"] == "job-old"
    assert new_job["metadata"]["restart_resume_count"] == 1
    old_job = plan_storage.load_decomposition_job(plan_id, "job-old")
    assert old_job is not None
    assert old_job["status"] == "failed"
    assert any("Resumed automatically" in log["message"] for log in old_job["logs"])


def test_full_plan_job_is_relaunched(repo):
    plan_id, ids = _plan_with_tasks(repo)
    _record(plan_id, "job-full", mode="full_plan", status="running", task_order=ids,
            params={"stop_on_failure": True, "dependency_block_mode": "block"})
    facade = _Facade(repo)

    outcomes = restart_resume.resume_interrupted_plan_jobs(plan_storage.list_interrupted_plan_jobs(), facade=facade)

    assert outcomes[0]["status"] == "resumed"
    assert facade.done.wait(5)
    kind, kwargs = facade.calls[0]
    assert kind == "full" and kwargs["task_order"] == ids
    assert kwargs["dependency_block_mode"] == "block" and kwargs["owner_id"] == "owner-1"


def test_resume_is_bounded_to_avoid_crash_loops(repo, monkeypatch):
    monkeypatch.setenv("PLAN_JOB_AUTO_RESUME_MAX", "2")
    plan_id, ids = _plan_with_tasks(repo)
    _record(plan_id, "job-loop", mode="task_chain", status="running", task_order=ids,
            metadata={"restart_resume_count": 2})
    facade = _Facade(repo)

    outcomes = restart_resume.resume_interrupted_plan_jobs(plan_storage.list_interrupted_plan_jobs(), facade=facade)

    assert outcomes[0]["status"] == "limit_reached" and facade.calls == []


def test_disabled_switch_and_busy_lock(repo, monkeypatch):
    plan_id, ids = _plan_with_tasks(repo)
    _record(plan_id, "job-x", mode="task_chain", status="running", task_order=ids)
    rows = plan_storage.list_interrupted_plan_jobs()
    facade = _Facade(repo)

    monkeypatch.setenv("PLAN_JOB_AUTO_RESUME_ENABLED", "0")
    assert restart_resume.resume_interrupted_plan_jobs(rows, facade=facade) == []
    monkeypatch.delenv("PLAN_JOB_AUTO_RESUME_ENABLED")

    facade.locks[(plan_id, ids[0])] = threading.Lock()  # someone else already runs it
    outcomes = restart_resume.resume_interrupted_plan_jobs(rows, facade=facade)
    assert outcomes[0]["status"] == "locked" and facade.calls == []


def test_deleted_tasks_are_dropped_from_the_resumed_order(repo):
    plan_id, ids = _plan_with_tasks(repo)
    _record(plan_id, "job-y", mode="task_chain", status="running", task_order=[*ids, 999])
    facade = _Facade(repo)

    outcomes = restart_resume.resume_interrupted_plan_jobs(plan_storage.list_interrupted_plan_jobs(), facade=facade)

    assert outcomes[0]["status"] == "resumed"
    assert facade.done.wait(5)
    assert facade.calls[0][1]["task_order"] == ids


def test_startup_lists_jobs_before_marking_them_failed():
    import inspect

    import app.main as app_main

    source = inspect.getsource(app_main.lifespan) if hasattr(app_main, "lifespan") else inspect.getsource(app_main)
    assert source.index("list_interrupted_plan_jobs()") < source.index("fix_stale_jobs_on_startup()")
    assert source.index("fix_stale_plan_task_statuses_on_startup()") < source.index("resume_interrupted_plan_jobs(")
