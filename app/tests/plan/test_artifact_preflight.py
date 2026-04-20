from __future__ import annotations

from app.services.plans.artifact_preflight import ArtifactPreflightService
from app.services.plans.plan_models import PlanNode, PlanTree


def _tree(plan_id: int, *nodes: PlanNode) -> PlanTree:
    tree = PlanTree(id=plan_id, title=f"Plan {plan_id}")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def test_preflight_reports_missing_producer_for_required_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        11,
        PlanNode(
            id=2,
            plan_id=11,
            name="Consumer",
            metadata={"artifact_contract": {"requires": ["ai_dl.references_bib"]}},
        ),
    )

    result = service.validate_plan(11, tree)

    assert result.ok is False
    assert any(issue.code == "missing_producer" for issue in result.errors)
    assert result.task_contracts[0].contract_source == "explicit"


def test_preflight_detects_ambiguous_producers(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        12,
        PlanNode(
            id=1,
            plan_id=12,
            name="Producer A",
            metadata={"artifact_contract": {"publishes": ["ai_dl.evidence_md"]}},
        ),
        PlanNode(
            id=2,
            plan_id=12,
            name="Producer B",
            metadata={"artifact_contract": {"publishes": ["ai_dl.evidence_md"]}},
        ),
        PlanNode(
            id=3,
            plan_id=12,
            name="Consumer",
            metadata={"artifact_contract": {"requires": ["ai_dl.evidence_md"]}},
        ),
    )

    result = service.validate_plan(12, tree)

    assert result.ok is False
    ambiguous = [issue for issue in result.errors if issue.code == "ambiguous_producer"]
    assert len(ambiguous) == 1
    assert ambiguous[0].alias == "ai_dl.evidence_md"
    assert ambiguous[0].related_task_ids == [1, 2, 3]


def test_preflight_detects_artifact_cycle(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        13,
        PlanNode(
            id=1,
            plan_id=13,
            name="Task A",
            metadata={
                "artifact_contract": {
                    "requires": ["ai_dl.references_bib"],
                    "publishes": ["ai_dl.evidence_md"],
                }
            },
        ),
        PlanNode(
            id=2,
            plan_id=13,
            name="Task B",
            metadata={
                "artifact_contract": {
                    "requires": ["ai_dl.evidence_md"],
                    "publishes": ["ai_dl.references_bib"],
                }
            },
        ),
    )

    result = service.validate_plan(13, tree)

    assert result.ok is False
    assert any(issue.code == "artifact_cycle" for issue in result.errors)


def test_preflight_warns_when_completed_task_missing_canonical_publish(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        14,
        PlanNode(
            id=5,
            plan_id=14,
            name="Completed producer",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["ai_dl.references_bib"]}},
        ),
    )

    result = service.validate_plan(14, tree)

    assert result.ok is True
    assert any(issue.code == "completed_task_missing_publish" for issue in result.warnings)


def test_preflight_tracks_inferred_contract_provenance(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        15,
        PlanNode(
            id=7,
            plan_id=15,
            name="AI consumer",
            metadata={"paper_context_paths": ["ai_dl_references.bib"]},
        ),
    )

    result = service.validate_plan(15, tree)

    snapshot = result.task_contracts[0]
    assert snapshot.contract_source == "inferred"
    assert "ai_dl.references_bib" in snapshot.requires