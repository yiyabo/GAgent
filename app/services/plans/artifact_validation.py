from __future__ import annotations

import csv
import glob
import json
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


@dataclass(frozen=True)
class ArtifactValidationSpec:
    """Typed validation contract for a published artifact alias."""

    kind: str
    description: str = ""
    min_rows: int = 0
    min_cols: int = 0
    min_size_bytes: int = 1
    required_columns: Tuple[str, ...] = ()
    required_keys: Tuple[str, ...] = ()
    min_files: int = 1
    glob_pattern: str = "**/*"
    allowed_suffixes: Tuple[str, ...] = ()
    require_sparse_loadable: bool = False
    allow_pickle: bool = False

    def to_prompt_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"kind": self.kind}
        if self.description:
            payload["description"] = self.description
        for key in (
            "min_rows",
            "min_cols",
            "min_size_bytes",
            "required_columns",
            "required_keys",
            "min_files",
            "glob_pattern",
            "allowed_suffixes",
            "require_sparse_loadable",
        ):
            value = getattr(self, key)
            if value in (None, "", (), [], False, 0):
                continue
            payload[key] = list(value) if isinstance(value, tuple) else value
        return payload


@dataclass
class ArtifactValidationResult:
    alias: str
    path: str
    kind: str
    exists: bool
    validated: bool
    schema_valid: bool
    failure_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "alias": self.alias,
            "path": self.path,
            "kind": self.kind,
            "exists": self.exists,
            "validated": self.validated,
            "schema_valid": self.schema_valid,
            "metadata": dict(self.metadata),
        }
        if self.failure_reason:
            payload["failure_reason"] = self.failure_reason
        return payload


_SPARSE_MATRIX_SPEC = ArtifactValidationSpec(
    kind="sparse_npz",
    description="SciPy sparse feature matrix; must load with scipy.sparse.load_npz and have non-zero rows/columns.",
    min_rows=1,
    min_cols=1,
    require_sparse_loadable=True,
)
_DENSE_ARRAY_SPEC = ArtifactValidationSpec(
    kind="numpy_npy",
    description="Dense NumPy array; must load with numpy.load and contain at least one row and one dimension.",
    min_rows=1,
    min_cols=1,
)


