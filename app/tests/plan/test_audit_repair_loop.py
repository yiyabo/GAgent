from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.services.plans.audit_repair_loop import AuditRepairLoopConfig, AuditRepairLoopService
from app.services.plans.artifact_contracts import load_artifact_manifest, resolve_manifest_aliases
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.task_verification import TaskVerificationService, VerificationFinalization


def _tree(node: PlanNode) -> PlanTree:
    tree = PlanTree(id=node.plan_id, title="Audit Repair Test")
    tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def _finalization(status: str, payload: dict[str, object]) -> VerificationFinalization:
    return VerificationFinalization(
        final_status=status,
        execution_status=status,
        payload=payload,
        verification=None,
        artifact_paths=[],
    )


class _StaticVerifier(TaskVerificationService):
    def __init__(self, finalization: VerificationFinalization) -> None:
        self.finalization = finalization

    def verify_task(self, repo, *, plan_id: int, task_id: int, trigger: str = "manual", override_criteria=None, dry_run: bool = False):
        return self.finalization


def test_environment_failure_blocks_without_delegate_or_rerun():
    node = PlanNode(
        id=7,
        plan_id=122,
        name="Differential expression",
        status="failed",
        execution_result=json.dumps({"status": "failed"}),
    )
    repo = MagicMock()
    repo.get_plan_tree.return_value = _tree(node)
    verifier = _StaticVerifier(
        _finalization(
            "failed",
            {
                "status": "failed",
                "content": "R with TCGAbiolinks and DESeq2 Bioconductor packages not available",
                "metadata": {},
            },
        )
    )
    delegate = MagicMock()
    executor = MagicMock()
    service = AuditRepairLoopService(repo=repo, verifier=verifier, plan_executor=executor, delegate_executor=delegate)

    result = service.run_task_loop(plan_id=122, task_id=7)

    assert result.success is False
    assert result.classification == "environment_blocked"
    assert result.steps[0].action == "blocked"
    delegate.execute.assert_not_called()
    executor.execute_task.assert_not_called()
    repo.update_task.assert_called_once()


def test_contract_mismatch_delegate_repair_then_reaudits_to_pass():
    node = PlanNode(
        id=11,
        plan_id=122,
        name="Publish artifact",
        status="failed",
        metadata={
            "artifact_contract": {"publishes": ["general.evidence_md"]},
            "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "evidence.md"}]},
        },
        execution_result=json.dumps({"status": "failed"}),
    )
    repo = MagicMock()
    repo.get_plan_tree.return_value = _tree(node)
    verifier = MagicMock()
    verifier.verify_task.side_effect = [
        _finalization(
            "failed",
            {
                "status": "failed",
                "content": "contract mismatch",
                "metadata": {
                    "failure_kind": "contract_mismatch",
                    "contract_diff": {"missing_required_outputs": ["evidence.md"]},
                },
            },
        ),
        _finalization(
            "completed",
            {
                "status": "success",
                "content": "verified",
                "metadata": {"verification": {"status": "passed"}},
            },
        ),
    ]
    delegate_result = MagicMock()
    delegate_result.status = "completed"
    delegate_result.summary = "Repaired manifest path."
    delegate_result.executor = "qwen_code"
    delegate_result.executor_session_id = "repair-run"
    delegate_result.metadata = {}
    delegate_result.artifact_paths = ["results/plans/plan_122/general/evidence.md"]
    delegate = MagicMock()
    delegate.execute.return_value = delegate_result
    service = AuditRepairLoopService(repo=repo, verifier=verifier, plan_executor=MagicMock(), delegate_executor=delegate)

    result = service.run_task_loop(
        plan_id=122,
        task_id=11,
        config=AuditRepairLoopConfig(max_loops=2, max_task_repairs=1),
    )

    assert result.success is True
    assert result.final_status == "completed"
    assert [step.action for step in result.steps] == ["delegate_repair", "audit_passed"]
    delegate.execute.assert_called_once()
    assert repo.update_task.call_count == 1
    assert repo.update_task.call_args.kwargs["status"] == "pending"


