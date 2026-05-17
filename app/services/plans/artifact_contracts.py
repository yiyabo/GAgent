from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
import fcntl
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.services.resources.resource_registry import get_resource_spec, normalize_resource_id
from .artifact_validation import artifact_entry_is_valid, validate_artifact

from pydantic import BaseModel, Field

_ARTIFACT_SPECS: Dict[str, tuple[str, str]] = {
    "general.evidence_md": ("general", "evidence.md"),
    "general.references_bib": ("general", "references.bib"),
    "general.library_jsonl": ("general", "library.jsonl"),
    "enhanced_sampling.evidence_md": ("enhanced_sampling", "evidence.md"),
    "enhanced_sampling.references_bib": ("enhanced_sampling", "references.bib"),
    "enhanced_sampling.structured_evidence_json": ("enhanced_sampling", "structured_evidence.json"),
    "ai_dl.evidence_md": ("ai_dl", "evidence.md"),
    "ai_dl.references_bib": ("ai_dl", "references.bib"),
    "ai_dl.structured_evidence_json": ("ai_dl", "structured_evidence.json"),
    "nmr_cryo_msm.structured_evidence_json": ("nmr_cryo_msm", "structured_evidence.json"),
    "nmr_cryo_msm.outline_json": ("nmr_cryo_msm", "outline.json"),
    "industry.structured_evidence_json": ("industry", "structured_evidence.json"),
    "industry.outline_json": ("industry", "outline.json"),
    "phage_ml.kmer_features_npz": ("phage_ml", "features/kmer_46.npz"),
    "phage_ml.genome_embeddings_npy": ("phage_ml", "features/genome_embeddings.npy"),
    "phage_ml.functional_features_csv": ("phage_ml", "features/functional_annotations.csv"),
    "phage_ml.hybrid_features_final_npz": ("phage_ml", "features/hybrid_features_final.npz"),
    "phage_ml.training_metadata_parquet": ("phage_ml", "metadata/training_metadata.parquet"),
    "phage_ml.feature_row_ids_json": ("phage_ml", "features/feature_row_ids.json"),
    "phage_ml.label_alignment_json": ("phage_ml", "metadata/label_alignment.json"),
    "ml_features.hybrid_matrix_npz": ("ml_features", "hybrid_features_final.npz"),
    "phage_features.hybrid_matrix_npz": ("phage_features", "hybrid_features_final.npz"),
    "ml_phage.features_hybrid_matrix": ("ml_phage", "hybrid_features_final.npz"),
    "ml_phage.hybrid_feature_matrix_npz": ("ml_phage", "hybrid_features_final.npz"),
    "ml_traditional.validation_metrics_json": ("ml_traditional", "validation_metrics.json"),
    "ml_traditional.model_checkpoints_dir": ("ml_traditional", "model_checkpoints"),
    "phage_dl.trained_models_dir": ("phage_dl", "models"),
    "phage_dl.training_logs_csv": ("phage_dl", "training_logs.csv"),
    "phage_ml.cv_splits_json": ("phage_ml", "cv_splits.json"),
    "phage_ml.trained_models_dir": ("phage_ml", "trained_models"),
    "phage_ml.cv_metrics_json": ("phage_ml", "cv_metrics.json"),
    "ml_phage.kmer_enrichment_results": ("ml_phage", "kmer_enrichment_by_genus.csv"),
    "phage_host.transfer_learning_checkpoints": ("phage_host", "transfer_learning"),
    "ml_phage.best_model_pkl": ("ml_phage", "best_model.pkl"),
    "ml_phage.prediction_service_docker": ("ml_phage", "deployment/phage_host_predictor.tar"),
}

