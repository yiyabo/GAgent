"""Local PhageScope research data utilities.

This tool is intentionally deterministic and lightweight.  It prepares the
local PhageScope public dataset for downstream code_executor modelling tasks
without trying to train heavyweight models inside the web server process.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.resources.resource_registry import resolve_resource


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "phagescope"
_DEFAULT_MIN_LABEL_COUNT = 100
_DEFAULT_COMPLETENESS = ("High-quality", "Medium-quality")
_VALID_LABEL_LEVELS = {"raw", "genus", "species_like"}
_VALID_SPLIT_GROUPS = {"subcluster", "cluster", "phage_id"}
_EXPECTED_METADATA_COLUMNS = [
    "Phage_ID",
    "Length",
    "GC_content",
    "Taxonomy",
    "Completeness",
    "Host",
    "Lifestyle",
    "Cluster",
    "Subcluster",
]
_BROAD_HOST_PREFIXES = {
    "uncultured",
    "unknown",
    "unclassified",
    "metagenome",
    "environmental",
}


class PhageScopeResearchError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _error_payload(message: str, *, code: str) -> Dict[str, Any]:
    return {
        "success": False,
        "tool": "phagescope_research",
        "error": message,
        "error_code": code,
        "no_claude_fallback": True,
    }


def _resolve_data_dir(data_dir: Optional[str]) -> Path:
    raw = str(data_dir or os.getenv("PHAGESCOPE_DATA_DIR") or _DEFAULT_DATA_DIR).strip()
    if not raw:
        raw = str(_DEFAULT_DATA_DIR)
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    lexical = path.absolute()
    if not lexical.exists() or not lexical.is_dir():
        stripped_raw = raw.rstrip(".。；;，,、")
        if stripped_raw != raw:
            stripped_path = Path(stripped_raw)
            if not stripped_path.is_absolute():
                stripped_path = _PROJECT_ROOT / stripped_path
            stripped_lexical = stripped_path.absolute()
            if stripped_lexical.exists() and stripped_lexical.is_dir():
                lexical = stripped_lexical
            else:
                raise PhageScopeResearchError(
                    f"PhageScope data directory does not exist: {lexical}",
                    code="data_dir_missing",
                )
        else:
            raise PhageScopeResearchError(
                f"PhageScope data directory does not exist: {lexical}",
                code="data_dir_missing",
            )
    metadata_dir = lexical / "meta_data"
    if not metadata_dir.exists() or not metadata_dir.is_dir():
        raise PhageScopeResearchError(
            f"Missing meta_data directory under PhageScope data dir: {metadata_dir}",
            code="metadata_dir_missing",
        )
    return lexical


def _resolve_output_dir(output_dir: Optional[str], session_id: Optional[str]) -> Path:
    if isinstance(output_dir, str) and output_dir.strip():
        path = Path(output_dir.strip())
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
    elif isinstance(session_id, str) and session_id.strip():
        try:
            from app.services.session_paths import get_session_tool_outputs_dir

            path = get_session_tool_outputs_dir(session_id.strip(), create=True) / "phagescope_research"
        except Exception:
            path = _PROJECT_ROOT / "runtime" / "phagescope_research"
    else:
        path = _PROJECT_ROOT / "runtime" / "phagescope_research"
    path = path.absolute()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _metadata_files(data_dir: Path) -> List[Path]:
    return sorted(
        p
        for p in (data_dir / "meta_data").glob("*.tsv")
        if p.is_file() and not p.name.startswith(".")
    )


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_missing(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text.lower() in {"unknown", "na", "n/a", "none", "nan", "-"}


def _source_from_file(path: Path) -> str:
    name = path.name.replace("_phage_meta_data.tsv", "")
    return name.upper() if name else "UNKNOWN"


def _host_label(host: Any, level: str) -> Optional[str]:
    text = " ".join(str(host or "").strip().split())
    if _is_missing(text):
        return None
    first = text.split()[0]
    if first.lower() in _BROAD_HOST_PREFIXES:
        return None
    if level == "raw":
        return text
    if level == "genus":
        return first
    if level == "species_like":
        parts = text.split()
        if len(parts) < 2:
            return None
        return " ".join(parts[:2])
    raise PhageScopeResearchError(f"Unsupported label_level: {level}", code="invalid_label_level")


def _normalise_completeness(values: Optional[Sequence[Any]]) -> Tuple[str, ...]:
    if values is None:
        return tuple(_DEFAULT_COMPLETENESS)
    if isinstance(values, str):
        items = [chunk.strip() for chunk in re.split(r"[,;]", values) if chunk.strip()]
    else:
        items = [str(item).strip() for item in values if str(item).strip()]
    return tuple(items or _DEFAULT_COMPLETENESS)


def _iter_metadata_rows(data_dir: Path) -> Iterable[Tuple[Path, Dict[str, str]]]:
    for path in _metadata_files(data_dir):
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                yield path, row


def _split_for_row(row: Dict[str, str], split_group: str) -> str:
    if split_group == "subcluster":
        group = row.get("Subcluster") or row.get("Cluster") or row.get("Phage_ID") or ""
    elif split_group == "cluster":
        group = row.get("Cluster") or row.get("Subcluster") or row.get("Phage_ID") or ""
    else:
        group = row.get("Phage_ID") or ""
    bucket = int(hashlib.sha1(group.encode("utf-8", errors="ignore")).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "val"
    return "test"


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except OSError:
        return 0


def _human_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _directory_size(path: Path) -> int:
    total = 0
    try:
        iterator = path.rglob("*")
    except OSError:
        return 0
    for child in iterator:
        if child.is_file():
            total += _file_size(child)
    return total


def _subdir_size_summary(data_dir: Path) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for child in sorted(data_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        files = [p for p in child.iterdir() if p.is_file()]
        size_bytes = _directory_size(child)
        summary[child.name] = {
            "files": len(files),
            "size_bytes": size_bytes,
            "size_human": _human_bytes(size_bytes),
            "largest_files": [
                {
                    "name": path.name,
                    "size_bytes": _file_size(path),
                    "size_human": _human_bytes(_file_size(path)),
                }
                for path in sorted(files, key=_file_size, reverse=True)[:5]
            ],
        }
    return summary


def _metadata_schema_profile(data_dir: Path) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    header_counts: Counter[Tuple[str, ...]] = Counter()
    missing_expected_by_file: Dict[str, List[str]] = {}
    extra_columns_by_file: Dict[str, List[str]] = {}
    for path in _metadata_files(data_dir):
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                header_line = fh.readline().rstrip("\n")
        except OSError:
            header_line = ""
        columns = header_line.split("\t") if header_line else []
        header_counts[tuple(columns)] += 1
        missing = [name for name in _EXPECTED_METADATA_COLUMNS if name not in columns]
        extra = [name for name in columns if name not in _EXPECTED_METADATA_COLUMNS]
        if missing:
            missing_expected_by_file[path.name] = missing
        if extra:
            extra_columns_by_file[path.name] = extra
        files.append(
            {
                "name": path.name,
                "size_bytes": _file_size(path),
                "size_human": _human_bytes(_file_size(path)),
                "columns": columns,
            }
        )
    most_common_header: List[str] = []
    if header_counts:
        most_common_header = list(header_counts.most_common(1)[0][0])
    return {
        "expected_columns": list(_EXPECTED_METADATA_COLUMNS),
        "files": files,
        "headers_consistent": len(header_counts) <= 1,
        "most_common_header": most_common_header,
        "missing_expected_by_file": missing_expected_by_file,
        "extra_columns_by_file": extra_columns_by_file,
    }


def _ml_metadata_status(data_dir: Path) -> Dict[str, Any]:
    path = data_dir / "ml_metadata_table_genus_cluster.tsv"
    if not path.exists():
        return {"exists": False, "path": str(path), "rows": 0, "empty_or_header_only": True}
    line_count = 0
    preview = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line_count == 0:
                    preview = line.rstrip("\n")
                line_count += 1
                if line_count > 2:
                    break
    except OSError:
        line_count = 0
    rows = max(0, line_count - 1)
    return {
        "exists": True,
        "path": str(path),
        "size_bytes": _file_size(path),
        "size_human": _human_bytes(_file_size(path)),
        "observed_lines": line_count,
        "rows": rows,
        "header": preview,
        "empty_or_header_only": rows == 0,
    }


def _audit(data_dir: Path, *, top_n: int = 30) -> Dict[str, Any]:
    row_counts: Dict[str, int] = {}
    host_counts: Counter[str] = Counter()
    taxonomy_counts: Counter[str] = Counter()
    completeness_counts: Counter[str] = Counter()
    lifestyle_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    ids: set[str] = set()
    duplicate_ids = 0

    for path, row in _iter_metadata_rows(data_dir):
        row_counts[path.name] = row_counts.get(path.name, 0) + 1
        phage_id = str(row.get("Phage_ID") or "").strip()
        if phage_id:
            if phage_id in ids:
                duplicate_ids += 1
            ids.add(phage_id)

        source = str(row.get("Phage_source") or _source_from_file(path)).strip()
        if source:
            source_counts[source] += 1

        for key, counter in (
            ("Host", host_counts),
            ("Taxonomy", taxonomy_counts),
            ("Completeness", completeness_counts),
            ("Lifestyle", lifestyle_counts),
        ):
            value = str(row.get(key) or "").strip()
            if _is_missing(value):
                missing_counts[key] += 1
            else:
                counter[value] += 1

    file_counts: Dict[str, int] = {}
    for subdir in sorted(p.name for p in data_dir.iterdir() if p.is_dir()):
        file_counts[subdir] = len([p for p in (data_dir / subdir).iterdir() if p.is_file()])

    hidden_files = [
        {"path": str(p), "bytes": p.stat().st_size}
        for p in sorted(data_dir.rglob(".fuse_hidden*"))
        if p.is_file()
    ]
    resource = resolve_resource("phagescope.sequence_corpus")
    resources = {"phagescope.sequence_corpus": resource.to_dict()} if resource else {}

    return {
        "success": True,
        "tool": "phagescope_research",
        "action": "audit",
        "data_dir": str(data_dir),
        "resolved_data_dir": str(data_dir.resolve()),
        "metadata_files": len(row_counts),
        "metadata_rows": sum(row_counts.values()),
        "unique_phage_ids": len(ids),
        "duplicate_phage_ids": duplicate_ids,
        "rows_by_metadata_file": dict(sorted(row_counts.items())),
        "file_counts_by_subdir": file_counts,
        "missing_counts": dict(missing_counts),
        "source_top": source_counts.most_common(top_n),
        "completeness_counts": completeness_counts.most_common(),
        "lifestyle_counts": lifestyle_counts.most_common(),
        "taxonomy_top": taxonomy_counts.most_common(top_n),
        "host_unique": len(host_counts),
        "host_top": host_counts.most_common(top_n),
        "hidden_fuse_files": hidden_files[:20],
        "hidden_fuse_file_count": len(hidden_files),
        "resources": resources,
        "resource_contract": {"requires": ["resource:phagescope.sequence_corpus"]},
        "code_executor_add_dirs": [str(data_dir), str(data_dir.resolve())],
        "notes": [
            "Use the listed code_executor_add_dirs for Docker/Qwen tasks; the project phagescope path may be a symlink.",
            "Do not use random splits as the primary result; use Cluster/Subcluster-aware splits.",
            "Some phage_fasta files use a .fasta extension but are gzip-compressed tar archives; use tarfile.open(path, \"r:*\") and stream FASTA members.",
        ],
    }


def _deep_profile(data_dir: Path, *, top_n: int = 30) -> Dict[str, Any]:
    audit = _audit(data_dir, top_n=top_n)
    subdir_sizes = _subdir_size_summary(data_dir)
    metadata_size_bytes = subdir_sizes.get("meta_data", {}).get("size_bytes", 0)
    total_size_bytes = sum(item.get("size_bytes", 0) for item in subdir_sizes.values())
    metadata_schema = _metadata_schema_profile(data_dir)
    ml_status = _ml_metadata_status(data_dir)
    missing_counts_raw = audit.get("missing_counts")
    missing_counts: Dict[str, Any] = missing_counts_raw if isinstance(missing_counts_raw, dict) else {}
    metadata_rows = int(audit.get("metadata_rows") or 0)
    host_missing = int(missing_counts.get("Host") or 0)
    host_available = max(0, metadata_rows - host_missing)
    host_missing_fraction = round(host_missing / metadata_rows, 6) if metadata_rows else None
    cluster_columns = metadata_schema.get("most_common_header") or []
    split_readiness = {
        "recommended_primary_split": "subcluster",
        "robustness_split": "cluster",
        "supports_subcluster_grouping": "Subcluster" in cluster_columns,
        "supports_cluster_grouping": "Cluster" in cluster_columns,
        "warning": "Random row-level splits can leak near-identical phages; use Cluster/Subcluster-aware grouping.",
    }
    label_quality = {
        "host_available_rows": host_available,
        "host_missing_rows": host_missing,
        "host_missing_fraction": host_missing_fraction,
        "host_unique": audit.get("host_unique"),
        "host_top": audit.get("host_top"),
        "completeness_counts": audit.get("completeness_counts"),
        "lifestyle_counts": audit.get("lifestyle_counts"),
    }
    anomalies = {
        "hidden_fuse_file_count": audit.get("hidden_fuse_file_count", 0),
        "hidden_fuse_files": audit.get("hidden_fuse_files", []),
        "non_data_log_files": [
            str(path)
            for path in sorted(data_dir.rglob("*.log"))[:20]
            if path.is_file()
        ],
    }
    return {
        "success": True,
        "tool": "phagescope_research",
        "action": "deep_profile",
        "data_dir": str(data_dir),
        "resolved_data_dir": str(data_dir.resolve()),
        "total_size_bytes": total_size_bytes,
        "total_size_human": _human_bytes(total_size_bytes),
        "metadata_size_bytes": metadata_size_bytes,
        "metadata_size_human": _human_bytes(metadata_size_bytes),
        "metadata_files": audit.get("metadata_files"),
        "metadata_rows": audit.get("metadata_rows"),
        "unique_phage_ids": audit.get("unique_phage_ids"),
        "duplicate_phage_ids": audit.get("duplicate_phage_ids"),
        "rows_by_metadata_file": audit.get("rows_by_metadata_file"),
        "subdir_size_summary": subdir_sizes,
        "metadata_schema": metadata_schema,
        "ml_metadata_table": ml_status,
        "label_quality": label_quality,
        "source_top": audit.get("source_top"),
        "taxonomy_top": audit.get("taxonomy_top"),
        "split_readiness": split_readiness,
        "annotation_inventory": audit.get("file_counts_by_subdir"),
        "anomalies": anomalies,
        "resources": audit.get("resources"),
        "resource_contract": audit.get("resource_contract"),
        "code_executor_add_dirs": audit.get("code_executor_add_dirs"),
        "claim_guidance": [
            "Use metadata_size_bytes/metadata_size_human for meta_data size claims; do not estimate metadata as the whole dataset size.",
            "Use total_size_bytes/total_size_human for whole-directory size claims.",
            "If ml_metadata_table.empty_or_header_only is true, say the ML-ready table has not been built yet.",
            "Numeric file, row, and size claims in the final answer should come from this deep_profile payload.",
        ],
        "recommended_next_step": (
            "Build the curated metadata table with action=prepare_metadata_table, then train metadata-only baselines "
            "before adding k-mer and annotation features."
        ),
    }


def _prepare_metadata_table(
    data_dir: Path,
    *,
    output_dir: Path,
    label_level: str,
    min_label_count: int,
    completeness: Tuple[str, ...],
    split_group: str,
    max_rows: Optional[int],
) -> Dict[str, Any]:
    if label_level not in _VALID_LABEL_LEVELS:
        raise PhageScopeResearchError(f"label_level must be one of {sorted(_VALID_LABEL_LEVELS)}", code="invalid_label_level")
    if split_group not in _VALID_SPLIT_GROUPS:
        raise PhageScopeResearchError(f"split_group must be one of {sorted(_VALID_SPLIT_GROUPS)}", code="invalid_split_group")
    if min_label_count <= 0:
        raise PhageScopeResearchError("min_label_count must be positive.", code="invalid_min_label_count")

    completeness_set = set(completeness)
    label_counts: Counter[str] = Counter()
    first_pass_rows = 0
    for _path, row in _iter_metadata_rows(data_dir):
        if completeness_set and row.get("Completeness") not in completeness_set:
            continue
        label = _host_label(row.get("Host"), label_level)
        if not label:
            continue
        label_counts[label] += 1
        first_pass_rows += 1

    kept_labels = {label for label, count in label_counts.items() if count >= min_label_count}
    if not kept_labels:
        raise PhageScopeResearchError("No host labels satisfy min_label_count.", code="empty_label_set")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = f"phagescope_metadata_{label_level}_{timestamp}"
    table_path = output_dir / f"{stem}.tsv"
    counts_path = output_dir / f"{stem}_label_counts.tsv"
    summary_path = output_dir / f"{stem}_summary.json"
    canonical_table_path = output_dir / "curated_metadata.tsv"
    canonical_counts_path = output_dir / "label_counts.tsv"
    canonical_summary_path = output_dir / "metadata_summary.json"

    columns = [
        "Phage_ID",
        "Length",
        "GC_content",
        "Taxonomy",
        "Completeness",
        "Lifestyle",
        "Cluster",
        "Subcluster",
        "Phage_source",
        "Host",
        "Host_label",
        "Split",
    ]

    split_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    written = 0
    with table_path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for path, row in _iter_metadata_rows(data_dir):
            if completeness_set and row.get("Completeness") not in completeness_set:
                continue
            label = _host_label(row.get("Host"), label_level)
            if label not in kept_labels:
                continue
            length = _safe_int(row.get("Length"))
            gc_content = _safe_float(row.get("GC_content"))
            if length is None or gc_content is None:
                continue
            source = str(row.get("Phage_source") or _source_from_file(path)).strip()
            split = _split_for_row(row, split_group)
            writer.writerow(
                {
                    "Phage_ID": row.get("Phage_ID", ""),
                    "Length": length,
                    "GC_content": gc_content,
                    "Taxonomy": row.get("Taxonomy", ""),
                    "Completeness": row.get("Completeness", ""),
                    "Lifestyle": row.get("Lifestyle", ""),
                    "Cluster": row.get("Cluster", ""),
                    "Subcluster": row.get("Subcluster", ""),
                    "Phage_source": source,
                    "Host": row.get("Host", ""),
                    "Host_label": label,
                    "Split": split,
                }
            )
            split_counts[split][label] += 1
            written += 1
            if max_rows and written >= max_rows:
                break

    with counts_path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow(["Host_label", "Total_count", "Written_count"])
        written_counts = Counter()
        for split_counter in split_counts.values():
            written_counts.update(split_counter)
        for label, count in label_counts.most_common():
            if label in kept_labels:
                writer.writerow([label, count, written_counts.get(label, 0)])

    split_totals = {split: sum(counter.values()) for split, counter in split_counts.items()}
    summary = {
        "tool": "phagescope_research",
        "action": "prepare_metadata_table",
        "data_dir": str(data_dir),
        "resolved_data_dir": str(data_dir.resolve()),
        "output_table": str(table_path),
        "label_counts_path": str(counts_path),
        "summary_path": str(summary_path),
        "canonical_output_table": str(canonical_table_path),
        "canonical_label_counts_path": str(canonical_counts_path),
        "canonical_summary_path": str(canonical_summary_path),
        "label_level": label_level,
        "min_label_count": min_label_count,
        "completeness": list(completeness),
        "split_group": split_group,
        "candidate_rows_before_label_min": first_pass_rows,
        "labels_before_filter": len(label_counts),
        "labels_kept": len(kept_labels),
        "rows_written": written,
        "split_totals": split_totals,
        "top_labels": label_counts.most_common(30),
        "recommended_next_step": (
            "Use code_executor with this TSV as input to train baseline models. "
            "Keep Split fixed and report test metrics separately."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(table_path, canonical_table_path)
    shutil.copy2(counts_path, canonical_counts_path)
    canonical_summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "success": True,
        **summary,
        "artifact_paths": [
            str(canonical_table_path),
            str(canonical_counts_path),
            str(canonical_summary_path),
            str(table_path),
            str(counts_path),
            str(summary_path),
        ],
        "session_artifact_paths": [
            str(canonical_table_path),
            str(canonical_counts_path),
            str(canonical_summary_path),
        ],
        "output_table_rows": _count_lines(table_path),
    }


def _research_plan(data_dir: Path) -> Dict[str, Any]:
    return {
        "success": True,
        "tool": "phagescope_research",
        "action": "research_plan",
        "data_dir": str(data_dir),
        "steps": [
            "Run action=audit and inspect missing labels, class imbalance, hidden files, and code_executor_add_dirs.",
            "Run action=prepare_metadata_table with label_level=genus first, completeness=High-quality,Medium-quality, and split_group=subcluster.",
            "Train metadata-only baseline models with code_executor using the prepared TSV and its fixed Split column.",
            "Add sequence k-mer features in a separate cached stage; detect gzip tar archives before reading phage_fasta.",
            "Add annotation-derived count features from protein, CRISPR, tRNA, terminator, transmembrane, AMR, and virulence TSVs.",
            "Evaluate top-k accuracy, macro-F1, source-stratified metrics, and lifestyle-stratified metrics.",
            "Write methods and limitations: this predicts host taxa from PhageScope labels, not experimentally validated strain-specific interactions.",
        ],
        "required_acceptance_outputs": [
            "data_audit.json",
            "curated_metadata.tsv",
            "label_counts.tsv",
            "split_summary.json",
            "model_metrics.json",
            "confusion_or_topk_figures",
            "methods_and_limitations.md",
        ],
    }


async def phagescope_research_handler(
    action: str = "audit",
    data_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    label_level: str = "genus",
    min_label_count: int = _DEFAULT_MIN_LABEL_COUNT,
    completeness: Optional[Sequence[Any]] = None,
    split_group: str = "subcluster",
    max_rows: Optional[int] = None,
    top_n: int = 30,
) -> Dict[str, Any]:
    """Handle local PhageScope research data preparation actions."""
    try:
        resolved_data_dir = _resolve_data_dir(data_dir)
        normalized_action = str(action or "audit").strip().lower()
        if normalized_action == "audit":
            return _audit(resolved_data_dir, top_n=max(1, int(top_n or 30)))
        if normalized_action == "deep_profile":
            return _deep_profile(resolved_data_dir, top_n=max(1, int(top_n or 30)))
        if normalized_action == "research_plan":
            return _research_plan(resolved_data_dir)
        if normalized_action == "prepare_metadata_table":
            out_dir = _resolve_output_dir(output_dir, session_id)
            return _prepare_metadata_table(
                resolved_data_dir,
                output_dir=out_dir,
                label_level=str(label_level or "genus").strip(),
                min_label_count=int(min_label_count or _DEFAULT_MIN_LABEL_COUNT),
                completeness=_normalise_completeness(completeness),
                split_group=str(split_group or "subcluster").strip().lower(),
                max_rows=int(max_rows) if max_rows else None,
            )
        return _error_payload(f"Unsupported action: {action}", code="invalid_action")
    except PhageScopeResearchError as exc:
        return _error_payload(str(exc), code=exc.code)
    except Exception as exc:
        return _error_payload(f"{type(exc).__name__}: {exc}", code="unexpected_error")


phagescope_research_tool = {
    "name": "phagescope_research",
    "description": (
        "Prepare, audit, and deep-profile the local PhageScope public dataset for host taxon "
        "prediction research. Use deep_profile before code_executor or final synthesis for PhageScope ML/data exploration tasks."
    ),
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["audit", "deep_profile", "research_plan", "prepare_metadata_table"],
                "default": "audit",
                "description": "Operation to perform.",
            },
            "data_dir": {
                "type": "string",
                "description": "Local PhageScope data directory. Defaults to PHAGESCOPE_DATA_DIR or <repo>/phagescope.",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for prepared TSV/JSON outputs.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional chat session id for session-scoped outputs.",
            },
            "label_level": {
                "type": "string",
                "enum": ["raw", "genus", "species_like"],
                "default": "genus",
                "description": "How to standardize Host labels for model targets.",
            },
            "min_label_count": {
                "type": "integer",
                "default": _DEFAULT_MIN_LABEL_COUNT,
                "description": "Minimum class count retained in prepared metadata table.",
            },
            "completeness": {
                "description": "Allowed Completeness values. Defaults to High-quality and Medium-quality.",
            },
            "split_group": {
                "type": "string",
                "enum": ["subcluster", "cluster", "phage_id"],
                "default": "subcluster",
                "description": "Grouping key for deterministic leakage-aware train/val/test split.",
            },
            "max_rows": {
                "type": "integer",
                "description": "Optional cap for smoke tests. Omit for full prepared table.",
            },
            "top_n": {
                "type": "integer",
                "default": 30,
                "description": "Number of top categories to include in audit output.",
            },
        },
    },
    "handler": phagescope_research_handler,
    "tags": ["phagescope", "machine-learning", "host", "taxonomy", "dataset"],
    "examples": [
        "Audit the local PhageScope dataset before host prediction modelling.",
        "Prepare a genus-level metadata table with Subcluster-aware splits.",
    ],
}