_ALIAS_VALIDATION_SPECS: Dict[str, ArtifactValidationSpec] = {
    "phage_ml.kmer_features_npz": _SPARSE_MATRIX_SPEC,
    "phage_ml.genome_embeddings_npy": _DENSE_ARRAY_SPEC,
    "phage_ml.functional_features_csv": ArtifactValidationSpec(kind="csv_table", min_rows=1),
    "phage_ml.unified_feature_matrix_h5": ArtifactValidationSpec(kind="hdf5_table", min_rows=1, min_cols=1),
    "phage_ml.unified_feature_matrix_csv": ArtifactValidationSpec(
        kind="csv_table",
        min_rows=1,
        min_cols=2,
        required_columns=("Phage_ID",),
    ),
    "phage_ml.hybrid_features_final_npz": _SPARSE_MATRIX_SPEC,
    "phage_ml.training_metadata_parquet": ArtifactValidationSpec(
        kind="parquet_table",
        description="Training metadata table with stable row identifiers and candidate supervised labels.",
        min_rows=1,
        required_columns=("phage_genome_id",),
    ),
    "phage_ml.feature_row_ids_json": ArtifactValidationSpec(
        kind="row_ids_json",
        description="Ordered feature matrix row identifiers used to align labels to X rows.",
        min_rows=1,
    ),
    "phage_ml.label_alignment_json": ArtifactValidationSpec(
        kind="ml_label_alignment",
        description="Supervised learning label alignment manifest proving real labels were joined to feature rows.",
        required_keys=("label_source", "label_alignment", "training_samples"),
    ),
    "phage_phi.genomic_stats_csv": ArtifactValidationSpec(
        kind="csv_table",
        min_rows=1,
        min_cols=4,
        required_columns=("Phage_ID", "Length", "GC_Content", "Coding_Density"),
    ),
    "phage_phi.functional_features_csv": ArtifactValidationSpec(
        kind="csv_table",
        min_rows=1,
        min_cols=2,
        required_columns=("Phage_ID",),
    ),
    "phage_phi.full_feature_matrix_h5": ArtifactValidationSpec(kind="hdf5_table", min_rows=1, min_cols=1),
    "phage_phi.unified_features_h5": ArtifactValidationSpec(kind="hdf5_table", min_rows=1, min_cols=1),
    "phage_phi.unified_features_csv": ArtifactValidationSpec(
        kind="csv_table",
        min_rows=1,
        min_cols=2,
        required_columns=("Phage_ID",),
    ),
    "phage_phi.split_assignments_json": ArtifactValidationSpec(kind="split_assignments", min_rows=1),
    "phage_phi.leakage_aware_splits_json": ArtifactValidationSpec(kind="split_assignments", min_rows=1),
    "phage_phi.split_statistics_json": ArtifactValidationSpec(kind="json_schema"),
    "phage_phi.split_report_md": ArtifactValidationSpec(kind="file_nonempty"),
    "phage_phi.baseline_train_val_arrays": ArtifactValidationSpec(kind="baseline_train_val_npz", min_rows=1, min_cols=1),
    "phage_phi.advanced_model_input_raw": ArtifactValidationSpec(
        kind="file_nonempty",
        min_size_bytes=1,
        allowed_suffixes=(".pkl", ".pickle"),
    ),
    "phage_phi.tree_model_features": ArtifactValidationSpec(kind="file_nonempty"),
    "phage_phi.dl_kmer_tensors": ArtifactValidationSpec(kind="hdf5_table", min_rows=1, min_cols=1),
    "phage_phi.advanced_model_ready_data": ArtifactValidationSpec(kind="directory_glob", min_files=1, allowed_suffixes=(".npz", ".npy", ".pt", ".pth", ".h5")),
    "phage_host.baseline_eval_by_genus_json": ArtifactValidationSpec(kind="json_schema"),
    "phage_host.baseline_feature_importance_csv": ArtifactValidationSpec(
        kind="csv_table",
        min_rows=1,
        required_columns=("Feature_Name",),
    ),
    "ml_features.hybrid_matrix_npz": _SPARSE_MATRIX_SPEC,
    "phage_features.hybrid_matrix_npz": _SPARSE_MATRIX_SPEC,
    "ml_phage.features_hybrid_matrix": _SPARSE_MATRIX_SPEC,
    "ml_phage.hybrid_feature_matrix_npz": _SPARSE_MATRIX_SPEC,
    "ml_traditional.validation_metrics_json": ArtifactValidationSpec(
        kind="ml_validation_metrics",
        description="Validation metrics JSON from real model training; placeholders/all-zero metrics are invalid.",
        required_keys=("models",),
    ),
    "ml_traditional.model_checkpoints_dir": ArtifactValidationSpec(
        kind="ml_model_checkpoints",
        description="Directory containing trained model checkpoints; placeholder-only directories are invalid.",
        min_files=1,
        allowed_suffixes=(".pkl", ".pickle", ".joblib", ".pt", ".pth"),
    ),
    "phage_dl.trained_models_dir": ArtifactValidationSpec(
        kind="directory_glob",
        description="Directory containing trained deep-learning model checkpoints.",
        min_files=1,
        allowed_suffixes=(".pt", ".pth", ".ckpt", ".bin"),
    ),
    "phage_dl.training_logs_csv": ArtifactValidationSpec(kind="csv_table", min_rows=1),
    "phage_ml.cv_splits_json": ArtifactValidationSpec(kind="json_schema"),
    "phage_ml.trained_models_dir": ArtifactValidationSpec(kind="directory_glob", min_files=1),
    "phage_ml.cv_metrics_json": ArtifactValidationSpec(kind="json_schema", required_keys=("splits_evaluated",)),
    "ml_phage.kmer_enrichment_results": ArtifactValidationSpec(kind="csv_table", min_rows=1),
    "phage_host.transfer_learning_checkpoints": ArtifactValidationSpec(
        kind="directory_glob",
        description="Transfer-learning checkpoints and metrics under outputs/transfer_learning.",
        min_files=1,
        allowed_suffixes=(".pth", ".pt", ".ckpt", ".json"),
    ),
    "ml_phage.best_model_pkl": ArtifactValidationSpec(
        kind="file_nonempty",
        min_size_bytes=1,
        allowed_suffixes=(".pkl", ".pickle", ".joblib"),
    ),
    "ml_phage.prediction_service_docker": ArtifactValidationSpec(kind="tar_archive", min_files=1),
    "general.evidence_md": ArtifactValidationSpec(kind="file_nonempty"),
    "general.references_bib": ArtifactValidationSpec(kind="file_nonempty"),
    "general.library_jsonl": ArtifactValidationSpec(kind="file_nonempty"),
    "enhanced_sampling.evidence_md": ArtifactValidationSpec(kind="file_nonempty"),
    "enhanced_sampling.references_bib": ArtifactValidationSpec(kind="file_nonempty"),
    "enhanced_sampling.structured_evidence_json": ArtifactValidationSpec(kind="json_schema"),
    "ai_dl.evidence_md": ArtifactValidationSpec(kind="file_nonempty"),
    "ai_dl.references_bib": ArtifactValidationSpec(kind="file_nonempty"),
    "ai_dl.structured_evidence_json": ArtifactValidationSpec(kind="json_schema"),
    "nmr_cryo_msm.structured_evidence_json": ArtifactValidationSpec(kind="json_schema"),
    "nmr_cryo_msm.outline_json": ArtifactValidationSpec(kind="json_schema"),
    "industry.structured_evidence_json": ArtifactValidationSpec(kind="json_schema"),
    "industry.outline_json": ArtifactValidationSpec(kind="json_schema"),
}

