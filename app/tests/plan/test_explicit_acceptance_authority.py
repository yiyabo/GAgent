from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.services.plans.plan_models import PlanNode, PlanTree
from app.config.executor_config import get_executor_settings
from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans.task_metadata_generator import ensure_task_metadata
from app.services.plans.task_verification import (
    TaskVerificationService,
    VerificationFinalization,
)


@pytest.mark.parametrize("trigger", ["auto", "manual"])
def test_explicit_blocking_value_failure_rejects_nonempty_output(tmp_path, trigger):
    output = tmp_path / "result.json"
    output.write_text('{"total": 5}', encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Produce the required result",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [
                    {
                        "type": "json_field_equals",
                        "path": str(output),
                        "key_path": "total",
                        "expected": 100,
                    }
                ],
            }
        },
    )
    result = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(output)]}},
        execution_status="completed",
        trigger=trigger,
    )
    assert result.final_status == "failed"
    assert result.verification["status"] == "failed"
    assert result.verification["blocking"] is True
    assert result.verification["failures"][0]["actual"] == 5


@pytest.mark.parametrize("hard", [False, True])
def test_explicit_nonblocking_failure_remains_advisory(tmp_path, hard):
    output = tmp_path / "result.json"
    output.write_text('{"total": 5}', encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Optional check",
        metadata={
            "acceptance_criteria": {
                "blocking": False,
                "checks": [
                    {
                        "type": "json_field_equals",
                        "path": str(output),
                        "key_path": "total",
                        "expected": 100,
                        "hard": hard,
                    }
                ],
            },
        },
    )
    result = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(output)]}},
    )
    assert result.final_status == "completed"
    assert result.verification["authoritative"] is False
    assert not TaskVerificationService.has_authoritative_verification_failure(
        node, result.payload["metadata"]
    )


@pytest.mark.parametrize(
    "hard, expected_status", [(False, "completed"), (True, "failed")]
)
def test_generated_checks_only_block_for_failed_hard_integrity_gates(
    tmp_path, monkeypatch, hard, expected_status
):
    output = tmp_path / "result.json"
    output.write_text('{"total": 5}', encoding="utf-8")
    node = PlanNode(id=1, plan_id=1, name="Generate result")
    verifier = TaskVerificationService()
    monkeypatch.setattr(
        verifier,
        "_effective_acceptance_criteria",
        lambda _: (
            {
                "blocking": True,
                "checks": [
                    {
                        "type": "json_field_equals",
                        "path": str(output),
                        "key_path": "total",
                        "expected": 100,
                        "hard": hard,
                    }
                ],
            },
            True,
        ),
    )
    monkeypatch.setattr(verifier, "_llm_arbitrate_verification", lambda **_: False)
    result = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(output)]}},
    )
    assert result.final_status == expected_status
    assert result.verification["authoritative"] is hard
    assert (
        TaskVerificationService.has_authoritative_verification_failure(
            node, result.payload["metadata"]
        )
        is hard
    )


@pytest.mark.parametrize("enrich", [False, True])
def test_inferred_filename_and_aliases_remain_advisory(tmp_path, monkeypatch, enrich):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "scratch.md"
    output.write_text("Partial notes", encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Generate result",
        instruction="Save to results/expected.md",
        metadata=ensure_task_metadata(
            None, "Generate result", "Save to results/expected.md"
        )
        if enrich
        else {},
    )
    verifier = TaskVerificationService()
    monkeypatch.setattr(verifier, "_llm_arbitrate_verification", lambda **_: False)
    result = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(output)]}},
    )
    result = verifier.apply_artifact_authority(
        1, node, result, manifest={"artifacts": {}}
    )
    assert result.final_status == "completed"
    assert result.verification["status"] == "warning"
    assert result.verification["generated"] is True
    assert (
        result.payload["metadata"]["artifact_authority"]["has_explicit_contract"]
        is False
    )
    node.status = result.final_status
    node.execution_result = json.dumps(result.payload)
    tree = PlanTree(id=1, title="Advisory plan", nodes={1: node})
    tree.rebuild_adjacency()
    assert (
        PlanStatusResolver().resolve_plan_states(1, tree, manifest={"artifacts": {}})[
            1
        ]["effective_status"]
        == "completed"
    )