def test_delegate_repair_publishes_matching_artifact_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "results" / "plans" / "plan_130" / "repairs" / "task_12" / "evidence.md"
    source.parent.mkdir(parents=True)
    source.write_text("repaired evidence", encoding="utf-8")
    node = PlanNode(
        id=12,
        plan_id=130,
        name="Publish evidence",
        status="failed",
        metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
        execution_result=json.dumps({"status": "failed"}),
    )
    repo = MagicMock()
    repo.get_plan_tree.return_value = _tree(node)
    delegate_result = MagicMock()
    delegate_result.status = "completed"
    delegate_result.summary = "Repaired evidence path."
    delegate_result.executor = "qwen_code"
    delegate_result.executor_session_id = "repair-run"
    delegate_result.metadata = {}
    delegate_result.artifact_paths = [str(source)]
    delegate = MagicMock()
    delegate.execute.return_value = delegate_result
    verifier = MagicMock()
    verifier.verify_task.side_effect = [
        _finalization(
            "failed",
            {
                "status": "failed",
                "content": "contract mismatch",
                "metadata": {
                    "failure_kind": "contract_mismatch",
                    "contract_diff": {"missing_required_outputs": ["evidence.md"]},
                },
            },
        ),
        _finalization("completed", {"status": "success", "content": "verified", "metadata": {}}),
    ]
    service = AuditRepairLoopService(repo=repo, verifier=verifier, plan_executor=MagicMock(), delegate_executor=delegate)

    result = service.run_task_loop(
        plan_id=130,
        task_id=12,
        config=AuditRepairLoopConfig(max_loops=2, max_task_repairs=1),
    )

    manifest = load_artifact_manifest(130)
    resolved = resolve_manifest_aliases(manifest, ["general.evidence_md"])
    assert result.success is True
    assert "general.evidence_md" in resolved
    assert Path(resolved["general.evidence_md"]).read_text(encoding="utf-8") == "repaired evidence"
    entry = manifest["artifacts"]["general.evidence_md"]
    assert entry["producer_task_id"] == 12


def test_delegate_repair_does_not_publish_outside_plan_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside" / "evidence.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("do not publish", encoding="utf-8")
    node = PlanNode(
        id=13,
        plan_id=131,
        name="Publish evidence",
        status="failed",
        metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
        execution_result=json.dumps({"status": "failed"}),
    )
    repo = MagicMock()
    repo.get_plan_tree.return_value = _tree(node)
    delegate_result = MagicMock()
    delegate_result.status = "completed"
    delegate_result.summary = "Returned outside evidence path."
    delegate_result.executor = "qwen_code"
    delegate_result.executor_session_id = "repair-run"
    delegate_result.metadata = {}
    delegate_result.artifact_paths = [str(outside)]
    delegate = MagicMock()
    delegate.execute.return_value = delegate_result
    verifier = MagicMock()
    verifier.verify_task.side_effect = [
        _finalization(
            "failed",
            {
                "status": "failed",
                "content": "contract mismatch",
                "metadata": {
                    "failure_kind": "contract_mismatch",
                    "contract_diff": {"missing_required_outputs": ["evidence.md"]},
                },
            },
        ),
        _finalization("failed", {"status": "failed", "content": "still missing", "metadata": {}}),
    ]
    service = AuditRepairLoopService(repo=repo, verifier=verifier, plan_executor=MagicMock(), delegate_executor=delegate)

    result = service.run_task_loop(
        plan_id=131,
        task_id=13,
        config=AuditRepairLoopConfig(max_loops=1, max_task_repairs=1),
    )

    manifest = load_artifact_manifest(131)
    assert result.success is False
    assert resolve_manifest_aliases(manifest, ["general.evidence_md"]) == {}
