"""Targeted tests for the `_is_internal_artifact_path` convergence (registry D5).

`PlanExecutor._is_internal_artifact_path` previously lacked the
`deliverables/manifest_latest.json` special case that
`TaskVerificationService._is_internal_artifact_path` (the artifact authority)
applies. The executor now delegates to the task-verification source (approved
behavior alignment): executor-side classification newly treats the canonical
session manifest path as internal (never a user deliverable).
"""

import pytest

from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.task_verification import TaskVerificationService


@pytest.mark.parametrize(
    "value",
    [
        "runtime/session_abc/deliverables/manifest_latest.json",
        "/data/runtime/sess-1/deliverables/manifest_latest.json",
        "runtime\\sess-1\\deliverables\\manifest_latest.json",
        "RUNTIME/sess-1/Deliverables/MANIFEST_LATEST.JSON",
    ],
)
def test_executor_manifest_special_case_now_internal(value):
    assert PlanExecutor._is_internal_artifact_path(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "runtime/sess-1/deliverables/manifest_latest.json",
        "runtime/sess-1/tool_outputs/job_42/step_1_build/result.json",
        "runtime/sess-1/tool_outputs/job_9/step_12_pack/preview.json",
        "output/tool_outputs/manifest.json",
        "runtime/sess-1/deliverables/final_report.md",
        "deliverables/report.md",
        "manifest_latest.json",
        "runtime/sess-1/tool_outputs/notes.txt",
        "",
        "/",
        "   ",
    ],
)
def test_executor_matches_task_verification(value):
    assert PlanExecutor._is_internal_artifact_path(value) == TaskVerificationService._is_internal_artifact_path(value)


def test_executor_internal_artifact_path_delegates_to_task_verification(monkeypatch):
    calls = []
    original = TaskVerificationService._is_internal_artifact_path

    def _spy(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(
        TaskVerificationService,
        "_is_internal_artifact_path",
        staticmethod(_spy),
    )
    PlanExecutor._is_internal_artifact_path("runtime/s/deliverables/manifest_latest.json")
    assert calls == ["runtime/s/deliverables/manifest_latest.json"]