_ARTIFACT_ALIAS_CANONICAL: Dict[str, str] = {
    "ml_features.hybrid_matrix_npz": "phage_ml.hybrid_features_final_npz",
    "phage_features.hybrid_matrix_npz": "phage_ml.hybrid_features_final_npz",
    "ml_phage.features_hybrid_matrix": "phage_ml.hybrid_features_final_npz",
    "ml_phage.hybrid_feature_matrix_npz": "phage_ml.hybrid_features_final_npz",
    "phage_host.hybrid_feature_matrix": "phage_ml.hybrid_features_final_npz",
    "phage_features.genome_embeddings_npy": "phage_ml.genome_embeddings_npy",
    "phage_ml.trained_models_dir": "ml_traditional.model_checkpoints_dir",
    "phage_ml.tuned_models_dir": "ml_traditional.model_checkpoints_dir",
    "ml_phage.best_model_pkl": "ml_traditional.model_checkpoints_dir",
    "ml_phage.model_evaluations_json": "ml_traditional.validation_metrics_json",
    "ml_phage.cross_validation_report_json": "phage_ml.cv_metrics_json",
    "phage_eval.cv_splits_json": "phage_ml.cv_splits_json",
    "ml_phage.biological_interpretation_md": "ml_phage.host_specificity_interpretation_md",
    "ml_phage.functional_annotations": "phage_ml.functional_features_csv",
    "phage_ml.modeling_metadata_parquet": "phage_ml.training_metadata_parquet",
    "ml_features.training_metadata_parquet": "phage_ml.training_metadata_parquet",
    "ml_features.feature_row_ids_json": "phage_ml.feature_row_ids_json",
    "ml_features.label_alignment_json": "phage_ml.label_alignment_json",
    "ml_supervised.training_metadata_parquet": "phage_ml.training_metadata_parquet",
    "ml_supervised.feature_row_ids_json": "phage_ml.feature_row_ids_json",
    "ml_supervised.label_alignment_json": "phage_ml.label_alignment_json",
}

_DYNAMIC_DIRECTORY_ARTIFACT_SLOTS: Dict[str, str] = {
    "evidence_dataframes": "evidence_dataframes",
    "evidence_tables": "evidence_tables",
    "summary_tables": "summary_tables",
    "intermediate_data_dir": "intermediate_data",
}
_DYNAMIC_ARTIFACT_ALIAS_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:_[a-z0-9]+)*\."
    r"(?:" + "|".join(re.escape(slot) for slot in _DYNAMIC_DIRECTORY_ARTIFACT_SLOTS) + r")$"
)


def _dynamic_artifact_spec(alias: str) -> Optional[tuple[str, str]]:
    text = str(alias or "").strip()
    if not _DYNAMIC_ARTIFACT_ALIAS_RE.fullmatch(text):
        return None
    namespace, slot = text.split(".", 1)
    return namespace, _DYNAMIC_DIRECTORY_ARTIFACT_SLOTS[slot]


def _artifact_spec_for_alias(alias: str) -> Optional[tuple[str, str]]:
    text = canonicalize_artifact_alias(alias)
    return _ARTIFACT_SPECS.get(text) or _dynamic_artifact_spec(text)


def _is_registered_artifact_alias(alias: str) -> bool:
    return _artifact_spec_for_alias(alias) is not None


def canonicalize_artifact_alias(alias: str) -> str:
    text = str(alias or "").strip()
    seen: set[str] = set()
    while text in _ARTIFACT_ALIAS_CANONICAL and text not in seen:
        seen.add(text)
        text = _ARTIFACT_ALIAS_CANONICAL[text]
    return text


def expand_artifact_aliases(alias: str) -> List[str]:
    canonical = canonicalize_artifact_alias(alias)
    aliases = [canonical]
    for source, target in sorted(_ARTIFACT_ALIAS_CANONICAL.items()):
        if canonicalize_artifact_alias(target) == canonical and source not in aliases:
            aliases.append(source)
    return aliases


_LEGACY_BASENAME_TO_ALIASES: Dict[str, List[str]] = {
    "enhanced_sampling_evidence.md": ["enhanced_sampling.evidence_md"],
    "enhanced_sampling_refs.bib": ["enhanced_sampling.references_bib"],
    "ai_evidence.md": ["ai_dl.evidence_md"],
    "ai_dl_evidence.md": ["ai_dl.evidence_md"],
    "ai_references.bib": ["ai_dl.references_bib"],
    "ai_dl_references.bib": ["ai_dl.references_bib"],
    "structured_evidence_ai_dl.json": ["ai_dl.structured_evidence_json"],
    "structured_evidence_nmr_cryo_msm.json": ["nmr_cryo_msm.structured_evidence_json"],
    "nmr_cryo_msm_outline.json": ["nmr_cryo_msm.outline_json"],
    "industry_outline.json": ["industry.outline_json"],
    "industrial_outline.json": ["industry.outline_json"],
    "structured_evidence_industry.json": ["industry.structured_evidence_json"],
    "modeling_metadata.parquet": ["phage_ml.training_metadata_parquet"],
    "training_metadata.parquet": ["phage_ml.training_metadata_parquet"],
    "genome_ids.json": ["phage_ml.feature_row_ids_json"],
    "feature_row_ids.json": ["phage_ml.feature_row_ids_json"],
    "row_ids.json": ["phage_ml.feature_row_ids_json"],
    "label_alignment.json": ["phage_ml.label_alignment_json"],
}

_GENERIC_BASENAME_TO_SLOT = {
    "evidence.md": "evidence_md",
    "references.bib": "references_bib",
    "library.jsonl": "library_jsonl",
    "structured_evidence.json": "structured_evidence_json",
    "outline.json": "outline_json",
}

