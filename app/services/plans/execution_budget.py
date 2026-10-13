"""Plan-wide deadlines, task windows shared across retries, and truthful aborts."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from contextlib import contextmanager
from functools import wraps

from app.services.cancellation import CancelToken, current_cancel_token, set_cancel_token, reset_cancel_token
from app.services.foundation.settings import get_settings
from app.services.run_budget import (
    RunBudget, RunDeadlineExceeded, bind_run_budget, reset_run_budget,
    current_run_budget, check_run_active,
)

logger = logging.getLogger(__name__)
_task_deadlines = contextvars.ContextVar("plan_task_deadlines", default=None)


def enabled() -> bool:
    return bool(getattr(get_settings(), "plan_execution_budget_enabled", False))


@contextmanager
def _scope(seconds, *, synthesis_reserve):
    check_run_active()
    if seconds <= 0:
        raise RunDeadlineExceeded("Plan execution budget exhausted before dispatch.")
    settings = get_settings()
    token = CancelToken(parent=current_cancel_token())
    budget = RunBudget(seconds, getattr(settings, "plan_close_reserve_seconds", 10), token,
                       synthesis_reserve_seconds=synthesis_reserve)
    budget_handle = bind_run_budget(budget)
    token_handle = set_cancel_token(token)
    try:
        check_run_active()
        yield budget
    finally:
        budget.close()
        reset_cancel_token(token_handle)
        reset_run_budget(budget_handle)


@contextmanager
def plan_job_scope():
    if not enabled() or _task_deadlines.get() is not None:
        yield current_run_budget()
        return
    state_handle = _task_deadlines.set({})
    try:
        # Synthesis belongs to an individual task; the enclosing job reserves
        # only cleanup time and bounds every task, repair and inter-task pause.
        with _scope(getattr(get_settings(), "plan_job_budget_seconds", 43200), synthesis_reserve=0) as budget:
            yield budget
    finally:
        _task_deadlines.reset(state_handle)


@contextmanager
def plan_task_scope(plan_id, task_id):
    if not enabled():
        yield current_run_budget()
        return
    check_run_active()
    settings = get_settings()
    deadlines = _task_deadlines.get()
    task_key = (plan_id, task_id)
    duration = getattr(settings, "plan_task_budget_seconds", 7200)
    proposed = {'deadline': time.monotonic() + duration, 'terminated': False,
                'cleanup': min(getattr(settings, "plan_close_reserve_seconds", 10), duration / 2)}
    window = deadlines.setdefault(task_key, proposed) if deadlines is not None else proposed
    active_deadline = window['deadline'] - window['cleanup']
    if window['terminated'] or time.monotonic() >= active_deadline:
        raise RunDeadlineExceeded("Task execution budget exhausted; no retry was dispatched.")
    try:
        with _scope(window['deadline']-time.monotonic(), synthesis_reserve=getattr(settings, "plan_synthesis_reserve_seconds", 120)) as budget:
            # A late retry must not turn reserved cleanup time into fresh work
            # when RunBudget clamps the reserve for a short remaining duration.
            budget.cancel_token.set_deadline(active_deadline)
            yield budget
    except (RunDeadlineExceeded, asyncio.CancelledError):
        window['terminated'] = True
        raise


def request_profile(plan_id, task_id, context):
    profile = {"current_plan_id": plan_id, "current_task_id": task_id,
               "plan_tree_edit_allowed": False,
               "session_id": context.get("session_id"), "owner_id": context.get("owner_id")}
    if enabled():
        budget = current_run_budget()
        profile.update(intent_type="execute_task", execution_lane="plan", plan_budget_enabled=True)
        if budget:
            profile["execution_budget"] = {"total_seconds": budget.total_seconds,
                "remaining_seconds": budget.remaining_seconds(),
                "synthesis_reserve_seconds": budget.effective_synthesis_reserve_seconds,
                "cleanup_reserve_seconds": budget.close_reserve_seconds}
    return profile


def run_task(executor, plan_id, node, callback):
    if not enabled():
        return callback()
    try:
        with plan_task_scope(plan_id, node.id):
            return callback()
    except (RunDeadlineExceeded, asyncio.CancelledError) as exc:
        reason = "run_deadline_exceeded" if isinstance(exc, RunDeadlineExceeded) else "cancelled"
        # Persist a deterministic failure receipt after the child scope has
        # unwound. No tools, model calls or artifact promotion happen here.
        payload = {"status": "failed", "content": "Task stopped: " + reason,
                   "metadata": {"execution_status": "failed", "termination_reason": reason,
                                "failure_kind": reason, "verification_status": "unchecked"}}
        try:
            executor._repo.update_task(plan_id, node.id, status="failed", execution_result=json.dumps(payload))
        except Exception:
            logger.warning("Could not persist task termination plan=%s task=%s", plan_id, node.id, exc_info=True)
        raise


def bounded_plan_task(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with plan_task_scope(kwargs['plan_id'], kwargs['task_id']):
            return function(*args, **kwargs)
    return wrapped


def bounded_plan_job(manager_factory=None):
    """Wrap synchronous plan entrypoints without changing their public signature."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            try:
                with plan_job_scope():
                    return function(*args, **kwargs)
            except (RunDeadlineExceeded, asyncio.CancelledError) as exc:
                if manager_factory is None or not kwargs.get("job_id"):
                    raise
                manager = manager_factory()
                job_id = kwargs["job_id"]
                reason = "run_deadline_exceeded" if isinstance(exc, RunDeadlineExceeded) else "cancelled"
                job = manager.get_job(job_id)
                # Never resurrect a removed/cancelled/terminal job after a late callback.
                if job is not None and job.status in {"queued", "running"}:
                    manager.mark_failure(job_id, "Plan execution stopped: " + reason,
                        result={"plan_id": kwargs.get("plan_id"), "termination_reason": reason,
                                "partial": True}, stats=dict(job.stats))
                return None
        return wrapped
    return decorate
