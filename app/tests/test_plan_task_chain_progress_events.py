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