_NAMESPACE_KEYWORDS = {
    "enhanced_sampling": (
        "enhanced sampling",
        "增强采样",
        "metadynamics",
        "umbrella sampling",
        "replica exchange",
        "free energy perturbation",
    ),
    "ai_dl": (
        "ai",
        "deep learning",
        "machine learning",
        "深度学习",
        "diffusion",
        "alphafold",
        "rosettafold",
        "equivariant",
    ),
    "industry": (
        "industry",
        "industrial",
        "工业应用",
        "行业案例",
        "药物发现",
        "drug discovery",
    ),
    "nmr_cryo_msm": (
        "nmr",
        "cryo-em",
        "cryo em",
        "markov state",
        "msm",
        "3dva",
        "异质性",
        "整合策略",
        "残基级动力学",
    ),
}


def _repo_root() -> Path:
    return Path.cwd().resolve()


def canonical_plan_root(plan_id: int) -> Path:
    return _repo_root() / "results" / "plans" / f"plan_{plan_id}"


def artifact_manifest_path(plan_id: int) -> Path:
    return canonical_plan_root(plan_id) / "artifacts_manifest.json"


def load_artifact_manifest(plan_id: int) -> Dict[str, Any]:
    path = artifact_manifest_path(plan_id)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("plan_id", plan_id)
    payload.setdefault("artifacts", {})
    return payload


