from __future__ import annotations

import json

from app.services.plans.artifact_contracts import canonical_artifact_path, save_artifact_manifest
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans.task_verification import TaskVerificationService, VerificationFinalization


def _tree(plan_id: int, *nodes: PlanNode) -> PlanTree:
    tree = PlanTree(id=plan_id, title=f"Plan {plan_id}")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def test_status_resolver_marks_completed_task_failed_when_canonical_publish_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Create an artifact manifest with at least one entry so that artifact
    # tracking is considered active.  Without a manifest the resolver
    # gracefully skips publish-contract checks (plans executed via DeepThink
    # may never initialise a manifest).
    save_artifact_manifest(21, {
        "plan_id": 21,
        "artifacts": {
            "other.placeholder": {
                "alias": "other.placeholder",
                "path": "/tmp/placeholder",
                "producer_task_id": 999,
            }
        },
    })
    resolver = PlanStatusResolver()
    tree = _tree(
        21,
        PlanNode(
            id=1,
            plan_id=21,
            name="Produce evidence",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        ),
    )

    state = resolver.resolve_plan_states(21, tree)[1]

    assert state["effective_status"] == "failed"
    assert state["missing_publish_aliases"] == ["general.evidence_md"]
    assert state["reason_code"] == "publish_contract_missing"


def test_status_resolver_marks_completed_task_completed_when_canonical_publish_exists(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    alias = "general.evidence_md"
    canonical = canonical_artifact_path(22, alias)
    assert canonical is not None
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("hello", encoding="utf-8")
    save_artifact_manifest(
        22,
        {
            "plan_id": 22,
            "artifacts": {
                alias: {
                    "alias": alias,
                    "path": str(canonical.resolve()),
                    "producer_task_id": 1,
                    "source_path": str(canonical.resolve()),
                }
            },
        },
    )
    resolver = PlanStatusResolver()
    tree = _tree(
        22,
        PlanNode(
            id=1,
            plan_id=22,
            name="Produce evidence",
            status="completed",
            metadata={"artifact_contract": {"publishes": [alias]}},
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        ),
    )

    state = resolver.resolve_plan_states(22, tree)[1]

    assert state["effective_status"] == "completed"
    assert state["missing_publish_aliases"] == []
    assert state["published_aliases"] == [alias]


def test_status_resolver_keeps_legacy_completed_task_completed_without_publish_contract(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = PlanStatusResolver()
    tree = _tree(
        23,
        PlanNode(
            id=1,
            plan_id=23,
            name="Legacy task",
            status="completed",
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        ),
    )

    state = resolver.resolve_plan_states(23, tree)[1]

    assert state["effective_status"] == "completed"
    assert state["contract_source"] == "none"


def test_status_resolver_keeps_structured_completed_report_completed_when_prose_mentions_failures(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = PlanStatusResolver()
    tree = _tree(
        29,
        PlanNode(
            id=1,
            plan_id=29,
            name="Submit verified report",
            status="completed",
            execution_result=json.dumps(
                {
                    "status": "completed",
                    "content": (
                        "# Task Complete: Verified Report Deliverables Successfully Submitted\n\n"
                        "The quality audit confirmed all publishable-paper gates passed. "
                        "The report also documents failed intermediate checks, blocked "
                        "release states, and retry guidance for reproducibility."
                    ),
                }
            ),
        ),
    )

    state = resolver.resolve_plan_states(29, tree)[1]

    assert state["effective_status"] == "completed"
    assert state["reason_code"] == "completed"


def test_status_resolver_marks_verification_failure_failed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = PlanStatusResolver()
    tree = _tree(
        24,
        PlanNode(
            id=1,
            plan_id=24,
            name="Verified task",
            status="completed",
            execution_result=json.dumps(
                {
                    "status": "completed",
                    "content": "ok",
                    "metadata": {"verification_status": "failed"},
                }
            ),
        ),
    )

    state = resolver.resolve_plan_states(24, tree)[1]

    assert state["effective_status"] == "failed"
    assert state["verification_status"] == "failed"


def test_status_resolver_marks_active_background_job_running(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = PlanStatusResolver()
    tree = _tree(
        25,
        PlanNode(id=1, plan_id=25, name="Queued task", status="pending"),
    )

    state = resolver.resolve_plan_states(25, tree, snapshot={"active_task_ids": {1}})[1]

    assert state["effective_status"] == "running"
    assert state["is_active_execution"] is True


def test_status_resolver_preserves_retryable_skipped_status(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = PlanStatusResolver()
    tree = _tree(
        26,
        PlanNode(
            id=1,
            plan_id=26,
            name="Skipped task",
            status="skipped",
            execution_result=json.dumps(
                {
                    "status": "skipped",
                    "content": "Temporary issue; retry later.",
                    "metadata": {"blocked_by_dependencies": False},
                }
            ),
        ),
    )

    state = resolver.resolve_plan_states(26, tree)[1]

    assert state["effective_status"] == "skipped"
    assert state["reason_code"] == "skipped_retryable"


def test_task_verifier_demotes_completed_payload_when_publish_contract_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    verifier = TaskVerificationService()
    node = PlanNode(
        id=1,
        plan_id=26,
        name="Produce evidence",
        status="completed",
        metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
    )
    finalization = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={"status": "completed", "content": "ok", "metadata": {}},
    )

    finalization = verifier.apply_artifact_authority(26, node, finalization)

    assert finalization.final_status == "failed"
    assert finalization.payload["status"] == "failed"
    assert finalization.payload["metadata"]["failure_kind"] == "artifact_publish_missing"


def test_status_resolver_keeps_manually_accepted_task_completed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = PlanStatusResolver()
    tree = _tree(
        27,
        PlanNode(
            id=1,
            plan_id=27,
            name="Reviewed task",
            status="completed",
            execution_result=json.dumps(
                {
                    "status": "completed",
                    "content": "candidate files downloaded",
                    "metadata": {
                        "verification_status": "failed",
                        "manual_acceptance": {
                            "status": "accepted",
                            "reason": "Artifacts are usable after manual review.",
                        },
                    },
                }
            ),
        ),
    )

    state = resolver.resolve_plan_states(27, tree)[1]

    assert state["effective_status"] == "completed"
    assert state["reason_code"] == "manual_acceptance"
    assert state["manual_acceptance_active"] is True


def test_task_verifier_keeps_manually_accepted_publish_gap_completed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    verifier = TaskVerificationService()
    node = PlanNode(
        id=1,
        plan_id=28,
        name="Produce evidence",
        status="completed",
        metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
    )
    finalization = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={
            "status": "completed",
            "content": "ok",
            "metadata": {
                "manual_acceptance": {
                    "status": "accepted",
                    "reason": "Reviewed and accepted.",
                }
            },
        },
    )

    finalization = verifier.apply_artifact_authority(28, node, finalization)

    assert finalization.final_status == "completed"
    assert finalization.payload["status"] == "completed"
    assert finalization.payload["metadata"]["artifact_authority"]["status"] == "failed"
