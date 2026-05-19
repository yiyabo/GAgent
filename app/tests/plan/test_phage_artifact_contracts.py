from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.services.plans.artifact_contracts import (
    aliases_for_file_name,
    canonical_artifact_path,
    candidate_filenames_for_alias,
    publish_artifact,
    resolve_manifest_aliases,
    save_artifact_manifest,
)
from app.services.plans.artifact_validation import validate_artifact
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.status_resolver import PlanStatusResolver


def _tree(plan_id: int, *nodes: PlanNode) -> PlanTree:
    tree = PlanTree(id=plan_id, title=f"Plan {plan_id}")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def _write_baseline_npz(path: Path, *, break_val_labels: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    val_y_genus = np.array([0, 1], dtype=np.int64) if break_val_labels else np.array([0], dtype=np.int64)
    np.savez(
        path,
        train_X_data=np.array([1.0, 2.0], dtype=np.float64),
        train_X_indices=np.array([0, 2], dtype=np.int32),
        train_X_indptr=np.array([0, 1, 2], dtype=np.int32),
        train_X_shape=np.array([2, 3], dtype=np.int64),
        train_y_genus=np.array([0, 1], dtype=np.int64),
        train_y_lifestyle=np.array([1, 0], dtype=np.int64),
        val_X_data=np.array([3.0], dtype=np.float64),
        val_X_indices=np.array([1], dtype=np.int32),
        val_X_indptr=np.array([0, 1], dtype=np.int32),
        val_X_shape=np.array([1, 3], dtype=np.int64),
        val_y_genus=val_y_genus,
        val_y_lifestyle=np.array([1], dtype=np.int64),
        test_X_data=np.array([4.0], dtype=np.float64),
        test_X_indices=np.array([2], dtype=np.int32),
        test_X_indptr=np.array([0, 1], dtype=np.int32),
        test_X_shape=np.array([1, 3], dtype=np.int64),
        test_y_genus=np.array([1], dtype=np.int64),
        test_y_lifestyle=np.array([0], dtype=np.int64),
        genus_encoder_classes=np.array(["A", "B"]),
        lifestyle_encoder_classes=np.array(["temperate", "virulent"]),
        feature_dim=np.array(3, dtype=np.int64),
    )


def test_phage_artifact_aliases_are_registered() -> None:
    aliases = [
        "phage_phi.genomic_stats_csv",
        "phage_phi.functional_features_csv",
        "phage_phi.full_feature_matrix_h5",
        "phage_ml.unified_feature_matrix_h5",
        "phage_ml.unified_feature_matrix_csv",
        "phage_phi.split_statistics_json",
        "phage_phi.split_report_md",
        "phage_phi.baseline_train_val_arrays",
        "phage_phi.advanced_model_input_raw",
        "phage_phi.tree_model_features",
        "phage_phi.dl_kmer_tensors",
        "phage_phi.advanced_model_ready_data",
        "phage_host.baseline_eval_by_genus_json",
        "phage_host.baseline_feature_importance_csv",
    ]

    for alias in aliases:
        assert canonical_artifact_path(105, alias) is not None
        assert candidate_filenames_for_alias(alias)


def test_source_candidate_basenames_do_not_create_inferred_publish_aliases() -> None:
    assert "phage_phi.genomic_stats_csv" not in aliases_for_file_name(
        "genomic_stats.csv",
        preferred_namespace="phage_phi",
    )
    assert "phage_host.baseline_feature_importance_csv" not in aliases_for_file_name(
        "unified_feature_importance_rankings.csv",
        preferred_namespace="phage_host",
    )

    assert "genomic_stats_clean.tsv" in candidate_filenames_for_alias("phage_phi.genomic_stats_csv")
    assert "unified_feature_importance_rankings.csv" in candidate_filenames_for_alias(
        "phage_host.baseline_feature_importance_csv"
    )


def test_standard_publish_accepts_phage_tsv_source_for_csv_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "runtime" / "task_65" / "features" / "genomic_stats_clean.tsv"
    source.parent.mkdir(parents=True)
    source.write_text(
        "".join(
            [
                "Phage_ID\tLength\tGC_Content\tCoding_Density\n",
                "AB002632.1\t8651\t44.52\t0.91\n",
            ]
        ),
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {"plan_id": 501, "artifacts": {}}

    entry = publish_artifact(
        plan_id=501,
        alias="phage_phi.genomic_stats_csv",
        source_path=str(source),
        producer_task_id=16,
        manifest=manifest,
    )

    assert entry is not None
    assert entry["validated"] is True
    assert entry["path"].endswith("results/plans/plan_501/phage_phi/features/genomic_stats.csv")
    save_artifact_manifest(501, manifest)
    assert resolve_manifest_aliases(manifest, ["phage_phi.genomic_stats_csv"]) == {
        "phage_phi.genomic_stats_csv": entry["path"]
    }

    resolver = PlanStatusResolver()
    tree = _tree(
        501,
        PlanNode(
            id=16,
            plan_id=501,
            name="Compute Genomic Statistics from Phage Sequences",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["phage_phi.genomic_stats_csv"]}},
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        ),
    )

    state = resolver.resolve_plan_states(501, tree)[16]

    assert state["effective_status"] == "completed"
    assert state["published_aliases"] == ["phage_phi.genomic_stats_csv"]
    assert state["missing_publish_aliases"] == []


