from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence

_POSITIVE_DELIVERABLE_LINE_CUES = (
    "save",
    "saved",
    "write",
    "written",
    "output",
    "outputs",
    "generate",
    "generated",
    "create",
    "created",
    "export",
    "exported",
    "produce",
    "produced",
    "store",
    "stored",
    "保存",
    "写入",
    "输出",
    "生成",
    "导出",
    "产出",
)

_DELIVERABLE_SECTION_CUES = (
    "output",
    "outputs",
    "deliverable",
    "deliverables",
    "artifact",
    "artifacts",
    "result",
    "results",
    "summary",
    "summaries",
    "报告",
    "清单",
    "文件",
    "产物",
    "输出",
    "结果",
)

_NEGATIVE_DELIVERABLE_NEARBY_PATTERNS = (
    re.compile(r"(?:^|[\s(\[{'\"`])(?:read|reads|reading|load|loads|loading|loaded|input|inputs|source|sources)(?:[\s:=-]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[\s(\[{'\"`])(?:use|uses|using|reference|references|upstream|dependency|dependencies|existing|from|via)(?:[\s:=-]|$)", re.IGNORECASE),
    re.compile(r"(?:读取|使用|载入|输入|来源|参考|上游|依赖|现有|来自|基于)"),
)
_TOOL_ROLE_NEARBY_PATTERNS = (
    re.compile(r"\b(?:script|scripts|notebook|notebooks|tool|tools|module|modules|pipeline|workflow)\b", re.IGNORECASE),
    re.compile(r"(?:脚本|工具|模块|流程|notebook)"),
)
_DELIVERABLE_SECTION_SUFFIXES = (
    "outputs",
    "output",
    "files",
    "deliverables",
    "artifacts",
    "results",
    "summary",
    "summaries",
    "报告",
    "清单",
    "文件",
    "产物",
    "输出",
    "结果",
)
_MARKDOWN_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s*")
_SECTION_BULLET_PREFIX_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+")

_DELIVERABLE_LINE_SECTION_LIMIT = 16
_DELIVERABLE_TOOL_CONTEXT_LIMIT = 40
_DELIVERABLE_POSITIVE_CONTEXT_LIMIT = 96

_DELIVERABLE_TOKEN_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)?"
    r"[A-Za-z0-9._*?\-\[\]]+"
    r"(?:[\\/][A-Za-z0-9._*?\-\[\]]+)*"
    r"\.(?:csv|tsv|txt|json|jsonl|md|markdown|png|jpg|jpeg|svg|pdf|html|htm|xlsx|xls|docx|pptx|"
    r"h5ad|h5|npy|npz|pkl|pickle|yaml|yml|fa|fasta|fna|faa|gff|gff3|bed|pdb|py|ipynb))",
    re.IGNORECASE,
)
_BULLET_PREFIX_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+")


def _normalize_deliverable_candidate(raw: str) -> str:
    text = str(raw or "").strip().strip("`'\"(),;:")
    return text.replace("\\", "/")


def _positive_deliverable_positions(line: str) -> List[int]:
    lowered = str(line or "").lower()
    positions: List[int] = []
    seen: set[int] = set()
    for cue in _POSITIVE_DELIVERABLE_LINE_CUES:
        token = cue.lower()
        start = 0
        while True:
            index = lowered.find(token, start)
            if index < 0:
                break
            end_index = index + len(token)
            if end_index not in seen:
                seen.add(end_index)
                positions.append(end_index)
            start = end_index
    positions.sort()
    return positions


def _clean_section_heading(line: str) -> str:
    text = str(line or "").strip()
    text = _SECTION_BULLET_PREFIX_RE.sub("", text)
    text = _MARKDOWN_HEADING_PREFIX_RE.sub("", text).strip()
    text = text.strip("`'\"")
    if text.endswith((":", "：")):
        text = text[:-1].strip()
    return text.casefold()


def _is_deliverable_section_heading(line: str) -> bool:
    raw = str(line or "").strip()
    heading = _clean_section_heading(raw)
    if not heading:
        return False
    if not any(cue in heading for cue in _DELIVERABLE_SECTION_CUES):
        return False
    if raw.endswith((":", "：")) or raw.startswith("#"):
        return True
    return any(heading.endswith(suffix) for suffix in _DELIVERABLE_SECTION_SUFFIXES)


