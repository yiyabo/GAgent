from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.routers import plan_routes


@dataclass
class _FakeExecResult:
    status: str = "completed"
    duration_sec: float | None = 0.1
    content: str = "ok"


def test_run_task_chain_job_emits_task_progress_events(monkeypatch) -> None:
    events: List[Dict[str, Any]] = []
    stats_updates: List[Dict[str, Any]] = []

    monkeypatch.setattr(
        plan_routes,
        "log_job_event",
        lambda level, message, metadata=None: events.append(
            {"level": level, "message": message, "metadata": metadata or {}}
        ),
    )
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "mark_running",
        lambda job_id: None,
    )
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "update_stats",
        lambda job_id, stats: stats_updates.append({"job_id": job_id, "stats": stats}),
    )
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "mark_success",
        lambda job_id, result=None, stats=None: None,
    )
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "mark_failure",
        lambda job_id, error, result=None, stats=None: None,
    )
    monkeypatch.setattr(
        plan_routes._plan_executor,
        "execute_task",
        lambda plan_id, task_id, config=None: _FakeExecResult(),
    )

    plan_routes._run_task_chain_job(
        job_id="job-progress-1",
        plan_id=1,
        target_task_id=10,
        task_order=[2, 3, 10],
        deep_think=True,
        session_id="sess-1",
    )

    task_progress_events = [
        e for e in events if e.get("metadata", {}).get("sub_type") == "task_progress"
    ]
    assert task_progress_events
    assert any(e["metadata"].get("step") == 1 for e in task_progress_events)
    assert any(e["metadata"].get("step") == 3 for e in task_progress_events)
    assert all(e["metadata"].get("total") == 3 for e in task_progress_events)
    assert stats_updates


def test_run_task_chain_job_passes_paper_mode_into_execution_config(
    monkeypatch,
) -> None:
    captured_modes: List[bool] = []

    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "mark_running", lambda job_id: None)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "update_stats", lambda job_id, stats: None)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "mark_success", lambda job_id, result=None, stats=None: None)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "mark_failure", lambda job_id, error, result=None, stats=None: None)

    def _fake_execute_task(_plan_id, _task_id, config=None):
        captured_modes.append(bool(getattr(config, "paper_mode", False)))
        return _FakeExecResult()

    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _fake_execute_task)

    plan_routes._run_task_chain_job(
        job_id="job-paper-mode-1",
        plan_id=1,
        target_task_id=3,
        task_order=[1, 2, 3],
        deep_think=True,
        session_id="sess-paper",
        paper_mode=True,
    )

    assert captured_modes == [True, True, True]


def test_execute_task_request_reads_plan_paper_mode_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PLAN_PAPER_MODE_DEFAULT", "true")
    request = plan_routes.ExecuteTaskRequest()
    assert request.paper_mode is True
