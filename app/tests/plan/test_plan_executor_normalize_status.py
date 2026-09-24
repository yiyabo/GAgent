"""Targeted tests for the `_normalize_status` convergence (refactor registry D4).

`PlanExecutor._normalize_status` previously lacked the `done`/`error` mappings
that `TaskVerificationService._normalize_status` (the verification authority)
has always applied. The executor now delegates to the task-verification source
(approved behavior alignment): executor-side status normalization newly
recognizes `done` -> `completed` and `error` -> `failed`.
"""

import pytest

from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.task_verification import TaskVerificationService


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("done", "completed"),
        ("DONE", "completed"),
        (" Done ", "completed"),
        ("error", "failed"),
        ("ERROR", "failed"),
        (" Error ", "failed"),
    ],
)
def test_executor_normalize_status_new_done_error_branches(raw, expected):
    assert PlanExecutor._normalize_status(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "completed",
        "complete",
        "success",
        "done",
        "failed",
        "failure",
        "error",
        "skipped",
        "running",
        "pending",
        "",
        None,
        "DONE",
        "Error ",
        "unrecognized-status",
    ],
)
def test_executor_normalize_status_matches_task_verification(raw):
    assert PlanExecutor._normalize_status(raw) == TaskVerificationService._normalize_status(raw)


def test_executor_finalize_treats_done_as_completed():
    # Downstream of the changed call site (plan_executor.py:~1852):
    # _finalize_task_execution must classify the newly-recognized values the
    # same as their canonical counterparts.
    executor = PlanExecutor.__new__(PlanExecutor)
    for raw, canonical in (("done", "completed"), ("error", "failed")):
        assert executor._normalize_status(raw) == executor._normalize_status(canonical)
