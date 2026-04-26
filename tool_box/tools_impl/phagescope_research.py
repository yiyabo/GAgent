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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "phagescope"
_DEFAULT_MIN_LABEL_COUNT = 100
_DEFAULT_COMPLETENESS = ("High-quality", "Medium-quality")
_VALID_LABEL_LEVELS = {"raw", "genus", "species_like"}
_VALID_SPLIT_GROUPS = {"subcluster", "cluster", "phage_id"}
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
        "code_executor_add_dirs": [str(data_dir), str(data_dir.resolve())],
        "notes": [
            "Use the listed code_executor_add_dirs for Docker/Qwen tasks; the project phagescope path may be a symlink.",
            "Do not use random splits as the primary result; use Cluster/Subcluster-aware splits.",
            "Some phage_fasta files are gzip-compressed tar archives, not plain gzipped FASTA streams.",
        ],
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

    return {
        "success": True,
        **summary,
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
        "Prepare and audit the local PhageScope public dataset for host taxon "
        "prediction research. Use this before code_executor for PhageScope ML tasks."
    ),
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["audit", "research_plan", "prepare_metadata_table"],
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