@pytest.mark.parametrize("source", ["bare", "enriched", "explicit"])
def test_production_metadata_provenance_survives_materialization_and_dependencies(
    tmp_path, monkeypatch, source
):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "scratch.md"
    output.write_text("Partial notes", encoding="utf-8")
    criteria = {
        "blocking": True,
        "checks": [{"type": "file_nonempty", "path": "results/expected.md"}],
    }
    metadata = (
        {}
        if source == "bare"
        else ensure_task_metadata(
            {"acceptance_criteria": criteria} if source == "explicit" else None,
            "Generate result",
            "Save to results/expected.md",
        )
    )
    producer = PlanNode(
        id=1,
        plan_id=1,
        name="Generate result",
        instruction="Save to results/expected.md",
        metadata=metadata,
    )
    consumer = PlanNode(id=2, plan_id=1, name="Continue analysis", dependencies=[1])
    tree = PlanTree(id=1, title="Provenance plan", nodes={1: producer, 2: consumer})
    tree.rebuild_adjacency()
    repo = MagicMock()
    repo.get_plan_tree.return_value = tree

    def update_task(_plan_id, task_id, **changes):
        for key, value in changes.items():
            setattr(tree.nodes[task_id], key, value)
        return tree.nodes[task_id]

    repo.update_task.side_effect = update_task
    executor = PlanExecutor(
        repo=repo,
        llm_service=MagicMock(),
        settings=replace(
            get_executor_settings(),
            plan_task_execution_backend="internal",
        ),
    )
    monkeypatch.setattr(
        executor._task_verifier, "_llm_arbitrate_verification", lambda **_: False
    )
    result = executor._task_verifier.finalize_payload(
        producer,
        {"status": "completed", "metadata": {"artifact_paths": [str(output)]}},
    )
    result, _ = executor._materialize_finalization(
        1, producer, result, session_context={}
    )
    states = executor._status_resolver.resolve_plan_states(1, tree)
    expected = "failed" if source == "explicit" else "completed"
    assert result.final_status == expected
    assert result.verification["generated"] is (source != "explicit")
    assert states[1]["effective_status"] == expected
    assert states[1]["authoritative_publish_aliases"] == []
    assert states[2]["incomplete_dependencies"] == ([1] if source == "explicit" else [])


def test_stored_inferred_criteria_keep_source_and_do_not_become_explicit(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    enriched = ensure_task_metadata(
        None, "Generate result", "Save to results/expected.md"
    )
    metadata = {
        "acceptance_criteria": enriched["acceptance_criteria"],
        "verification_status": "failed",
        "verification": {
            "status": "failed",
            "generated": False,
            "blocking": True,
            "failures": [{"type": "file_nonempty", "success": False}],
        },
    }
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Reloaded result",
        execution_result=json.dumps({"status": "completed", "metadata": metadata}),
    )
    _, generated = TaskVerificationService()._effective_acceptance_criteria(node)
    assert generated is True
    assert (
        TaskVerificationService.has_authoritative_verification_failure(node, metadata)
        is False
    )


def test_enriched_inferred_tsv_still_enforces_hard_integrity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "result.tsv"
    output.write_text("name\tvalue\n", encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Generate table",
        instruction="Save to result.tsv",
        metadata=ensure_task_metadata(
            None,
            "Generate table",
            "Save to result.tsv",
        ),
    )
    result = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(output)]}},
    )
    assert result.final_status == "failed"
    assert result.verification["generated"] is True
    assert result.verification["authoritative"] is True
    assert (
        TaskVerificationService.has_authoritative_verification_failure(
            node, result.payload["metadata"]
        )
        is True
    )


