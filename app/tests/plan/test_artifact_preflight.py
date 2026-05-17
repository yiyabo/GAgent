from __future__ import annotations

from app.services.plans.artifact_contracts import save_artifact_manifest
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


def test_preflight_ignores_same_task_artifact_self_cycle(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        131,
        PlanNode(
            id=46,
            plan_id=131,
            name="Load and normalize metadata",
            metadata={
                "artifact_contract": {
                    "requires": ["phage_ml.training_metadata_parquet"],
                    "publishes": ["phage_ml.training_metadata_parquet"],
                }
            },
        ),
    )

    result = service.validate_plan(131, tree)

    assert result.ok is True
    assert not [issue for issue in result.errors if issue.code == "artifact_cycle"]


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


def test_preflight_resolves_equivalent_hybrid_matrix_aliases(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        93,
        PlanNode(
            id=18,
            plan_id=93,
            name="Construct Hybrid Feature Matrix",
            metadata={"artifact_contract": {"publishes": ["phage_ml.hybrid_features_final_npz"]}},
        ),
        PlanNode(
            id=19,
            plan_id=93,
            name="Train traditional models",
            metadata={"artifact_contract": {"requires": ["ml_features.hybrid_matrix_npz"]}},
        ),
        PlanNode(
            id=24,
            plan_id=93,
            name="Train deep learning models",
            metadata={"artifact_contract": {"requires": ["phage_features.hybrid_matrix_npz"]}},
        ),
        PlanNode(
            id=32,
            plan_id=93,
            name="Kmer enrichment",
            metadata={"artifact_contract": {"requires": ["ml_phage.features_hybrid_matrix"]}},
        ),
    )

    result = service.validate_plan(93, tree)

    assert result.ok is True
    assert not [issue for issue in result.errors if issue.code == "missing_producer"]


def test_preflight_resolves_equivalent_model_directory_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        94,
        PlanNode(
            id=19,
            plan_id=94,
            name="Train traditional models",
            metadata={"artifact_contract": {"publishes": ["ml_traditional.model_checkpoints_dir"]}},
        ),
        PlanNode(
            id=27,
            plan_id=94,
            name="Evaluate models",
            metadata={"artifact_contract": {"requires": ["phage_ml.trained_models_dir"]}},
        ),
        PlanNode(
            id=39,
            plan_id=94,
            name="Package deployment",
            metadata={"artifact_contract": {"requires": ["ml_phage.best_model_pkl"]}},
        ),
    )

    result = service.validate_plan(94, tree)

    assert result.ok is True
    assert not [issue for issue in result.errors if issue.code == "missing_producer"]


def test_preflight_resolves_dynamic_directory_artifact_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    alias = "analysis_ccc.evidence_dataframes"
    data_dir = tmp_path / "outputs" / "task_1" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "lr_pairs.tsv").write_text("ligand\treceptor\nMIF\tCD74\n", encoding="utf-8")
    save_artifact_manifest(
        101,
        {
            "plan_id": 101,
            "artifacts": {
                alias: {
                    "alias": alias,
                    "path": str(data_dir.resolve()),
                    "producer_task_id": 1,
                    "source_path": str(data_dir.resolve()),
                    "validation": {"validated": True, "kind": "directory"},
                }
            },
        },
    )
    service = ArtifactPreflightService()
    tree = _tree(
        101,
        PlanNode(
            id=1,
            plan_id=101,
            name="Produce evidence dataframes",
            status="completed",
            metadata={"artifact_contract": {"publishes": [alias]}},
        ),
        PlanNode(
            id=2,
            plan_id=101,
            name="Consume evidence dataframes",
            metadata={"artifact_contract": {"requires": [alias]}},
        ),
    )

    result = service.validate_plan(101, tree)

    assert result.ok is True
    assert result.manifest_resolved_aliases[alias] == str(data_dir.resolve())
    assert not [issue for issue in result.warnings if issue.code == "unknown_artifact_alias"]
    assert not [issue for issue in result.warnings if issue.code == "completed_task_missing_publish"]


def test_supervised_ml_contract_requires_label_and_row_id_artifacts(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        95,
        PlanNode(
            id=14,
            plan_id=95,
            name="Prepare training metadata",
            metadata={"artifact_contract": {"publishes": ["phage_ml.training_metadata_parquet"]}},
        ),
        PlanNode(
            id=15,
            plan_id=95,
            name="Extract feature row IDs",
            metadata={"artifact_contract": {"publishes": ["phage_ml.feature_row_ids_json"]}},
        ),
        PlanNode(
            id=18,
            plan_id=95,
            name="Construct feature matrix",
            metadata={"artifact_contract": {"publishes": ["phage_ml.hybrid_features_final_npz"]}},
        ),
        PlanNode(
            id=19,
            plan_id=95,
            name="Train supervised classifiers",
            metadata={"artifact_contract": {
                "requires": ["ml_features.hybrid_matrix_npz"],
                "publishes": ["ml_traditional.validation_metrics_json", "ml_traditional.model_checkpoints_dir"],
            }},
        ),
    )

    result = service.validate_plan(95, tree)

    assert result.ok is True
    task19 = next(snapshot for snapshot in result.task_contracts if snapshot.task_id == 19)
    assert "phage_ml.hybrid_features_final_npz" in task19.requires
    assert "phage_ml.training_metadata_parquet" in task19.requires
    assert "phage_ml.feature_row_ids_json" in task19.requires
    assert "phage_ml.label_alignment_json" in task19.publishes


def test_supervised_ml_contract_blocks_missing_label_inputs(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = ArtifactPreflightService()
    tree = _tree(
        96,
        PlanNode(
            id=18,
            plan_id=96,
            name="Construct feature matrix",
            metadata={"artifact_contract": {"publishes": ["phage_ml.hybrid_features_final_npz"]}},
        ),
        PlanNode(
            id=19,
            plan_id=96,
            name="Train supervised classifiers",
            metadata={"artifact_contract": {
                "requires": ["ml_features.hybrid_matrix_npz"],
                "publishes": ["ml_traditional.validation_metrics_json", "ml_traditional.model_checkpoints_dir"],
            }},
        ),
    )

    result = service.validate_plan(96, tree)

    missing = [issue.alias for issue in result.errors if issue.code == "missing_producer"]
    assert result.ok is False
    assert "phage_ml.training_metadata_parquet" in missing
    assert "phage_ml.feature_row_ids_json" in missing