def test_baseline_train_val_npz_validator_accepts_structured_csr_components(tmp_path: Path) -> None:
    artifact = tmp_path / "baseline_train_val.npz"
    _write_baseline_npz(artifact)

    result = validate_artifact("phage_phi.baseline_train_val_arrays", str(artifact))

    assert result.validated is True
    assert result.schema_valid is True
    assert result.metadata["row_count"] == 4
    assert result.metadata["feature_dim"] == 3
    assert result.metadata["splits"]["train"] == {"shape": [2, 3], "nnz": 2}


def test_baseline_train_val_npz_validator_rejects_label_row_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "baseline_train_val.npz"
    _write_baseline_npz(artifact, break_val_labels=True)

    result = validate_artifact("phage_phi.baseline_train_val_arrays", str(artifact))

    assert result.validated is False
    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "val_y_genus" in result.failure_reason


def test_baseline_train_val_npz_validator_rejects_object_arrays(tmp_path: Path) -> None:
    artifact = tmp_path / "baseline_train_val.npz"
    _write_baseline_npz(artifact)
    with np.load(str(artifact), allow_pickle=False) as archive:
        payload = {key: archive[key] for key in archive.files}
    payload["train_y_genus"] = np.array([{ "unsafe": "object" }], dtype=object)
    np.savez(artifact, **payload)

    result = validate_artifact("phage_phi.baseline_train_val_arrays", str(artifact))

    assert result.validated is False
    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "Object arrays cannot be loaded when allow_pickle=False" in result.failure_reason or "object dtype" in result.failure_reason


def test_advanced_model_input_raw_validator_does_not_unpickle_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "advanced_model_input_raw.pkl"
    artifact.write_bytes(b"not a pickle, but non-empty")

    result = validate_artifact("phage_phi.advanced_model_input_raw", str(artifact))

    assert result.validated is True
    assert result.schema_valid is True
    assert result.kind == "file_nonempty"


def test_dl_kmer_tensor_alias_uses_hdf5_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h5py = pytest.importorskip("h5py")
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "runtime" / "task_100" / "data" / "dl_kmer_tensors.h5"
    source.parent.mkdir(parents=True)
    with h5py.File(source, "w") as handle:
        handle.create_dataset("train/X", data=np.ones((2, 3), dtype=np.float32))
        handle.create_dataset("val/X", data=np.ones((1, 3), dtype=np.float32))
        handle.create_dataset("test/X", data=np.ones((1, 3), dtype=np.float32))
    manifest: dict[str, Any] = {"plan_id": 502, "artifacts": {}}

    entry = publish_artifact(
        plan_id=502,
        alias="phage_phi.dl_kmer_tensors",
        source_path=str(source),
        producer_task_id=100,
        manifest=manifest,
    )

    assert entry is not None
    assert entry["validated"] is True
    assert entry["path"].endswith("results/plans/plan_502/phage_phi/models/dl_kmer_tensors.h5")
    assert entry["validation"]["kind"] == "hdf5_table"


def test_split_assignment_alias_accepts_structured_json(tmp_path: Path) -> None:
    artifact = tmp_path / "split_assignments.json"
    artifact.write_text(
        json.dumps(
            {
                "cluster_to_split": {"cluster_a": "train", "cluster_b": "test"},
                "genus_to_split": {"Vibrio": "train"},
                "split_statistics": {"total_samples": 2},
            }
        ),
        encoding="utf-8",
    )

    result = validate_artifact("phage_phi.split_assignments_json", str(artifact))


    assert result.validated is True
    assert result.schema_valid is True
    assert result.metadata["row_count"] == 2


def test_split_assignment_alias_prefers_metadata_total_when_available(tmp_path: Path) -> None:
    artifact = tmp_path / "split_assignments.json"
    artifact.write_text(
        json.dumps(
            {
                "cluster_to_split": {"cluster_a": "train"},
                "genus_to_split": {"Vibrio": "train"},
                "metadata": {"total_samples": 2998},
                "split_statistics": {"total_samples": 2},
            }
        ),
        encoding="utf-8",
    )

    result = validate_artifact("phage_phi.split_assignments_json", str(artifact))

    assert result.validated is True
    assert result.metadata["row_count"] == 2998