@pytest.mark.parametrize(
    "contract_key, expected_status", [("publishes", "failed"), ("requires", "skipped")]
)
def test_successful_delegation_does_not_bypass_explicit_artifact_contract(
    contract_key, expected_status
):
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Delegated task",
        metadata={
            "artifact_contract": {contract_key: ["general.evidence_md"]},
        },
    )
    result = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={
            "status": "completed",
            "metadata": {
                "delegated_task_execution": True,
                "executor": "code_executor",
                "delegation_status": "completed",
                "execution_success": True,
                "artifact_paths": ["unrelated.md"],
            },
        },
    )
    result = TaskVerificationService().apply_artifact_authority(
        1, node, result, manifest={"artifacts": {}}
    )
    assert result.final_status == expected_status
    assert result.payload["metadata"]["artifact_authority"]["status"] == "failed"


def test_manual_acceptance_preserves_audited_contract_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Reviewed task",
        status="completed",
        metadata={
            "artifact_contract": {
                "publishes": ["general.evidence_md"],
                "requires": ["general.references_bib"],
            },
        },
    )
    result = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={
            "status": "completed",
            "metadata": {
                "manual_acceptance": {
                    "status": "accepted",
                    "reason": "Reviewed alternative deliverable.",
                },
            },
        },
    )
    result = TaskVerificationService().apply_artifact_authority(
        1, node, result, manifest={"artifacts": {}}
    )
    node.execution_result = json.dumps(result.payload)
    tree = PlanTree(id=1, title="Reviewed plan", nodes={1: node})
    tree.rebuild_adjacency()
    assert result.final_status == "completed"
    assert (
        PlanStatusResolver().resolve_plan_states(1, tree, manifest={"artifacts": {}})[
            1
        ]["reason_code"]
        == "manual_acceptance"
    )


def test_legacy_explicit_rejection_blocks_completed_dependents_and_parent(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    producer = PlanNode(
        id=1,
        plan_id=1,
        name="Rejected output",
        status="completed",
        parent_id=3,
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [
                    {
                        "type": "json_field_equals",
                        "path": "result.json",
                        "key_path": "total",
                        "expected": 100,
                    }
                ],
            },
        },
        execution_result=json.dumps(
            {
                "status": "completed",
                "metadata": {
                    "execution_status": "completed",
                    "verification_status": "warning",
                    "verification": {
                        "status": "warning",
                        "blocking": False,
                        "generated": False,
                        "failures": [
                            {"type": "json_field_equals", "success": False, "actual": 5}
                        ],
                    },
                },
            }
        ),
    )
    consumer = PlanNode(
        id=2,
        plan_id=1,
        name="Dependent output",
        status="completed",
        dependencies=[1],
        execution_result=json.dumps({"status": "completed"}),
    )
    parent = PlanNode(id=3, plan_id=1, name="Aggregate", status="completed")
    tree = PlanTree(
        id=1, title="Rejected plan", nodes={1: producer, 2: consumer, 3: parent}
    )
    tree.rebuild_adjacency()
    states = PlanStatusResolver().resolve_plan_states(
        1, tree, manifest={"artifacts": {}}
    )
    assert states[1]["effective_status"] == "failed"
    assert states[1]["reason_code"] == "acceptance_rejected"
    assert states[2]["effective_status"] == "pending"
    assert states[2]["incomplete_dependencies"] == [1]
    assert states[3]["effective_status"] == "failed"


@pytest.mark.parametrize(
    "contract_key, expected_status", [("publishes", "failed"), ("requires", "pending")]
)
def test_completed_delegated_payload_cannot_override_missing_explicit_alias(
    tmp_path, monkeypatch, contract_key, expected_status
):
    monkeypatch.chdir(tmp_path)
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Delegated task",
        status="completed",
        metadata={
            "artifact_contract": {contract_key: ["general.evidence_md"]},
        },
        execution_result=json.dumps(
            {
                "status": "completed",
                "metadata": {
                    "verification_status": "passed",
                    "delegated_task_execution": True,
                    "delegation_status": "completed",
                    "execution_success": True,
                },
            }
        ),
    )
    tree = PlanTree(id=1, title="Delegated plan", nodes={1: node})
    tree.rebuild_adjacency()
    assert (
        PlanStatusResolver().resolve_plan_states(1, tree, manifest={"artifacts": {}})[
            1
        ]["effective_status"]
        == expected_status
    )
