from __future__ import annotations

import json
import joblib
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier

from app.services.plans.artifact_validation import validate_artifact


def test_ml_validation_metrics_rejects_placeholder_all_zero_metrics(tmp_path):
    metrics = tmp_path / "validation_metrics.json"
    metrics.write_text(json.dumps({
        "models": {
            "random_forest": {"accuracy": 0.0, "macro_f1": 0.0},
            "svm": {"accuracy": 0.0, "macro_f1": 0.0},
        },
        "metadata": {"note": "Placeholder validation metrics. Actual values will be populated after model training."},
    }), encoding="utf-8")

    result = validate_artifact("ml_traditional.validation_metrics_json", str(metrics))

    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "placeholder" in result.failure_reason.lower() or "zero" in result.failure_reason.lower()


def test_ml_validation_metrics_accepts_nonzero_model_metrics(tmp_path):
    metrics = tmp_path / "validation_metrics.json"
    metrics.write_text(json.dumps({
        "models": {
            "random_forest": {"accuracy": 0.72, "macro_f1": 0.68},
            "svm": {"accuracy": 0.65, "macro_f1": 0.61},
        },
        "metadata": {
            "training_samples": 5000,
            "label_source": "phage_ml.training_metadata_parquet:host_label",
            "label_alignment": "inner join on stable feature row IDs",
        },
    }), encoding="utf-8")

    result = validate_artifact("ml_traditional.validation_metrics_json", str(metrics))

    assert result.schema_valid is True
    assert result.metadata["model_count"] == 2


def test_ml_validation_metrics_accepts_row_ids_source_as_alignment_evidence(tmp_path):
    metrics = tmp_path / "validation_metrics.json"
    metrics.write_text(json.dumps({
        "models": {"random_forest": {"accuracy": 0.72, "macro_f1": 0.68}},
        "metadata": {
            "label_source": "phage_ml.training_metadata_parquet:host_label",
            "row_ids_source": "phage_ml.feature_row_ids_json",
        },
    }), encoding="utf-8")

    result = validate_artifact("ml_traditional.validation_metrics_json", str(metrics))

    assert result.schema_valid is True
    assert result.metadata["model_count"] == 1


def test_ml_model_checkpoints_rejects_placeholder_only_directory(tmp_path):
    ckpt_dir = tmp_path / "model_checkpoints"
    ckpt_dir.mkdir()
    (ckpt_dir / "placeholder_checkpoint.pkl").write_bytes(b"placeholder")

    result = validate_artifact("ml_traditional.model_checkpoints_dir", str(ckpt_dir))

    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "placeholder" in result.failure_reason.lower() or "checkpoint" in result.failure_reason.lower()


def test_ml_model_checkpoints_accepts_real_checkpoint_file(tmp_path):
    ckpt_dir = tmp_path / "model_checkpoints"
    ckpt_dir.mkdir()
    model = RandomForestClassifier(n_estimators=1, random_state=0).fit([[0], [1]], [0, 1])
    joblib.dump(model, ckpt_dir / "random_forest_best.joblib")

    result = validate_artifact("ml_traditional.model_checkpoints_dir", str(ckpt_dir))

    assert result.schema_valid is True
    assert result.metadata["real_checkpoint_count"] == 1
    inspected = result.metadata["inspected_checkpoints"][0]
    assert inspected["valid"] is True
    assert "RandomForestClassifier" in inspected["model_type"]


def test_ml_validation_metrics_rejects_synthetic_label_provenance(tmp_path):
    metrics = tmp_path / "validation_metrics.json"
    metrics.write_text(json.dumps({
        "models": {"RandomForest": {"accuracy": 0.49, "macro_f1": 0.49}},
        "metadata": {
            "label_source": "balanced labels were created synthetically",
            "label_alignment": "synthetic balanced random labels",
            "training_samples": 5000,
        },
    }), encoding="utf-8")

    result = validate_artifact("ml_traditional.validation_metrics_json", str(metrics))

    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "synthetic" in result.failure_reason.lower() or "provenance" in result.failure_reason.lower()


def test_ml_validation_metrics_rejects_missing_label_provenance(tmp_path):
    metrics = tmp_path / "validation_metrics.json"
    metrics.write_text(json.dumps({
        "models": {"RandomForest": {"accuracy": 0.49, "macro_f1": 0.49}},
        "metadata": {"training_samples": 5000},
    }), encoding="utf-8")

    result = validate_artifact("ml_traditional.validation_metrics_json", str(metrics))

    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "label_source" in result.failure_reason or "provenance" in result.failure_reason.lower()


def test_ml_model_checkpoints_rejects_dummy_classifier_even_when_large(tmp_path):
    ckpt_dir = tmp_path / "model_checkpoints"
    ckpt_dir.mkdir()
    dummy = DummyClassifier(strategy="constant", constant=0).fit([[0], [1]], [0, 0])
    joblib.dump(dummy, ckpt_dir / "dummy_best_model.joblib")

    result = validate_artifact("ml_traditional.model_checkpoints_dir", str(ckpt_dir))

    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "dummyclassifier" in result.failure_reason.lower() or "dummy" in result.failure_reason.lower()


def test_training_metadata_parquet_accepts_required_identifier_column(tmp_path):
    import pandas as pd

    artifact = tmp_path / "training_metadata.parquet"
    pd.DataFrame({
        "phage_genome_id": ["g1", "g2"],
        "host_label": ["A", "B"],
    }).to_parquet(artifact)

    result = validate_artifact("phage_ml.training_metadata_parquet", str(artifact))

    assert result.schema_valid is True
    assert result.metadata["row_count"] == 2


def test_feature_row_ids_json_rejects_duplicates(tmp_path):
    artifact = tmp_path / "feature_row_ids.json"
    artifact.write_text(json.dumps(["g1", "g1"]), encoding="utf-8")

    result = validate_artifact("phage_ml.feature_row_ids_json", str(artifact))

    assert result.schema_valid is False
    assert result.failure_reason is not None
    assert "duplicate" in result.failure_reason.lower()


def test_label_alignment_manifest_accepts_real_provenance(tmp_path):
    artifact = tmp_path / "label_alignment.json"
    artifact.write_text(json.dumps({
        "label_source": "phage_ml.training_metadata_parquet:host_label",
        "label_alignment": "inner join on feature_row_ids_json",
        "training_samples": 2,
        "is_synthetic": False,
    }), encoding="utf-8")

    result = validate_artifact("phage_ml.label_alignment_json", str(artifact))

    assert result.schema_valid is True
    assert result.metadata["provenance_valid"] is True