def save_artifact_manifest(plan_id: int, manifest: Dict[str, Any]) -> Path:
    path = artifact_manifest_path(plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            existing = load_artifact_manifest(plan_id)
            merged = dict(existing)
            merged.update({k: v for k, v in manifest.items() if k != "artifacts"})
            existing_artifacts = existing.get("artifacts") if isinstance(existing.get("artifacts"), dict) else {}
            incoming_artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
            merged["artifacts"] = {**existing_artifacts, **incoming_artifacts}

            tmp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
            fd, tmp_path = tempfile.mkstemp(prefix=tmp_name, dir=str(path.parent), text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                    json.dump(merged, tmp_file, ensure_ascii=False, indent=2)
                    tmp_file.write("\n")
                    tmp_file.flush()
                    os.fsync(tmp_file.fileno())
                os.replace(tmp_path, path)
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except OSError:
                    pass
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return path


def canonical_artifact_path(plan_id: int, alias: str) -> Optional[Path]:
    alias = canonicalize_artifact_alias(alias)
    spec = _artifact_spec_for_alias(alias)
    if not spec:
        return None
    namespace, filename = spec
    return canonical_plan_root(plan_id) / namespace / filename

def _text_contains_namespace_keyword(text: str, keyword: str) -> bool:
    lowered_text = str(text or "").lower()
    lowered_keyword = str(keyword or "").strip().lower()
    if not lowered_keyword:
        return False
    if re.fullmatch(r"[a-z0-9_+-]+", lowered_keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(lowered_keyword)}(?![a-z0-9])"
        return re.search(pattern, lowered_text) is not None
    return lowered_keyword in lowered_text


def infer_artifact_namespace(task_name: str, instruction: str = "") -> str:
    text = f"{task_name}\n{instruction}".lower()
    for namespace in ("enhanced_sampling", "ai_dl", "industry", "nmr_cryo_msm"):
        keywords = _NAMESPACE_KEYWORDS.get(namespace, ())
        if any(_text_contains_namespace_keyword(text, keyword) for keyword in keywords):
            return namespace
    return "general"


def aliases_for_path_text(text: str, *, preferred_namespace: str) -> List[str]:
    lowered = str(text or "").lower()
    aliases: List[str] = []
    seen: set[str] = set()
    for basename, mapped in _LEGACY_BASENAME_TO_ALIASES.items():
        if basename in lowered:
            for alias in mapped:
                if alias not in seen:
                    seen.add(alias)
                    aliases.append(alias)
    for basename, slot in _GENERIC_BASENAME_TO_SLOT.items():
        if basename in lowered:
            alias = f"{preferred_namespace}.{slot}"
            if _is_registered_artifact_alias(alias) and alias not in seen:
                seen.add(alias)
                aliases.append(alias)
    return aliases


def aliases_for_file_name(file_name: str, *, preferred_namespace: str) -> List[str]:
    lowered = Path(str(file_name or "")).name.lower()
    aliases = list(_LEGACY_BASENAME_TO_ALIASES.get(lowered, ()))
    if lowered in _GENERIC_BASENAME_TO_SLOT:
        alias = f"{preferred_namespace}.{_GENERIC_BASENAME_TO_SLOT[lowered]}"
        if _is_registered_artifact_alias(alias) and alias not in aliases:
            aliases.append(alias)
    for alias in _semantic_aliases_for_file_name(
        lowered,
        preferred_namespace=preferred_namespace,
    ):
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def candidate_filenames_for_alias(alias: str) -> List[str]:
    alias = canonicalize_artifact_alias(alias)
    spec = _artifact_spec_for_alias(alias)
    names: List[str] = []
    if spec:
        filename = spec[1].lower().strip("/\\")
        if filename:
            names.append(filename)
            basename = Path(filename).name.lower()
            if basename and basename not in names:
                names.append(basename)
    for basename, aliases in _LEGACY_BASENAME_TO_ALIASES.items():
        if alias in aliases and basename.lower() not in names:
            names.append(basename.lower())
    if _dynamic_artifact_spec(alias):
        slot = alias.split(".", 1)[1]
        for candidate in (slot, "data", "tables", "dataframes"):
            if candidate not in names:
                names.append(candidate)
    return names

def is_artifact_alias(value: str) -> bool:
    return _is_registered_artifact_alias(str(value or "").strip())

def _semantic_basename_matches_alias(*, basename: str, alias: str) -> bool:
    spec = _artifact_spec_for_alias(alias)
    if spec is None:
        return False

    namespace, _ = spec
    slot = alias.split(".", 1)[1] if "." in alias else ""
    lowered = Path(str(basename or "")).name.lower()
    if not lowered:
        return False
    if lowered.endswith(".analysis.md") or lowered.endswith(".partial.md"):
        return False

    suffix = Path(lowered).suffix.lower()
    stem = Path(lowered).stem.lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", stem) if token]
    token_set = set(tokens)

    if slot == "evidence_md":
        if suffix != ".md" or "evidence" not in token_set:
            return False
    elif slot == "references_bib":
        if suffix != ".bib" or not token_set.intersection({"reference", "references", "refs", "bibliography", "citations"}):
            return False
    elif slot == "library_jsonl":
        if suffix != ".jsonl" or "library" not in token_set:
            return False
    elif slot == "structured_evidence_json":
        if suffix != ".json" or not {"structured", "evidence"}.issubset(token_set):
            return False
    elif slot == "outline_json":
        if suffix != ".json" or "outline" not in token_set:
            return False
    else:
        return False

    if namespace == "general":
        return True

    keyword_text = f"{' '.join(tokens)} {lowered}"
    return any(
        _text_contains_namespace_keyword(keyword_text, keyword)
        for keyword in _NAMESPACE_KEYWORDS.get(namespace, ())
    )


def artifact_path_matches_alias(path_text: str, alias: str) -> bool:
    alias = canonicalize_artifact_alias(alias)
    normalized = str(path_text or "").strip().replace("\\", "/").strip("/").lower()
    basename = Path(normalized).name.lower()
    if not basename or not _is_registered_artifact_alias(alias):
        return False
    candidates = candidate_filenames_for_alias(alias)
    if basename in candidates or normalized in candidates:
        return True
    return _semantic_basename_matches_alias(basename=basename, alias=alias)

def _semantic_aliases_for_file_name(
    file_name: str,
    *,
    preferred_namespace: str,
) -> List[str]:
    aliases: List[str] = []
    for alias, (namespace, _) in _ARTIFACT_SPECS.items():
        if namespace != preferred_namespace:
            continue
        if artifact_path_matches_alias(file_name, alias):
            aliases.append(alias)
    return aliases


def infer_artifact_contract(
    *,
    task_name: str,
    instruction: str,
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Compatibility wrapper returning combined requires/publishes as a dict.

    Prefer :func:`resolve_artifact_contract_with_provenance` for new call sites
    that need to distinguish explicit declarations from inferred fallbacks.
    """
    resolved = resolve_artifact_contract_with_provenance(
        task_name=task_name,
        instruction=instruction,
        metadata=metadata,
    )
    return resolved.as_contract_dict()


class ArtifactContractProvenance(BaseModel):
    """Structured artifact contract with provenance tracking.

    ``explicit_*`` aliases come from the task metadata's ``artifact_contract``
    block and are the authoritative intent. ``inferred_*`` aliases are derived
    from free-text paths (paper_context_paths, instruction text,
    acceptance_criteria.checks) and are kept as compatibility fallbacks.
    ``runtime_*`` aliases are added after runtime filesystem scans and should
    be treated as the weakest signal.
    """

    explicit_requires: List[str] = Field(default_factory=list)
    explicit_publishes: List[str] = Field(default_factory=list)
    inferred_requires: List[str] = Field(default_factory=list)
    inferred_publishes: List[str] = Field(default_factory=list)
    runtime_requires: List[str] = Field(default_factory=list)
    runtime_publishes: List[str] = Field(default_factory=list)
    resource_requires: List[str] = Field(default_factory=list)

    def requires(self) -> List[str]:
        return _merge_unique(
            self.explicit_requires, self.inferred_requires, self.runtime_requires
        )

    def publishes(self) -> List[str]:
        return _merge_unique(
            self.explicit_publishes, self.inferred_publishes, self.runtime_publishes
        )

    def required_resources(self) -> List[str]:
        return _merge_unique(self.resource_requires)

    @property
    def has_explicit(self) -> bool:
        return bool(self.explicit_requires or self.explicit_publishes)

    @property
    def has_inferred(self) -> bool:
        return bool(self.inferred_requires or self.inferred_publishes)

    @property
    def has_runtime(self) -> bool:
        return bool(self.runtime_requires or self.runtime_publishes)

    @property
    def contract_source(self) -> str:
        has_explicit = self.has_explicit
        has_fallback = self.has_inferred or self.has_runtime
        if has_explicit and has_fallback:
            return "mixed"
        if has_explicit:
            return "explicit"
        if self.has_runtime and not self.has_inferred:
            return "runtime"
        if has_fallback:
            return "inferred"
        return "none"

    def as_contract_dict(self) -> Dict[str, List[str]]:
        contract = {"requires": self.requires(), "publishes": self.publishes()}
        resources = self.required_resources()
        if resources:
            contract["resources"] = resources
        return contract


def _merge_unique(*buckets: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    merged: List[str] = []
    for bucket in buckets:
        for alias in bucket:
            text = str(alias or "").strip()
            if text:
                canonical_text = canonicalize_artifact_alias(text)
                if _is_registered_artifact_alias(canonical_text):
                    text = canonical_text
            if text and text not in seen:
                seen.add(text)
                merged.append(text)
    return merged


_RESOURCE_REF_RE = re.compile(r"\bresource:([A-Za-z0-9_.-]+)\b")


def _extract_resource_requires(raw_items: Any, *, allow_unknown: bool = False) -> List[str]:
    resources: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        has_resource_prefix = text.startswith("resource:")
        resource_id = normalize_resource_id(text)
        if not allow_unknown and not has_resource_prefix and get_resource_spec(resource_id) is None:
            return
        if resource_id and resource_id not in seen:
            seen.add(resource_id)
            resources.append(resource_id)

    if isinstance(raw_items, list):
        for item in raw_items:
            add(item)
    elif isinstance(raw_items, str):
        add(raw_items)
    elif isinstance(raw_items, dict):
        for key in ("requires", "resources"):
            nested = raw_items.get(key)
            if isinstance(nested, list):
                for item in nested:
                    add(item)
            elif isinstance(nested, str):
                add(nested)
    return resources


def _extract_resource_refs_from_text(*values: Any) -> List[str]:
    resources: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "")
        for match in _RESOURCE_REF_RE.finditer(text):
            resource_id = normalize_resource_id(match.group(1))
            if resource_id and resource_id not in seen:
                seen.add(resource_id)
                resources.append(resource_id)
    return resources


def _infer_known_resource_requires(task_name: str, instruction: str, metadata: Dict[str, Any]) -> List[str]:
    haystack = " ".join([
        str(task_name or ""),
        str(instruction or ""),
        json.dumps(metadata, ensure_ascii=False, default=str),
    ]).lower()
    if (
        "phagescope" in haystack
        or "phage_fasta" in haystack
        or ("k-mer" in haystack and "phage" in haystack)
        or ("kmer" in haystack and "phage" in haystack)
        or ("genome embedding" in haystack and "phage" in haystack)
        or ("genome embeddings" in haystack and "phage" in haystack)
        or ("dna language model" in haystack and "phage" in haystack)
        or ("hyenadna" in haystack and "phage" in haystack)
        or ("nucleotide transformer" in haystack and "phage" in haystack)
        or "phage_ml.genome_embeddings_npy" in haystack
    ):
        return ["phagescope.sequence_corpus"]
    return []


_SUPERVISED_ML_OUTPUT_ALIASES = {
    "ml_traditional.validation_metrics_json",
    "phage_ml.cv_metrics_json",
}

_PHAGE_SUPERVISED_FEATURE_ALIASES = {
    "phage_ml.hybrid_features_final_npz",
    "phage_ml.kmer_features_npz",
    "phage_ml.genome_embeddings_npy",
    "phage_ml.functional_features_csv",
}


def _known_artifact_alias(value: Any) -> Optional[str]:
    alias = canonicalize_artifact_alias(str(value or "").strip())
    return alias if _is_registered_artifact_alias(alias) else None


def _collect_alias_fields(mapping: Dict[str, Any], field_names: Iterable[str]) -> List[str]:
    aliases: List[str] = []
    seen: set[str] = set()
    for field_name in field_names:
        raw_value = mapping.get(field_name)
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            alias = _known_artifact_alias(value)
            if alias and alias not in seen:
                seen.add(alias)
                aliases.append(alias)
    return aliases


def _infer_supervised_learning_artifacts(
    raw_contract: Dict[str, Any],
    *,
    requires: Iterable[str],
    publishes: Iterable[str],
) -> tuple[List[str], List[str]]:
    """Compile supervised-learning semantics into ordinary artifact aliases.

    This intentionally keys off explicit contract metadata and canonical ML output
    aliases rather than task IDs or free-text task names.  The returned aliases
    flow through the existing preflight, prompt injection, and verification path.
    """

    block = raw_contract.get("supervised_learning") or raw_contract.get("supervised_ml")
    block_enabled = isinstance(block, dict) and block.get("enabled", True) is not False
    require_set = {canonicalize_artifact_alias(str(item or "").strip()) for item in requires}
    publish_set = {canonicalize_artifact_alias(str(item or "").strip()) for item in publishes}
    has_supervised_outputs = bool(publish_set.intersection(_SUPERVISED_ML_OUTPUT_ALIASES))
    if not block_enabled and not has_supervised_outputs:
        return [], []

    semantic_requires: List[str] = []
    semantic_publishes: List[str] = []

    if isinstance(block, dict):
        semantic_requires.extend(_collect_alias_fields(block, (
            "feature_alias",
            "features_alias",
            "feature_matrix_alias",
            "label_alias",
            "labels_alias",
            "label_table_alias",
            "metadata_alias",
            "row_ids_alias",
            "feature_row_ids_alias",
        )))
        semantic_publishes.extend(_collect_alias_fields(block, (
            "alignment_alias",
            "label_alignment_alias",
            "metrics_alias",
            "model_alias",
            "models_alias",
        )))

    phage_feature_context = bool(require_set.intersection(_PHAGE_SUPERVISED_FEATURE_ALIASES))
    phage_output_context = bool(publish_set.intersection({
        "ml_traditional.validation_metrics_json",
        "phage_ml.cv_metrics_json",
    }))
    if phage_feature_context or phage_output_context:
        semantic_requires.extend([
            "phage_ml.training_metadata_parquet",
            "phage_ml.feature_row_ids_json",
        ])
        publish_alignment = not (isinstance(block, dict) and block.get("publish_alignment") is False)
        if publish_alignment:
            semantic_publishes.append("phage_ml.label_alignment_json")

    def dedupe(values: Iterable[str], existing: set[str]) -> List[str]:
        result: List[str] = []
        seen = set(existing)
        for value in values:
            alias = _known_artifact_alias(value)
            if alias and alias not in seen:
                seen.add(alias)
                result.append(alias)
        return result

    return dedupe(semantic_requires, require_set), dedupe(semantic_publishes, publish_set)

def _extract_explicit_aliases(raw_items: Any) -> List[str]:
    if not isinstance(raw_items, list):
        return []
    aliases: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = canonicalize_artifact_alias(str(item or "").strip())
        if text and _is_registered_artifact_alias(text) and text not in seen:
            seen.add(text)
            aliases.append(text)
    return aliases

def resolve_artifact_contract_with_provenance(
    *,
    task_name: str,
    instruction: str,
    metadata: Optional[Dict[str, Any]],
) -> ArtifactContractProvenance:
    """Resolve a task's artifact contract while tracking provenance.

    Explicit declarations from ``metadata.artifact_contract`` are authoritative.
    Inferred aliases from paper_context_paths / instruction / acceptance
    criteria checks remain as compatibility fallbacks and are flagged as such
    on the returned object.
    """
    payload = metadata if isinstance(metadata, dict) else {}
    raw_contract = payload.get("artifact_contract")
    if not isinstance(raw_contract, dict):
        raw_contract = {}
    preferred_namespace = infer_artifact_namespace(task_name, instruction)

    explicit_requires = _extract_explicit_aliases(raw_contract.get("requires"))
    explicit_publishes = _extract_explicit_aliases(raw_contract.get("publishes"))
    resource_requires = _merge_unique(
        _extract_resource_requires(raw_contract.get("resources"), allow_unknown=True),
        _extract_resource_requires(raw_contract.get("requires"), allow_unknown=False),
        _extract_resource_requires(payload.get("resource_contract"), allow_unknown=True),
        _extract_resource_refs_from_text(task_name, instruction, payload.get("resource_contract"), raw_contract),
        _infer_known_resource_requires(task_name, instruction, payload),
    )
    explicit_require_set = set(explicit_requires)
    explicit_publish_set = set(explicit_publishes)

    inferred_requires: List[str] = []
    seen_inferred_req: set[str] = set()

    raw_context_paths = payload.get("paper_context_paths")
    if isinstance(raw_context_paths, list):
        for item in raw_context_paths:
            for alias in aliases_for_path_text(
                str(item or ""), preferred_namespace=preferred_namespace
            ):
                alias = canonicalize_artifact_alias(alias)
                if alias in explicit_require_set or alias in seen_inferred_req:
                    continue
                seen_inferred_req.add(alias)
                inferred_requires.append(alias)

    for alias in aliases_for_path_text(
        instruction, preferred_namespace=preferred_namespace
    ):
        alias = canonicalize_artifact_alias(alias)
        if alias in explicit_require_set or alias in seen_inferred_req:
            continue
        seen_inferred_req.add(alias)
        inferred_requires.append(alias)

    inferred_publishes: List[str] = []
    seen_inferred_pub: set[str] = set()

    acceptance = payload.get("acceptance_criteria")
    checks = acceptance.get("checks") if isinstance(acceptance, dict) else None
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue
            raw_path = check.get("path")
            if not raw_path:
                continue
            for alias in aliases_for_path_text(
                str(raw_path), preferred_namespace=preferred_namespace
            ):
                alias = canonicalize_artifact_alias(alias)
                if alias in explicit_publish_set or alias in seen_inferred_pub:
                    continue
                seen_inferred_pub.add(alias)
                inferred_publishes.append(alias)

    semantic_requires, semantic_publishes = _infer_supervised_learning_artifacts(
        raw_contract,
        requires=[*explicit_requires, *inferred_requires],
        publishes=[*explicit_publishes, *inferred_publishes],
    )
    for alias in semantic_requires:
        if alias not in explicit_require_set and alias not in seen_inferred_req:
            seen_inferred_req.add(alias)
            inferred_requires.append(alias)
    for alias in semantic_publishes:
        if alias not in explicit_publish_set and alias not in seen_inferred_pub:
            seen_inferred_pub.add(alias)
            inferred_publishes.append(alias)

    return ArtifactContractProvenance(
        explicit_requires=explicit_requires,
        explicit_publishes=explicit_publishes,
        inferred_requires=inferred_requires,
        inferred_publishes=inferred_publishes,
        resource_requires=resource_requires,
    )


def extend_contract_with_runtime_candidates(
    provenance: ArtifactContractProvenance,
    *,
    task_name: str,
    instruction: str,
    candidate_paths: Iterable[str],
) -> ArtifactContractProvenance:
    """Return a new provenance with runtime-discovered publish aliases added."""
    preferred_namespace = infer_artifact_namespace(task_name, instruction)
    already = set(provenance.publishes())
    runtime_publishes = list(provenance.runtime_publishes)
    for raw_path in candidate_paths:
        for alias in aliases_for_file_name(
            raw_path, preferred_namespace=preferred_namespace
        ):
            if alias in already or alias in runtime_publishes:
                continue
            runtime_publishes.append(alias)
    return provenance.model_copy(update={"runtime_publishes": runtime_publishes})


def published_artifact_paths_for_task(manifest: Dict[str, Any], task_id: int) -> List[str]:
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    if not isinstance(artifacts, dict):
        return []
    paths: List[str] = []
    for entry in artifacts.values():
        if not isinstance(entry, dict):
            continue
        if int(entry.get("producer_task_id") or -1) != int(task_id):
            continue
        path = str(entry.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def publish_artifact(
    *,
    plan_id: int,
    alias: str,
    source_path: str,
    producer_task_id: int,
    manifest: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    requested_alias = str(alias or "").strip()
    alias = canonicalize_artifact_alias(requested_alias)
    canonical = canonical_artifact_path(plan_id, alias)
    if canonical is None:
        return None
    source = Path(str(source_path or "").strip()).expanduser()
    if not source.exists() or not (source.is_file() or source.is_dir()):
        return None
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != canonical.resolve():
        if source.is_dir():
            if canonical.exists() and canonical.is_file():
                canonical.unlink()
            if canonical.exists() and canonical.is_dir():
                shutil.rmtree(canonical)
            shutil.copytree(source, canonical)
        else:
            shutil.copy2(source, canonical)
    validation = validate_artifact(alias, str(canonical.resolve())).to_dict()
    entry = {
        "alias": alias,
        "requested_alias": requested_alias,
        "path": str(canonical.resolve()),
        "producer_task_id": producer_task_id,
        "source_path": str(source.resolve()),
        "source_size": source.stat().st_size if source.is_file() else None,
        "updated_at": time.time(),
        "validation": validation,
        "validated": bool(validation.get("validated") and validation.get("schema_valid")),
    }
    artifacts = manifest.setdefault("artifacts", {})
    artifacts[alias] = entry
    return entry

def resolve_manifest_aliases(
    manifest: Dict[str, Any],
    aliases: Iterable[str],
) -> Dict[str, str]:
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    resolved: Dict[str, str] = {}
    if not isinstance(artifacts, dict):
        return resolved
    for alias in aliases:
        alias_text = str(alias or "").strip()
        canonical_alias = canonicalize_artifact_alias(alias_text)
        entry = artifacts.get(canonical_alias) or artifacts.get(alias_text)
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if path and Path(path).exists() and artifact_entry_is_valid(entry):
            resolved[alias_text] = path
    return resolved

def find_runtime_candidates(plan_id: int, task_id: int, alias: str) -> List[str]:
    runtime_root = _repo_root() / "runtime" / "session_adhoc"
    if not runtime_root.exists():
        return []
    basenames = candidate_filenames_for_alias(alias)
    matches: List[str] = []
    seen: set[str] = set()
    for basename in basenames:
        pattern = f"plan{plan_id}_task{task_id}/run_*/**/{basename}"
        for candidate in sorted(runtime_root.glob(pattern)):
            try:
                resolved = str(candidate.resolve())
            except Exception:
                resolved = str(candidate)
            if resolved not in seen:
                seen.add(resolved)
                matches.append(resolved)
    return matches


def infer_contract_from_candidates(
    *,
    task_name: str,
    instruction: str,
    candidate_paths: Iterable[str],
    current_contract: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[str]]:
    preferred_namespace = infer_artifact_namespace(task_name, instruction)
    requires = list((current_contract or {}).get("requires") or [])
    publishes = list((current_contract or {}).get("publishes") or [])
    seen = set(publishes)
    for raw_path in candidate_paths:
        for alias in aliases_for_file_name(raw_path, preferred_namespace=preferred_namespace):
            if alias not in seen:
                seen.add(alias)
                publishes.append(alias)
    return {"requires": requires, "publishes": publishes}


def find_candidate_source_for_alias(
    *,
    alias: str,
    candidate_paths: Iterable[str],
) -> Optional[str]:
    wanted = set(candidate_filenames_for_alias(alias))
    if not wanted:
        return None

    deduped: List[str] = []
    seen: set[str] = set()
    for raw_path in candidate_paths:
        text = str(raw_path or "").strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)

    # Prefer explicit artifact paths reported by the executor. Iterate from the
    # end because later repair attempts append newer candidates after older
    # failed attempts.
    for raw_path in reversed(deduped):
        candidate = Path(raw_path).expanduser()
        basename = candidate.name.lower()
        if basename in wanted and candidate.exists():
            return str(candidate)

    # Some tools report only a parent output directory. Search inside such
    # directories for canonical filenames/directories derived from the alias so
    # reconciliation remains generic instead of plan/task-specific.
    for raw_path in reversed(deduped):
        candidate = Path(raw_path).expanduser()
        if not candidate.exists() or not candidate.is_dir():
            continue
        for basename in wanted:
            direct = candidate / basename
            if direct.exists() and (direct.is_file() or direct.is_dir()):
                return str(direct)
        for child in candidate.rglob("*"):
            if child.name.lower() in wanted and (child.is_file() or child.is_dir()):
                return str(child)

    for raw_path in reversed(deduped):
        candidate = Path(raw_path).expanduser()
        if not candidate.exists() or not (candidate.is_file() or candidate.is_dir()):
            continue
        if artifact_path_matches_alias(str(candidate), alias):
            return str(candidate)
    return None


def producer_candidates_for_alias(alias: str, nodes: Iterable[Any]) -> List[int]:
    candidates: List[int] = []
    canonical_alias = canonicalize_artifact_alias(alias)
    for node in nodes:
        metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
        contract = infer_artifact_contract(
            task_name=str(getattr(node, "name", "")),
            instruction=str(getattr(node, "instruction", "") or ""),
            metadata=metadata,
        )
        published = [canonicalize_artifact_alias(item) for item in contract.get("publishes", [])]
        if canonical_alias in published:
            candidates.append(int(getattr(node, "id")))
    return candidates