def _has_negative_deliverable_context(prefix: str) -> bool:
    window = str(prefix or "")[-_DELIVERABLE_LINE_SECTION_LIMIT :]
    return any(pattern.search(window) for pattern in _NEGATIVE_DELIVERABLE_NEARBY_PATTERNS)


def _looks_like_tool_reference(candidate: str, prefix: str) -> bool:
    _root, suffix = os.path.splitext(str(candidate or "").strip().lower())
    if suffix not in {".py", ".ipynb"}:
        return False
    window = str(prefix or "")[-_DELIVERABLE_TOOL_CONTEXT_LIMIT :]
    return any(pattern.search(window) for pattern in _TOOL_ROLE_NEARBY_PATTERNS)


def _candidate_has_output_intent(
    line: str,
    *,
    candidate: str,
    match_start: int,
    positive_positions: Sequence[int],
    capture_following: bool,
) -> bool:
    prefix = line[:match_start]
    if not capture_following and not any(position <= match_start for position in positive_positions):
        return False
    if _has_negative_deliverable_context(prefix):
        return False
    if _looks_like_tool_reference(candidate, prefix):
        return False
    if capture_following:
        return True

    positive_context_start = max(0, match_start - _DELIVERABLE_POSITIVE_CONTEXT_LIMIT)
    positive_context = line[positive_context_start:match_start]
    return bool(_positive_deliverable_positions(positive_context) or positive_positions)