_DYNAMIC_DIRECTORY_ALIAS_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:_[a-z0-9]+)*\."
    r"(?:evidence_dataframes|evidence_tables|summary_tables|intermediate_data_dir)$"
)


def get_artifact_validation_spec(alias: str) -> Optional[ArtifactValidationSpec]:
    alias_text = str(alias or "").strip()
    spec = _ALIAS_VALIDATION_SPECS.get(alias_text)
    if spec is not None:
        return spec
    if _DYNAMIC_DIRECTORY_ALIAS_RE.fullmatch(alias_text):
        return ArtifactValidationSpec(kind="directory_glob", min_files=1)
    return None


def get_artifact_validation_prompt_specs(aliases: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    specs: Dict[str, Dict[str, Any]] = {}
    for alias in aliases:
        alias_text = str(alias or "").strip()
        spec = get_artifact_validation_spec(alias_text)
        if spec is not None:
            specs[alias_text] = spec.to_prompt_dict()
    return specs


def validate_artifact(alias: str, path: str, spec: Optional[ArtifactValidationSpec] = None) -> ArtifactValidationResult:
    alias_text = str(alias or "").strip()
    path_text = str(path or "").strip()
    effective_spec = spec or get_artifact_validation_spec(alias_text) or _infer_spec_from_path(path_text)
    if effective_spec is None:
        effective_spec = ArtifactValidationSpec(kind="file_nonempty")

    candidate = Path(path_text).expanduser()
    if not candidate.exists():
        return ArtifactValidationResult(
            alias=alias_text,
            path=path_text,
            kind=effective_spec.kind,
            exists=False,
            validated=False,
            schema_valid=False,
            failure_reason="Artifact path does not exist.",
        )

    try:
        if effective_spec.kind == "file_nonempty":
            metadata = _validate_file_nonempty(candidate, effective_spec)
        elif effective_spec.kind == "sparse_npz":
            metadata = _validate_sparse_npz(candidate, effective_spec)
        elif effective_spec.kind == "numpy_npy":
            metadata = _validate_numpy_npy(candidate, effective_spec)
        elif effective_spec.kind == "json_schema":
            metadata = _validate_json_schema(candidate, effective_spec)
        elif effective_spec.kind == "parquet_table":
            metadata = _validate_parquet_table(candidate, effective_spec)
        elif effective_spec.kind == "row_ids_json":
            metadata = _validate_row_ids_json(candidate, effective_spec)
        elif effective_spec.kind == "ml_label_alignment":
            metadata = _validate_ml_label_alignment(candidate, effective_spec)
        elif effective_spec.kind == "ml_validation_metrics":
            metadata = _validate_ml_validation_metrics(candidate, effective_spec)
        elif effective_spec.kind == "csv_table":
            metadata = _validate_csv_table(candidate, effective_spec)
        elif effective_spec.kind == "hdf5_table":
            metadata = _validate_hdf5_table(candidate, effective_spec)
        elif effective_spec.kind == "split_assignments":
            metadata = _validate_split_assignments(candidate, effective_spec)
        elif effective_spec.kind == "baseline_train_val_npz":
            metadata = _validate_baseline_train_val_npz(candidate, effective_spec)
        elif effective_spec.kind == "directory_glob":
            metadata = _validate_directory_glob(candidate, effective_spec)
        elif effective_spec.kind == "ml_model_checkpoints":
            metadata = _validate_ml_model_checkpoints(candidate, effective_spec)
        elif effective_spec.kind == "tar_archive":
            metadata = _validate_archive(candidate, effective_spec)
        else:
            raise ValueError(f"Unsupported artifact validation kind: {effective_spec.kind}")
    except Exception as exc:
        return ArtifactValidationResult(
            alias=alias_text,
            path=path_text,
            kind=effective_spec.kind,
            exists=True,
            validated=False,
            schema_valid=False,
            failure_reason=str(exc),
        )

    return ArtifactValidationResult(
        alias=alias_text,
        path=path_text,
        kind=effective_spec.kind,
        exists=True,
        validated=True,
        schema_valid=True,
        metadata=metadata,
    )


def artifact_entry_is_valid(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    validation = entry.get("validation")
    alias = str(entry.get("alias") or "").strip()
    path = str(entry.get("path") or "").strip()
    if isinstance(validation, dict):
        if bool(validation.get("validated") and validation.get("schema_valid")):
            return True
        if validation.get("validated") is False or validation.get("schema_valid") is False:
            return False
    return bool(path and Path(path).exists() and validate_artifact(alias, path).validated)


def _infer_spec_from_path(path_text: str) -> Optional[ArtifactValidationSpec]:
    suffix = Path(path_text).suffix.lower()
    if suffix == ".npz":
        return _SPARSE_MATRIX_SPEC
    if suffix == ".npy":
        return ArtifactValidationSpec(kind="numpy_npy", min_rows=1)
    if suffix == ".json":
        return ArtifactValidationSpec(kind="json_schema")
    if suffix == ".parquet":
        return ArtifactValidationSpec(kind="parquet_table", min_rows=1)
    if suffix in {".csv", ".tsv"}:
        return ArtifactValidationSpec(kind="csv_table", min_rows=1)
    if suffix in {".tar", ".zip", ".gz", ".tgz"}:
        return ArtifactValidationSpec(kind="tar_archive", min_files=1)
    return None


def _validate_file_nonempty(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    if not path.is_file():
        raise ValueError("Artifact is not a file.")
    size = path.stat().st_size
    if size < max(1, int(spec.min_size_bytes or 1)):
        raise ValueError(f"Artifact is empty or too small: {size} bytes.")
    _assert_suffix_allowed(path, spec)
    return {"size_bytes": size}


def _validate_sparse_npz(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    if path.suffix.lower() != ".npz":
        raise ValueError("Expected a .npz sparse matrix artifact.")
    try:
        from scipy import sparse  # type: ignore
    except Exception as exc:
        raise ValueError(f"scipy is required to validate sparse NPZ artifacts: {exc}")
    try:
        matrix = sparse.load_npz(str(path))
    except Exception as exc:
        raise ValueError(f"NPZ is not loadable as a SciPy sparse matrix: {exc}")
    shape = tuple(int(v) for v in getattr(matrix, "shape", ()) or ())
    if len(shape) != 2:
        raise ValueError(f"Sparse matrix must be 2D, got shape {shape}.")
    if shape[0] < int(spec.min_rows or 0) or shape[1] < int(spec.min_cols or 0):
        raise ValueError(f"Sparse matrix shape {shape} is below required minimum ({spec.min_rows}, {spec.min_cols}).")
    nnz = int(getattr(matrix, "nnz", 0) or 0)
    return {"shape": list(shape), "row_count": shape[0], "column_count": shape[1], "nnz": nnz}


def _validate_numpy_npy(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    if path.suffix.lower() != ".npy":
        raise ValueError("Expected a .npy NumPy array artifact.")
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise ValueError(f"numpy is required to validate NPY artifacts: {exc}")
    arr = np.load(str(path), allow_pickle=bool(spec.allow_pickle))
    shape = tuple(int(v) for v in getattr(arr, "shape", ()) or ())
    if not shape:
        raise ValueError("NumPy array must have a non-empty shape.")
    row_count = shape[0]
    if shape[0] < int(spec.min_rows or 0):
        raise ValueError(f"NumPy array has too few rows: {shape[0]} < {spec.min_rows}.")
    if len(shape) > 1 and shape[1] < int(spec.min_cols or 0):
        raise ValueError(f"NumPy array has too few columns: {shape[1]} < {spec.min_cols}.")
    if int(getattr(arr, "size", 0) or 0) <= 0:
        raise ValueError("NumPy array contains no elements.")
    return {"shape": list(shape), "row_count": row_count, "size": int(arr.size), "dtype": str(arr.dtype)}


def _validate_json_schema(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in spec.required_keys:
        if _nested_get(payload, key) is None:
            raise ValueError(f"JSON artifact is missing required key: {key}")
    item_count = len(payload) if isinstance(payload, (dict, list)) else 1
    if item_count <= 0:
        raise ValueError("JSON artifact is empty.")
    return {"json_type": type(payload).__name__, "item_count": item_count}


def _validate_parquet_table(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    if path.suffix.lower() != ".parquet":
        raise ValueError("Expected a .parquet table artifact.")
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise ValueError(f"pandas is required to validate Parquet artifacts: {exc}")
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        raise ValueError(f"Parquet artifact is not readable: {exc}")
    columns = [str(column) for column in frame.columns]
    row_count = int(len(frame))
    missing = [col for col in spec.required_columns if col not in columns]
    if missing:
        raise ValueError(f"Parquet artifact is missing required columns: {missing}")
    if row_count < int(spec.min_rows or 0):
        raise ValueError(f"Parquet artifact has too few data rows: {row_count} < {spec.min_rows}.")
    return {"row_count": row_count, "column_count": len(columns), "columns": columns[:50]}


def _validate_row_ids_json(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    metadata = _validate_json_schema(path, ArtifactValidationSpec(kind="json_schema"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        row_ids = payload
    elif isinstance(payload, dict):
        row_ids = None
        for key in ("row_ids", "feature_row_ids", "genome_ids", "ids"):
            value = payload.get(key)
            if isinstance(value, list):
                row_ids = value
                break
        if row_ids is None:
            raise ValueError("Row-id JSON must be a list or contain row_ids/feature_row_ids/genome_ids.")
    else:
        raise ValueError("Row-id JSON must be a list or object.")
    cleaned = [str(item).strip() for item in row_ids if str(item).strip()]
    if len(cleaned) < int(spec.min_rows or 1):
        raise ValueError(f"Row-id JSON has too few IDs: {len(cleaned)} < {spec.min_rows or 1}.")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Row-id JSON contains duplicate IDs; feature-row alignment must be one-to-one.")
    metadata.update({"row_count": len(cleaned), "sample_ids": cleaned[:5]})
    return metadata


def _validate_ml_label_alignment(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    metadata = _validate_json_schema(path, spec)
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    _validate_ml_label_provenance(payload, serialized)
    metadata.update({"provenance_valid": True})
    return metadata


def _validate_ml_validation_metrics(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    metadata = _validate_json_schema(path, spec)
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    if "placeholder" in serialized or "actual values will be populated" in serialized or "not been performed" in serialized:
        raise ValueError("Validation metrics appear to be placeholder output, not real model training results.")
    _validate_ml_label_provenance(payload, serialized)
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict) or not models:
        raise ValueError("Validation metrics must contain a non-empty models object.")
    metric_values: List[float] = []
    evaluated_models = 0
    for model_name, model_payload in models.items():
        if not isinstance(model_payload, dict):
            continue
        if "accuracy" not in model_payload or "macro_f1" not in model_payload:
            raise ValueError(f"Model {model_name!r} is missing accuracy or macro_f1.")
        try:
            accuracy_value = model_payload.get("accuracy")
            macro_f1_value = model_payload.get("macro_f1")
            if accuracy_value is None or macro_f1_value is None:
                raise ValueError("accuracy and macro_f1 must be present")
            accuracy = float(accuracy_value)
            macro_f1 = float(macro_f1_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Model {model_name!r} has non-numeric metrics: {exc}")
        if accuracy < 0 or macro_f1 < 0:
            raise ValueError(f"Model {model_name!r} has negative metrics.")
        metric_values.extend([accuracy, macro_f1])
        evaluated_models += 1
    if evaluated_models <= 0:
        raise ValueError("Validation metrics contain no evaluated models.")
    if not any(value > 0 for value in metric_values):
        raise ValueError("All validation metrics are zero; this looks like a placeholder, not trained model evaluation.")
    metadata.update({"model_count": evaluated_models, "nonzero_metric_count": sum(1 for value in metric_values if value > 0)})
    return metadata


def _validate_ml_label_provenance(payload: Any, serialized: str) -> None:
    synthetic_patterns = (
        r"synthetic(?:ally)?[^.]{0,80}labels?",
        r"labels?[^.]{0,80}synthetic",
        r"random(?:ly)?[^.]{0,80}labels?",
        r"labels?[^.]{0,80}random",
        r"balanced[^.]{0,80}labels?[^.]{0,80}synthetic",
        r"dummy[^.]{0,80}labels?",
        r"fake[^.]{0,80}labels?",
        r"fabricated[^.]{0,80}labels?",
        r"simulated[^.]{0,80}labels?",
        r"generated[^.]{0,80}labels?",
        r"constant[^.]{0,80}labels?",
    )
    if re.search(r'"is_synthetic"\s*:\s*true', serialized):
        raise ValueError("Validation metrics declare synthetic labels; real label provenance is required.")
    provenance_text = re.sub(r'"is_synthetic"\s*:\s*false\s*,?', '', serialized)
    if any(re.search(pattern, provenance_text) for pattern in synthetic_patterns):
        raise ValueError("Validation metrics declare synthetic, random, dummy, or fabricated labels; real label provenance is required.")
    if not isinstance(payload, dict):
        raise ValueError("Validation metrics must be a JSON object with label provenance metadata.")
    provenance = payload.get("metadata") if isinstance(payload, dict) else None
    if not isinstance(provenance, dict):
        provenance = payload if isinstance(payload, dict) else None
    if not isinstance(provenance, dict):
        raise ValueError("Validation metrics metadata must record real label provenance and alignment.")
    source_keys = ("label_source", "labels_source", "target_source", "training_label_source", "label_provenance", "source_label_path")
    alignment_keys = (
        "label_alignment",
        "alignment_method",
        "aligned_sample_count",
        "n_samples",
        "training_samples",
        "row_ids_source",
        "feature_row_ids_source",
        "row_alignment_source",
    )
    source_value = next((provenance.get(key) for key in source_keys if provenance.get(key)), None)
    alignment_value = next((provenance.get(key) for key in alignment_keys if provenance.get(key) is not None), None)
    if source_value is None:
        raise ValueError("Validation metrics metadata is missing real label_source/label_provenance information.")
    if alignment_value is None:
        raise ValueError("Validation metrics metadata is missing label alignment or aligned sample count information.")
    source_text = str(source_value).strip().lower()
    if not source_text or source_text in {"unknown", "n/a", "none"}:
        raise ValueError("Validation metrics label provenance is empty or unknown.")


def _validate_csv_table(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    delimiter = _detect_delimiter(path)
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = list(reader.fieldnames or [])
        row_count = sum(1 for _ in reader)
    missing = [col for col in spec.required_columns if col not in columns]
    if missing:
        raise ValueError(f"CSV artifact is missing required columns: {missing}")
    if row_count < int(spec.min_rows or 0):
        raise ValueError(f"CSV artifact has too few data rows: {row_count} < {spec.min_rows}.")
    if spec.min_cols and len(columns) < int(spec.min_cols):
        raise ValueError(f"CSV artifact has too few columns: {len(columns)} < {spec.min_cols}.")
    return {"row_count": row_count, "column_count": len(columns), "columns": columns[:50]}


def _detect_delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        sample = handle.read(4096)
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    if "\t" in first_line and first_line.count("\t") >= first_line.count(","):
        return "\t"
    if path.suffix.lower() == ".tsv":
        return "\t"
    return ","


def _validate_hdf5_table(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    if path.suffix.lower() not in {".h5", ".hdf5"}:
        raise ValueError("Expected an HDF5 artifact with .h5 or .hdf5 suffix.")
    try:
        import h5py  # type: ignore
    except Exception as exc:
        raise ValueError(f"h5py is required to validate HDF5 artifacts: {exc}")

    datasets: List[Dict[str, Any]] = []
    table_datasets: List[Dict[str, Any]] = []
    with h5py.File(path, "r") as handle:
        def collect(name: str, value: Any) -> None:
            shape = getattr(value, "shape", None)
            if shape is None:
                return
            shape_tuple = tuple(int(item) for item in shape)
            dtype = getattr(value, "dtype", "")
            item: Dict[str, Union[str, List[int], int]] = {
                "name": name,
                "shape": list(shape_tuple),
                "dtype": str(dtype),
            }
            datasets.append(item)
            dtype_names = getattr(dtype, "names", None)
            if name.endswith("/table") and isinstance(dtype_names, tuple):
                item["column_count"] = len(dtype_names)
                table_datasets.append(item)

        handle.visititems(collect)

    if not datasets:
        raise ValueError("HDF5 artifact contains no datasets.")

    primary_datasets = table_datasets or [
        item for item in datasets if item["shape"] and not item["name"].split("/")[0].startswith("_i_")
    ] or datasets
    max_rows = max((item["shape"][0] for item in primary_datasets if item["shape"]), default=0)
    max_cols = max(
        (
            int(item.get("column_count") or (item["shape"][1] if len(item["shape"]) > 1 else 1))
            for item in primary_datasets
        ),
        default=1,
    )
    if max_rows < int(spec.min_rows or 0):
        raise ValueError(f"HDF5 artifact has too few rows: {max_rows} < {spec.min_rows}.")
    if max_cols < int(spec.min_cols or 0):
        raise ValueError(f"HDF5 artifact has too few columns: {max_cols} < {spec.min_cols}.")
    return {
        "dataset_count": len(datasets),
        "row_count": max_rows,
        "column_count": max_cols,
        "datasets": primary_datasets[:20],
    }


def _validate_split_assignments(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            row_count = len(payload)
            if row_count < int(spec.min_rows or 0):
                raise ValueError(f"Split assignment list has too few rows: {row_count} < {spec.min_rows}.")
            first = payload[0] if payload else {}
            if not isinstance(first, dict) or not _split_assignment_columns_present(first):
                raise ValueError("Split assignment rows must include phage and split fields.")
            return {"json_type": "list", "row_count": row_count, "columns": sorted(first)[:50]}
        if isinstance(payload, dict):
            row_count = _count_split_assignment_payload_rows(payload)
            if row_count < int(spec.min_rows or 0):
                raise ValueError(f"Split assignment JSON has too few rows: {row_count} < {spec.min_rows}.")
            return {"json_type": "dict", "row_count": row_count, "keys": sorted(payload)[:50]}
        raise ValueError("Split assignment JSON must be an object or list.")
    if suffix in {".csv", ".tsv"}:
        delimiter = _detect_delimiter(path)
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            columns = list(reader.fieldnames or [])
            lowered = {column.lower() for column in columns}
            if "phage_id" not in lowered:
                raise ValueError("Split table is missing Phage_ID column.")
            if not ({"split", "set", "partition"} & lowered):
                raise ValueError("Split table is missing a split/set/partition column.")
            row_count = sum(1 for _ in reader)
        if row_count < int(spec.min_rows or 0):
            raise ValueError(f"Split assignment table has too few rows: {row_count} < {spec.min_rows}.")
        return {"row_count": row_count, "column_count": len(columns), "columns": columns[:50]}
    raise ValueError("Split assignments must be a JSON, CSV, or TSV artifact.")


def _split_assignment_columns_present(row: Dict[str, Any]) -> bool:
    lowered = {str(column).lower() for column in row}
    return "phage_id" in lowered and bool({"split", "set", "partition"} & lowered)


def _count_split_assignment_payload_rows(payload: Dict[str, Any]) -> int:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        total = _split_metadata_total(metadata)
        if total > 0:
            return total
    row_keys = (
        "assignments",
        "split_assignments",
        "phage_split_assignments",
        "rows",
    )
    for key in row_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            return len(value)
    split_statistics = payload.get("split_statistics")
    if isinstance(split_statistics, dict):
        total = _numeric_total_from_mapping(split_statistics)
        if total > 0:
            return total
    cluster_to_split = payload.get("cluster_to_split")
    genus_to_split = payload.get("genus_to_split")
    if isinstance(cluster_to_split, dict) and cluster_to_split:
        return len(cluster_to_split)
    if isinstance(genus_to_split, dict) and genus_to_split:
        return len(genus_to_split)
    return len(payload)


def _numeric_total_from_mapping(mapping: Dict[str, Any]) -> int:
    for key in ("total", "total_samples", "n_samples", "count", "row_count"):
        value = mapping.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    total = 0
    for value in mapping.values():
        if isinstance(value, dict):
            total += _numeric_total_from_mapping(value)
        elif isinstance(value, (int, float)) and value > 0:
            total += int(value)
    return total


def _split_metadata_total(metadata: Dict[str, Any]) -> int:
    for key in ("total_phages", "total_samples", "row_count", "clusters_assigned", "total_clusters"):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return _numeric_total_from_mapping(metadata)


def _validate_baseline_train_val_npz(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    if path.suffix.lower() != ".npz":
        raise ValueError("Expected a .npz baseline train/validation/test artifact.")
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise ValueError(f"numpy is required to validate baseline arrays: {exc}")
    with np.load(str(path), allow_pickle=False) as archive:
        keys = set(archive.files)
        required_keys = {
            "train_X_data", "train_X_indices", "train_X_indptr", "train_X_shape", "train_y_genus", "train_y_lifestyle",
            "val_X_data", "val_X_indices", "val_X_indptr", "val_X_shape", "val_y_genus", "val_y_lifestyle",
            "test_X_data", "test_X_indices", "test_X_indptr", "test_X_shape", "test_y_genus", "test_y_lifestyle",
            "genus_encoder_classes", "lifestyle_encoder_classes", "feature_dim",
        }
        missing = sorted(required_keys - keys)
        if missing:
            raise ValueError(f"Baseline NPZ is missing required arrays: {missing}")

        split_metadata: Dict[str, Any] = {}
        total_rows = 0
        feature_dims: set[int] = set()
        for split in ("train", "val", "test"):
            shape_array = _npz_array(archive, f"{split}_X_shape")
            shape = tuple(int(value) for value in shape_array.tolist())
            if len(shape) != 2:
                raise ValueError(f"{split}_X_shape must be 2D, got {shape}.")
            rows, cols = shape
            if rows < int(spec.min_rows or 0) or cols < int(spec.min_cols or 0):
                raise ValueError(f"{split} matrix shape {shape} is below required minimum ({spec.min_rows}, {spec.min_cols}).")
            indptr_len = int(len(_npz_array(archive, f"{split}_X_indptr")))
            data_len = int(len(_npz_array(archive, f"{split}_X_data")))
            indices_len = int(len(_npz_array(archive, f"{split}_X_indices")))
            if indptr_len != rows + 1:
                raise ValueError(f"{split}_X_indptr length {indptr_len} does not match row count {rows}.")
            if data_len != indices_len:
                raise ValueError(f"{split}_X_data and {split}_X_indices lengths differ: {data_len} != {indices_len}.")
            for label_key in (f"{split}_y_genus", f"{split}_y_lifestyle"):
                label_rows = int(len(_npz_array(archive, label_key)))
                if label_rows != rows:
                    raise ValueError(f"{label_key} length {label_rows} does not match {split} row count {rows}.")
            total_rows += rows
            feature_dims.add(cols)
            split_metadata[split] = {"shape": [rows, cols], "nnz": data_len}

        declared_feature_dim = int(_npz_array(archive, "feature_dim").item())
        genus_class_count = int(len(_npz_array(archive, "genus_encoder_classes")))
        lifestyle_class_count = int(len(_npz_array(archive, "lifestyle_encoder_classes")))
    if feature_dims != {declared_feature_dim}:
        raise ValueError(f"feature_dim {declared_feature_dim} does not match split matrix columns {sorted(feature_dims)}.")
    return {
        "row_count": total_rows,
        "feature_dim": declared_feature_dim,
        "splits": split_metadata,
        "genus_class_count": genus_class_count,
        "lifestyle_class_count": lifestyle_class_count,
    }


def _npz_array(archive: Any, key: str) -> Any:
    value = archive[key]
    if getattr(value, "dtype", None) is not None and value.dtype.hasobject:
        raise ValueError(f"Baseline NPZ array {key} has object dtype, which is not allowed.")
    return value


def _validate_directory_glob(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    if not path.is_dir():
        raise ValueError("Artifact is not a directory.")
    pattern = str(path / (spec.glob_pattern or "**/*"))
    matched = [Path(item) for item in glob.glob(pattern, recursive=True) if Path(item).is_file()]
    if spec.allowed_suffixes:
        allowed = {suffix.lower() for suffix in spec.allowed_suffixes}
        matched = [item for item in matched if item.suffix.lower() in allowed]
    if len(matched) < int(spec.min_files or 1):
        raise ValueError(f"Directory artifact matched {len(matched)} files, expected at least {spec.min_files}.")
    return {"file_count": len(matched), "matched_files": [str(item) for item in matched[:20]]}


def _validate_ml_model_checkpoints(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    metadata = _validate_directory_glob(path, spec)
    matched = [Path(item) for item in metadata.get("matched_files", [])]
    checkpoint_files = [
        item for item in matched
        if "placeholder" not in item.name.lower()
        and item.suffix.lower() in {".pkl", ".pickle", ".joblib", ".pt", ".pth"}
        and item.exists()
    ]
    inspected: List[Dict[str, Any]] = []
    rejected: List[str] = []
    dummy_rejections: List[str] = []
    real_checkpoints: List[Path] = []
    for item in checkpoint_files:
        ok, reason, info = _inspect_ml_checkpoint(item)
        inspected.append({"path": str(item), "valid": ok, **info})
        if ok and item.stat().st_size >= 1024:
            real_checkpoints.append(item)
        elif not ok:
            rejected.append(f"{item.name}: {reason}")
            if "dummy" in reason.lower():
                dummy_rejections.append(f"{item.name}: {reason}")
    if dummy_rejections:
        raise ValueError("Checkpoint directory contains DummyClassifier/stale dummy checkpoint files: " + "; ".join(dummy_rejections[:5]))
    if not real_checkpoints:
        detail = "; ".join(rejected[:5]) if rejected else "no eligible checkpoint files"
        raise ValueError(f"Checkpoint directory contains no non-placeholder trained model checkpoint >= 1KB ({detail}).")
    metadata["real_checkpoint_count"] = len(real_checkpoints)
    metadata["real_checkpoints"] = [str(item) for item in real_checkpoints[:20]]
    metadata["inspected_checkpoints"] = inspected[:20]
    return metadata


def _inspect_ml_checkpoint(path: Path) -> Tuple[bool, str, Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".pt", ".pth"}:
        return True, "torch checkpoint accepted by suffix", {"size_bytes": path.stat().st_size}
    try:
        import joblib  # type: ignore
    except Exception as exc:
        return True, f"joblib unavailable; accepted by size only: {exc}", {"size_bytes": path.stat().st_size}
    try:
        model = joblib.load(str(path))
    except Exception as exc:
        return False, f"checkpoint is not loadable with joblib: {exc}", {"size_bytes": path.stat().st_size}
    module = type(model).__module__
    class_name = type(model).__name__
    if class_name == "DummyClassifier" or module.endswith("dummy"):
        return False, "DummyClassifier checkpoint is not a trained model", {"model_type": f"{module}.{class_name}"}
    if hasattr(model, "strategy") and class_name.lower().startswith("dummy"):
        return False, "dummy strategy checkpoint is not a trained model", {"model_type": f"{module}.{class_name}"}
    return True, "loadable non-dummy checkpoint", {"model_type": f"{module}.{class_name}", "size_bytes": path.stat().st_size}


def _validate_archive(path: Path, spec: ArtifactValidationSpec) -> Dict[str, Any]:
    _validate_file_nonempty(path, spec)
    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            members = [member.name for member in archive.getmembers() if member.isfile()]
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = [info.filename for info in archive.infolist() if not info.is_dir()]
    else:
        raise ValueError("Archive artifact is not a readable tar or zip file.")
    if len(members) < int(spec.min_files or 1):
        raise ValueError(f"Archive contains {len(members)} files, expected at least {spec.min_files}.")
    return {"file_count": len(members), "members": members[:30]}


def _assert_suffix_allowed(path: Path, spec: ArtifactValidationSpec) -> None:
    if not spec.allowed_suffixes:
        return
    allowed = {suffix.lower() for suffix in spec.allowed_suffixes}
    if path.suffix.lower() not in allowed:
        raise ValueError(f"Artifact suffix {path.suffix!r} is not one of {sorted(allowed)}.")


def _nested_get(payload: Any, key_path: str) -> Any:
    current = payload
    for part in [segment for segment in str(key_path or "").split(".") if segment]:
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current
