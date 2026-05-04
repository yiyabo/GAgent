from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ResourceSpec:
    id: str
    name: str
    candidate_roots: Tuple[str, ...]
    required_subpaths: Tuple[str, ...] = ()
    format_hints: Tuple[str, ...] = ()
    description: str = ""
    aliases: Tuple[str, ...] = ()
    env_var: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedResource:
    id: str
    name: str
    root: str
    resolved_root: str
    required_paths: Tuple[str, ...]
    format_hints: Tuple[str, ...]
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "root": self.root,
            "resolved_root": self.resolved_root,
            "required_paths": list(self.required_paths),
            "format_hints": list(self.format_hints),
            "description": self.description,
            "metadata": dict(self.metadata),
        }


_REGISTRY: Dict[str, ResourceSpec] = {
    "phagescope.sequence_corpus": ResourceSpec(
        id="phagescope.sequence_corpus",
        name="PhageScope sequence corpus",
        candidate_roots=(
            "/home/zczhao/Phage-Agent/phagescope",
            "/mnt/sdm/zczhao/Phage-Agent/phagescope",
        ),
        required_subpaths=("phage_fasta",),
        format_hints=(
            "Sequence files live under phage_fasta/.",
            "Files with .fasta extension may be gzip-compressed tar archives; use tarfile.open(path, 'r:*') and stream member FASTA files, falling back to gzip.open only when the decompressed payload is plain FASTA.",
            "FASTA record IDs may already be the modeling genome IDs (for example TemPhD_cluster_10017); do not assume they must be NCBI accessions or call sequence_fetch before checking local FASTA headers.",
            "For embedding tasks, first map requested genome_ids to FASTA headers in this corpus, extract sequences into the task workspace, then run the pretrained DNA model on those sequences.",
            "Process large FASTA corpora with streaming parsers; do not load all genomes into memory at once.",
        ),
        description="Local PhageScope phage genome FASTA corpus for k-mer and host modeling tasks.",
        aliases=(
            "phagescope",
            "phagescope_corpus",
            "phagescope.sequence",
            "phage_fasta",
        ),
        env_var="PHAGESCOPE_DATA_DIR",
        metadata={
            "primary_subdir": "phage_fasta",
            "file_glob": "phage_fasta/*.fasta",
            "compression": "gzip_tar_possible",
        },
    ),
}

_ALIAS_TO_ID: Dict[str, str] = {}
for _resource_id, _spec in _REGISTRY.items():
    _ALIAS_TO_ID[_resource_id.lower()] = _resource_id
    for _alias in _spec.aliases:
        _ALIAS_TO_ID[str(_alias).strip().lower()] = _resource_id


def normalize_resource_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("resource:"):
        text = text.split(":", 1)[1].strip()
    return _ALIAS_TO_ID.get(text.lower(), text)


def list_resource_specs() -> List[ResourceSpec]:
    return list(_REGISTRY.values())


def get_resource_spec(resource_id: Any) -> Optional[ResourceSpec]:
    normalized = normalize_resource_id(resource_id)
    return _REGISTRY.get(normalized)


def _candidate_roots(spec: ResourceSpec) -> Iterable[Path]:
    if spec.env_var:
        raw_env = os.getenv(spec.env_var)
        if raw_env:
            yield Path(raw_env).expanduser()
    for raw_root in spec.candidate_roots:
        yield Path(raw_root).expanduser()


def resolve_resource(resource_id: Any) -> Optional[ResolvedResource]:
    spec = get_resource_spec(resource_id)
    if spec is None:
        return None

    for candidate in _candidate_roots(spec):
        try:
            lexical = candidate.absolute()
            if not lexical.exists() or not lexical.is_dir():
                continue
            required_paths: List[str] = []
            missing_required = False
            for rel in spec.required_subpaths:
                required = lexical / rel
                if not required.exists():
                    missing_required = True
                    break
                required_paths.append(str(required.absolute()))
            if missing_required:
                continue
            try:
                resolved_root = lexical.resolve()
            except OSError:
                resolved_root = lexical
            return ResolvedResource(
                id=spec.id,
                name=spec.name,
                root=str(lexical),
                resolved_root=str(resolved_root),
                required_paths=tuple(required_paths),
                format_hints=spec.format_hints,
                description=spec.description,
                metadata=spec.metadata,
            )
        except OSError:
            continue
    return None


def resolve_resources(resource_ids: Iterable[Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    resolved: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    seen: set[str] = set()
    for raw_id in resource_ids:
        resource_id = normalize_resource_id(raw_id)
        if not resource_id or resource_id in seen:
            continue
        seen.add(resource_id)
        resource = resolve_resource(resource_id)
        if resource is None:
            missing.append(resource_id)
            continue
        resolved[resource_id] = resource.to_dict()
    return resolved, missing