def _line_can_extend_deliverable_section(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if _BULLET_PREFIX_RE.match(text):
        return True
    if _DELIVERABLE_TOKEN_RE.search(text):
        return True
    return False


def extract_explicit_deliverables_from_text(text: Any) -> List[str]:
    if not isinstance(text, str) or not text.strip():
        return []

    ordered: List[str] = []
    seen: set[str] = set()
    capture_following = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            capture_following = False
            continue

        positive_positions = _positive_deliverable_positions(line)
        section_heading = _is_deliverable_section_heading(line)
        should_scan = bool(positive_positions) or capture_following
        matched_any = False
        if should_scan:
            for match in _DELIVERABLE_TOKEN_RE.finditer(line):
                candidate = _normalize_deliverable_candidate(match.group("path"))
                if not candidate:
                    continue
                if not _candidate_has_output_intent(
                    line,
                    candidate=candidate,
                    match_start=match.start("path"),
                    positive_positions=positive_positions,
                    capture_following=capture_following,
                ):
                    continue
                key = candidate.lower()
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(candidate)
                matched_any = True

        if section_heading:
            capture_following = True
        elif capture_following:
            capture_following = matched_any or _line_can_extend_deliverable_section(line)

    return ordered


def derive_acceptance_criteria_from_text(
    text: Any,
    *,
    blocking: bool = True,
) -> Optional[Dict[str, Any]]:
    deliverables = extract_explicit_deliverables_from_text(text)
    if not deliverables:
        return None

    checks: List[Dict[str, Any]] = []
    for candidate in deliverables:
        if any(token in candidate for token in ("*", "?", "[")):
            checks.append({"type": "glob_count_at_least", "path": candidate, "count": 1})
        else:
            checks.append({"type": "file_nonempty", "path": candidate})
            checks.extend(_content_checks_for_deliverable(candidate))

    return {
        "category": "file_data",
        "blocking": bool(blocking),
        "checks": checks,
    }


def _content_checks_for_deliverable(candidate: str) -> List[Dict[str, Any]]:
    """Add format-aware checks for common data/report artifacts.

    A non-empty file is not enough for plan execution: header-only TSVs,
    placeholder PDFs, and metrics-free JSON files were previously able to pass
    generated criteria.  These checks are intentionally deterministic and hard,
    so downstream LLM arbitration cannot wave them through.
    """
    lowered = str(candidate or "").strip().lower()
    if not lowered:
        return []

    checks: List[Dict[str, Any]] = []
    if lowered.endswith((".tsv", ".csv")):
        checks.append({
            "type": "json_field_at_least",
            "path": candidate,
            "key_path": "row_count",
            "min_value": 1,
            "hard": True,
        })

    basename = lowered.rsplit("/", 1)[-1]
    if lowered.endswith(".pdf"):
        checks.append({
            "type": "pdf_valid",
            "path": candidate,
            "min_pages": 1,
            "min_text_chars": 200,
            "hard": True,
        })
    if lowered.endswith(".json") and (
        "audit" in basename or basename in {"data_audit.json", "audit_result.json"}
    ):
        checks.append({
            "type": "json_field_at_least",
            "path": candidate,
            "key_path": "metadata_rows",
            "min_value": 1,
            "hard": True,
        })
    if lowered.endswith(".json") and (
        "metadata" in basename and "summary" in basename
    ):
        checks.extend([
            {
                "type": "json_field_at_least",
                "path": candidate,
                "key_path": "rows_written",
                "min_value": 1,
                "hard": True,
            },
            {
                "type": "json_field_at_least",
                "path": candidate,
                "key_path": "labels_kept",
                "min_value": 1,
                "hard": True,
            },
        ])
    if lowered.endswith(".json") and (
        "metric" in basename or "model" in basename or "baseline" in basename
    ):
        checks.append({"type": "model_metrics_valid", "path": candidate, "hard": True})
    return checks


def resolve_glob_pattern(raw_check: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(raw_check, dict):
        return None
    for key in ("glob", "path"):
        value = raw_check.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_glob_min_count(raw_check: Optional[Dict[str, Any]], *, default: int = 0) -> int:
    if not isinstance(raw_check, dict):
        return default
    for key in ("min_count", "count"):
        value = raw_check.get(key)
        if value is None:
            continue
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return default


def derive_expected_deliverables(
    criteria: Optional[Dict[str, Any]],
    *,
    include_globs: bool = True,
    relative_only: bool = False,
) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()

    checks = criteria.get("checks") if isinstance(criteria, dict) else None
    if not isinstance(checks, list):
        return ordered

    for raw_check in checks:
        if not isinstance(raw_check, dict):
            continue
        check_type = str(raw_check.get("type") or "").strip().lower()
        candidate: Any = None
        if check_type in {
            "file_exists",
            "file_nonempty",
            "text_contains",
            "json_field_equals",
            "json_field_at_least",
        }:
            candidate = raw_check.get("path")
        elif check_type in {"glob_count_at_least", "glob_nonempty"}:
            candidate = resolve_glob_pattern(raw_check)

        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text:
            continue
        if not include_globs and any(token in text for token in ("*", "?", "[")):
            continue
        if relative_only and (
            text.startswith("/")
            or text.startswith("~/")
            or re.match(r"^[A-Za-z]:[\\/]", text)
        ):
            continue

        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)

    return ordered


def top_level_relative_output_dir(raw_path: Any) -> Optional[str]:
    if not isinstance(raw_path, str):
        return None

    text = raw_path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]

    if (
        not text
        or text.startswith("/")
        or text.startswith("../")
        or text.startswith("~/")
        or re.match(r"^[A-Za-z]:/", text)
    ):
        return None

    parts = [part for part in text.split("/") if part and part != "."]
    if len(parts) < 2:
        return None

    head = parts[0]
    if any(token in head for token in ("*", "?", "[")):
        return None
    return head


def derive_relative_output_dirs(
    criteria: Optional[Dict[str, Any]],
    *,
    default_dirs: Sequence[str] = (),
) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()

    def _append(value: Any) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(text)

    for name in default_dirs:
        _append(name)

    checks = criteria.get("checks") if isinstance(criteria, dict) else None
    if not isinstance(checks, list):
        return ordered

    for raw_check in checks:
        if not isinstance(raw_check, dict):
            continue
        check_type = str(raw_check.get("type") or "").strip().lower()
        candidate: Any = None
        if check_type in {
            "file_exists",
            "file_nonempty",
            "text_contains",
            "json_field_equals",
            "json_field_at_least",
        }:
            candidate = raw_check.get("path")
        elif check_type in {"glob_count_at_least", "glob_nonempty"}:
            candidate = resolve_glob_pattern(raw_check)
        top_level = top_level_relative_output_dir(candidate)
        if top_level:
            _append(top_level)

    return ordered
