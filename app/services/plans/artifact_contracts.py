from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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
}

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
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def canonical_artifact_path(plan_id: int, alias: str) -> Optional[Path]:
    spec = _ARTIFACT_SPECS.get(alias)
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
            if alias in _ARTIFACT_SPECS and alias not in seen:
                seen.add(alias)
                aliases.append(alias)
    return aliases


def aliases_for_file_name(file_name: str, *, preferred_namespace: str) -> List[str]:
    lowered = Path(str(file_name or "")).name.lower()
    aliases = list(_LEGACY_BASENAME_TO_ALIASES.get(lowered, ()))
    if lowered in _GENERIC_BASENAME_TO_SLOT:
        alias = f"{preferred_namespace}.{_GENERIC_BASENAME_TO_SLOT[lowered]}"
        if alias in _ARTIFACT_SPECS and alias not in aliases:
            aliases.append(alias)
    for alias in _semantic_aliases_for_file_name(
        lowered,
        preferred_namespace=preferred_namespace,
    ):
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def candidate_filenames_for_alias(alias: str) -> List[str]:
    spec = _ARTIFACT_SPECS.get(alias)
    names: List[str] = []
    if spec:
        names.append(spec[1].lower())
    for basename, aliases in _LEGACY_BASENAME_TO_ALIASES.items():
        if alias in aliases and basename.lower() not in names:
            names.append(basename.lower())
    return names


def is_artifact_alias(value: str) -> bool:
    return str(value or "").strip() in _ARTIFACT_SPECS


def _semantic_basename_matches_alias(*, basename: str, alias: str) -> bool:
    spec = _ARTIFACT_SPECS.get(alias)
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
    basename = Path(str(path_text or "")).name.lower()
    if not basename or alias not in _ARTIFACT_SPECS:
        return False
    if basename in candidate_filenames_for_alias(alias):
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

    def requires(self) -> List[str]:
        return _merge_unique(
            self.explicit_requires, self.inferred_requires, self.runtime_requires
        )

    def publishes(self) -> List[str]:
        return _merge_unique(
            self.explicit_publishes, self.inferred_publishes, self.runtime_publishes
        )

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
        return {"requires": self.requires(), "publishes": self.publishes()}


def _merge_unique(*buckets: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    merged: List[str] = []
    for bucket in buckets:
        for alias in bucket:
            text = str(alias or "").strip()
            if text and text not in seen:
                seen.add(text)
                merged.append(text)
    return merged


def _extract_explicit_aliases(raw_items: Any) -> List[str]:
    if not isinstance(raw_items, list):
        return []
    aliases: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if text and text in _ARTIFACT_SPECS and text not in seen:
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
                if alias in explicit_require_set or alias in seen_inferred_req:
                    continue
                seen_inferred_req.add(alias)
                inferred_requires.append(alias)

    for alias in aliases_for_path_text(
        instruction, preferred_namespace=preferred_namespace
    ):
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
                if alias in explicit_publish_set or alias in seen_inferred_pub:
                    continue
                seen_inferred_pub.add(alias)
                inferred_publishes.append(alias)

    return ArtifactContractProvenance(
        explicit_requires=explicit_requires,
        explicit_publishes=explicit_publishes,
        inferred_requires=inferred_requires,
        inferred_publishes=inferred_publishes,
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
    canonical = canonical_artifact_path(plan_id, alias)
    if canonical is None:
        return None
    source = Path(str(source_path or "").strip()).expanduser()
    if not source.exists() or not source.is_file():
        return None
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != canonical.resolve():
        shutil.copy2(source, canonical)
    entry = {
        "alias": alias,
        "path": str(canonical.resolve()),
        "producer_task_id": producer_task_id,
        "source_path": str(source.resolve()),
        "updated_at": time.time(),
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
        entry = artifacts.get(alias)
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if path and Path(path).exists():
            resolved[alias] = path
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
    for raw_path in reversed(deduped):
        basename = Path(raw_path).name.lower()
        if basename in wanted and Path(raw_path).exists():
            return raw_path
    for raw_path in reversed(deduped):
        candidate = Path(raw_path)
        if not candidate.exists() or not candidate.is_file():
            continue
        if artifact_path_matches_alias(raw_path, alias):
            return raw_path
    return None


def producer_candidates_for_alias(alias: str, nodes: Iterable[Any]) -> List[int]:
    candidates: List[int] = []
    for node in nodes:
        metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
        contract = infer_artifact_contract(
            task_name=str(getattr(node, "name", "")),
            instruction=str(getattr(node, "instruction", "") or ""),
            metadata=metadata,
        )
        if alias in contract.get("publishes", []):
            candidates.append(int(getattr(node, "id")))
    return candidates
