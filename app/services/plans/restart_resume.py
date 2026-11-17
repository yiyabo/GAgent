"""Re-launch Plan execution jobs that a server restart interrupted.

A deploy or crash kills the worker threads of running chain/full-plan jobs.
Startup records them (``list_interrupted_plan_jobs``) before the stale-job
sweep marks them failed, and this module starts an equivalent new job for each
one. Completed tasks are reused by the job runners, so the new job continues
where the old one stopped. Every hop is bounded so a crash loop cannot rerun a
job forever.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

RESUME_COUNT_KEY = "restart_resume_count"
RESUMED_FROM_KEY = "resumed_from_job_id"
RESUME_NOTE = "Resumed automatically after a server restart."


def enabled() -> bool:
    raw = os.getenv("PLAN_JOB_AUTO_RESUME_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def max_resumes() -> int:
    try:
        value = int(os.getenv("PLAN_JOB_AUTO_RESUME_MAX", "3"))
    except ValueError:
        value = 3
    return max(0, min(value, 10))


def _note_on_old_job(row: dict[str, Any], message: str, metadata: dict[str, Any]) -> None:
    from app.repository.plan_storage import append_decomposition_job_log

    try:
        append_decomposition_job_log(
            row["plan_id"],
            job_id=row["job_id"],
            timestamp=datetime.now(timezone.utc),
            level="info",
            message=message,
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - a log line must not block recovery
        logger.debug("could not annotate interrupted job %s", row.get("job_id"), exc_info=True)


def _remaining_order(row: dict[str, Any], tree: Any) -> list[int]:
    order = (row.get("metadata") or {}).get("task_order") or []
    result: list[int] = []
    for value in order:
        try:
            task_id = int(value)
        except (TypeError, ValueError):
            continue
        if tree.has_node(task_id) and task_id not in result:
            result.append(task_id)
    return result


def _lineage(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    count = int(metadata.get(RESUME_COUNT_KEY) or 0) + 1
    metadata.update(
        {
            RESUME_COUNT_KEY: count,
            RESUMED_FROM_KEY: row["job_id"],
            "resume_reason": "server_restart",
        }
    )
    return metadata


def _start_chain(facade: Any, row: dict[str, Any], order: list[int], metadata: dict[str, Any]) -> str | None:
    plan_id = int(row["plan_id"])
    params = dict(row.get("params") or {})
    target = row.get("target_task_id")
    target_task_id = int(target) if target is not None else order[-1]
    scope = sorted(set(order) | {int(t) for t in params.get("selected_task_ids") or [] if str(t).isdigit()})
    locks = []

    def release() -> None:
        for tid, lock in reversed(locks):
            facade._release_plan_execution_lock(plan_id, tid, lock)
        locks.clear()

    for tid in scope:
        lock = facade._acquire_plan_execution_lock(plan_id, tid)
        if lock is None:
            release()
            return None
        locks.append((tid, lock))
    metadata["task_order"] = order
    params["steps"] = len(order)
    try:
        job = facade.plan_decomposition_jobs.create_job(
            plan_id=plan_id, task_id=target_task_id, mode="task_chain", job_type="plan_execute",
            owner_id=row.get("owner_id"), session_id=row.get("session_id"), params=params, metadata=metadata,
        )
        facade.plan_decomposition_jobs.append_log(
            job.job_id, "info", RESUME_NOTE,
            {"resumed_from_job_id": row["job_id"], "task_order": order, "sub_type": "restart_resume"},
        )

        def run() -> None:
            try:
                facade._run_task_chain_job(
                    job_id=job.job_id, plan_id=plan_id, target_task_id=target_task_id, task_order=order,
                    deep_think=bool(params.get("deep_think", True)), session_id=row.get("session_id"),
                    paper_mode=bool(params.get("paper_mode", False)), owner_id=row.get("owner_id"),
                )
            finally:
                release()

        facade.threading.Thread(target=run, daemon=True, name=f"plan-resume-{job.job_id[:8]}").start()
        return job.job_id
    except Exception:
        release()
        raise


def _start_full_plan(facade: Any, row: dict[str, Any], order: list[int], metadata: dict[str, Any]) -> str | None:
    plan_id = int(row["plan_id"])
    params = dict(row.get("params") or {})
    lock = facade._acquire_plan_execution_lock(plan_id, 0)
    if lock is None:
        return None
    metadata["task_order"] = order
    params["steps"] = len(order)
    try:
        job = facade.plan_decomposition_jobs.create_job(
            plan_id=plan_id, task_id=None, mode="full_plan", job_type="plan_execute",
            owner_id=row.get("owner_id"), session_id=row.get("session_id"), params=params, metadata=metadata,
        )
        facade.plan_decomposition_jobs.append_log(
            job.job_id, "info", RESUME_NOTE,
            {"resumed_from_job_id": row["job_id"], "task_order": order, "sub_type": "restart_resume"},
        )

        def run() -> None:
            try:
                facade._run_full_plan_job(
                    job_id=job.job_id, plan_id=plan_id, task_order=order,
                    initial_completed_steps=int(params.get("initial_completed_steps") or 0),
                    overall_total_steps=params.get("overall_total_steps"),
                    deep_think=bool(params.get("deep_think", True)), session_id=row.get("session_id"),
                    paper_mode=bool(params.get("paper_mode", False)),
                    stop_on_failure=bool(params.get("stop_on_failure", True)),
                    dependency_block_mode=str(params.get("dependency_block_mode") or "warn"),
                    owner_id=row.get("owner_id"),
                )
            finally:
                facade._release_plan_execution_lock(plan_id, 0, lock)

        facade.threading.Thread(target=run, daemon=True, name=f"plan-resume-{job.job_id[:8]}").start()
        return job.job_id
    except Exception:
        facade._release_plan_execution_lock(plan_id, 0, lock)
        raise


def resume_one(row: dict[str, Any], *, facade: Any = None) -> dict[str, Any]:
    """Start a replacement job for one interrupted row; returns an outcome record."""
    outcome: dict[str, Any] = {"plan_id": row.get("plan_id"), "job_id": row.get("job_id"), "mode": row.get("mode")}
    count = int((row.get("metadata") or {}).get(RESUME_COUNT_KEY) or 0)
    if count >= max_resumes():
        _note_on_old_job(row, "Not resumed: automatic restart-resume limit reached.", {"resume_count": count})
        return {**outcome, "status": "limit_reached"}
    if facade is None:
        from app.routers import plan_routes as facade  # late import: router wiring
    tree = facade._plan_repo.get_plan_tree(int(row["plan_id"]))
    order = _remaining_order(row, tree)
    if not order:
        return {**outcome, "status": "no_tasks"}
    metadata = _lineage(row)
    starter = _start_chain if row.get("mode") == "task_chain" else _start_full_plan
    new_job_id = starter(facade, row, order, metadata)
    if new_job_id is None:
        return {**outcome, "status": "locked"}
    _note_on_old_job(row, f"Resumed automatically as job {new_job_id} after a server restart.",
                     {"resumed_as_job_id": new_job_id, "sub_type": "restart_resume"})
    return {**outcome, "status": "resumed", "new_job_id": new_job_id}


def resume_interrupted_plan_jobs(rows: list[dict[str, Any]], *, facade: Any = None) -> list[dict[str, Any]]:
    if not rows or not enabled():
        return []
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        try:
            outcomes.append(resume_one(row, facade=facade))
        except Exception as exc:
            logger.warning("Could not resume interrupted plan job %s: %s", row.get("job_id"), exc)
            outcomes.append({"plan_id": row.get("plan_id"), "job_id": row.get("job_id"), "status": "error",
                             "error": str(exc)})
    return outcomes


__all__ = [
    "RESUMED_FROM_KEY",
    "RESUME_COUNT_KEY",
    "enabled",
    "max_resumes",
    "resume_interrupted_plan_jobs",
    "resume_one",
]
