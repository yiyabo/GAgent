"""needs_review: a run that executed nothing and produced nothing.

A deep-think task with no primary execution tool call and no output is no
longer reported as a bare "success" — it surfaces as needs_review and a
human decides whether the prose answer suffices (P5, 2026-10-11).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.plans.executor_deepthink import (
    _node_declares_outputs,
    _task_ran_without_output,
)
from app.services.plans.plan_models import PlanNode
from app.services.plans.task_verification import TaskVerificationService


def _node(**kwargs) -> PlanNode:
    base = {"id": 1, "plan_id": 1, "name": "Explain", "status": "pending"}
    base.update(kwargs)
    return PlanNode(**base)


def test_outputless_run_is_needs_review() -> None:
    assert (
        _task_ran_without_output(
            node=_node(),
            result=SimpleNamespace(),
            tools_used=["file_operations"],
            payload={},
            tool_result_context={},
        )
        is True
    )


def test_primary_tool_run_is_not_needs_review() -> None:
    assert (
        _task_ran_without_output(
            node=_node(),
            result=SimpleNamespace(),
            tools_used=["code_executor"],
            payload={},
            tool_result_context={},
        )
        is False
    )


def test_declared_outputs_are_not_needs_review() -> None:
    node = _node(metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}})
    assert _node_declares_outputs(node) is True
    assert (
        _task_ran_without_output(
            node=node,
            result=SimpleNamespace(),
            tools_used=[],
            payload={},
            tool_result_context={},
        )
        is False
    )


def test_output_evidence_is_not_needs_review(tmp_path) -> None:
    # ``_has_recoverable_output_evidence`` only counts artifact paths that
    # actually point at an existing file/directory, so the path must be real.
    artifact = tmp_path / "out.csv"
    artifact.write_text("a,b\n", encoding="utf-8")
    assert (
        _task_ran_without_output(
            node=_node(),
            result=SimpleNamespace(),
            tools_used=[],
            payload={"artifact_paths": [str(artifact)]},
            tool_result_context={},
        )
        is False
    )


def test_finalize_payload_preserves_needs_review(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    node = _node(status="needs_review")
    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "needs_review", "content": "prose only", "metadata": {}},
        execution_status="needs_review",
    )

    assert finalization.final_status == "needs_review"
    assert finalization.payload["status"] == "needs_review"
    assert finalization.payload["metadata"]["failure_kind"] == "needs_review"


def test_status_after_audit_repair_never_demotes_needs_review() -> None:
    from app.routers.plan_routes.execution_jobs import _status_after_audit_repair

    assert _status_after_audit_repair("needs_review", {"success": False, "final_status": "failed"}) == "needs_review"
    assert _status_after_audit_repair("needs_review", {"success": True}) == "completed"
    assert _status_after_audit_repair("failed", {"success": False, "final_status": "failed"}) == "failed"
