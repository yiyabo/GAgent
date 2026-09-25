"""Local (draft_only) manuscript assembly from completed task outputs.

Buckets result/method/supplementary Markdown sources and renders a draft plus
its analysis memo without any LLM call. ``_PROJECT_ROOT`` is read through the
late-bound ``_facade()`` accessor because tests patch it on the package
namespace.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import _CONTEXT_FILE_MAX_BYTES, _DEFAULT_SECTIONS
from .paths import _read_text_file, _resolve_project_path
from .rubrics import _normalize_section_key, _section_title


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import manuscript_writer as facade

    return facade


def _demote_markdown_headings(text: str, *, levels: int = 1) -> str:
    if levels <= 0:
        return str(text or "").strip()

    def _replace(match: re.Match[str]) -> str:
        hashes = match.group(1)
        suffix = match.group(2)
        return f"{'#' * min(6, len(hashes) + levels)}{suffix}"

    return re.sub(r"^(#{1,6})(\s.*)$", _replace, str(text or "").strip(), flags=re.MULTILINE)


def _local_draft_bucket(path: str) -> Optional[str]:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return None
    if "/manuscript/results/" in normalized or normalized.startswith("manuscript/results/"):
        return "result"
    if (
        "/methods/" in normalized
        or normalized.startswith("methods/")
        or normalized.endswith("data_source_preprocessing.md")
    ):
        return "method"
    if normalized.endswith("_summary.md") or normalized.endswith("_summary.txt"):
        return "supplementary"
    return None


def _local_draft_sort_key(bucket: str, path: str) -> Tuple[int, str]:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if bucket == "method":
        for idx, token in enumerate(
            (
                "data_source_preprocessing",
                "clustering_annotation",
                "differential_enrichment",
                "cell_communication",
            )
        ):
            if token in normalized:
                return idx, normalized
        return 99, normalized
    if bucket == "result":
        match = re.search(r"5\.1\.3\.(\d+)", normalized)
        if match:
            return int(match.group(1)), normalized
        return 99, normalized
    return 99, normalized


def _assemble_local_draft_from_context(
    *,
    task: str,
    context_paths: List[str],
    max_context_bytes: int,
    section_list: List[str],
) -> Tuple[str, str, List[str], Dict[str, int], Dict[str, str]]:
    grouped: Dict[str, List[Tuple[Tuple[int, str], str, str]]] = {
        "method": [],
        "result": [],
        "supplementary": [],
    }
    used_sources: List[str] = []

    for raw in context_paths:
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            path = _resolve_project_path(value)
        except Exception:
            continue
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        bucket = _local_draft_bucket(str(path.relative_to(_facade()._PROJECT_ROOT)))
        if bucket is None:
            continue
        try:
            content = _read_text_file(path, min(max_context_bytes, _CONTEXT_FILE_MAX_BYTES)).strip()
        except Exception:
            continue
        if not content:
            continue
        rel = str(path.relative_to(_facade()._PROJECT_ROOT))
        grouped[bucket].append((_local_draft_sort_key(bucket, rel), rel, content))
        used_sources.append(rel)

    for bucket in grouped:
        grouped[bucket].sort(key=lambda item: item[0])

    section_counts = {bucket: len(items) for bucket, items in grouped.items()}

    def _render_group(items: List[Tuple[Tuple[int, str], str, str]], *, fallback: str) -> str:
        if not items:
            return fallback
        rendered: List[str] = []
        for _sort_key, rel, content in items:
            block = _demote_markdown_headings(content, levels=1)
            if not block.startswith("#"):
                title = Path(rel).stem.replace("_", " ").strip() or rel
                block = f"### {title}\n\n{block}"
            rendered.append(block)
        return "\n\n".join(rendered).strip()

    parts: List[str] = [
        "# Manuscript Draft",
        "",
        "> Auto-assembled locally from completed task outputs without additional literature review or re-analysis.",
        "",
    ]
    ordered_sections = section_list or list(_DEFAULT_SECTIONS)
    section_text_map: Dict[str, str] = {}
    for section in ordered_sections:
        key = _normalize_section_key(section)
        title = _section_title(key)
        parts.extend([f"## {title}", ""])
        section_body = ""
        if key == "abstract":
            section_body = "Not available in provided context."
        elif key == "introduction":
            section_body = "Not available in provided context."
        elif key == "method":
            section_body = _render_group(
                grouped["method"],
                fallback="Not available in provided context.",
            )
        elif key == "experiment":
            section_body = "Not available in provided context."
        elif key == "result":
            section_body = _render_group(
                grouped["result"],
                fallback="Not available in provided context.",
            )
        elif key == "discussion":
            section_body = _render_group(
                grouped["supplementary"],
                fallback="Pending final synthesis from the completed result sections above.",
            )
        elif key == "conclusion":
            section_body = "Pending final synthesis from the completed result sections above."
        elif key == "references":
            section_body = "Not available in provided context."
        else:
            section_body = "Not available in provided context."
        section_text_map[key] = str(section_body).strip()
        parts.append(section_body)
        parts.extend(["", ""])

    analysis_lines = [
        "# Analysis Memo",
        "",
        "- mode: local_draft_assembly",
        f"- task: {task}",
        f"- source_files_used: {len(used_sources)}",
        f"- method_sources: {section_counts['method']}",
        f"- result_sources: {section_counts['result']}",
        f"- supplementary_sources: {section_counts['supplementary']}",
        "",
        "## Included source files",
        "",
    ]
    if used_sources:
        analysis_lines.extend(f"- {path}" for path in used_sources)
    else:
        analysis_lines.append("- None")
    analysis_lines.extend(["", "## Notes", "", "- This draft was assembled locally from existing Markdown outputs.", "- Missing sections remain explicitly marked as not available.", ""])

    return (
        "\n".join(parts).strip() + "\n",
        "\n".join(analysis_lines).strip() + "\n",
        used_sources,
        section_counts,
        section_text_map,
    )
