from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence


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
