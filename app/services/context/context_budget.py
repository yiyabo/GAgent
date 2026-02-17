import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

# Priority order for budgeting. We keep deterministic ordering.
# Phase 4: add 'index' with highest priority, before dependencies.
# Phase 5: add hierarchy-based context types (ancestor, h_sibling).
# Include TF-IDF retrieved items between dep:refers and sibling.
# Pinned sections are forced to the highest priority and are not trimmed.
PRIORITY_ORDER = ("pinned", "index", "dep:requires", "dep:refers", "ancestor", "retrieved", "h_sibling", "sibling", "manual")


_BUD_LOGGER = logging.getLogger("app.context.budget")
from app.services.foundation.settings import get_settings


def _debug_on() -> bool:
    """Use centralized settings for budget debug flag."""
    try:
        s = get_settings()
        return bool(getattr(s, "budget_debug", False) or getattr(s, "ctx_debug", False))
    except Exception:
        v = os.environ.get("BUDGET_DEBUG") or os.environ.get("CTX_DEBUG") or os.environ.get("CONTEXT_DEBUG")
        return str(v).strip().lower() in {"1", "true", "yes", "on"} if v else False


def _truncate(text: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """Naive truncation summarizer.

    Returns truncated text and metadata describing the operation.
    """
    text = text or ""
    original_len = len(text)
    limit = max(0, int(limit)) if limit is not None else original_len
    if original_len <= limit:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}
    return text[:limit], {"truncated": True, "original_len": original_len, "new_len": limit}


def _detect_content_format(text: str) -> str:
    """Detect the format of the content to apply appropriate truncation strategy.
    
    Args:
        text: The text to analyze
        
    Returns:
        One of: "json", "tsv", "csv", "xml", "html", "markdown", "text"
    """
    text = text.strip() if text else ""
    if not text:
        return "text"
    
    # Check for JSON format
    if text.startswith("{") or text.startswith("["):
        try:
            json.loads(text)
            return "json"
        except json.JSONDecodeError:
            # Might be partial JSON, still try JSON-aware truncation
            if '"' in text and (":" in text or text.startswith("[") or text.startswith("{")):
                return "json"
    
    # Check for TSV format (tabs present and consistent column count)
    if "\t" in text:
        lines = text.split("\n")
        if len(lines) > 1:
            tab_counts = [line.count("\t") for line in lines[:min(10, len(lines))]]
            if tab_counts and all(c == tab_counts[0] for c in tab_counts):
                return "tsv"
    
    # Check for CSV format (commas present and consistent column count)
    if "," in text and "\t" not in text:
        lines = text.split("\n")
        if len(lines) > 1:
            comma_counts = [line.count(",") for line in lines[:min(10, len(lines))]]
            if comma_counts and all(c == comma_counts[0] for c in comma_counts):
                return "csv"
    
    # Check for XML/HTML format
    if text.startswith("<") and ">" in text:
        if text.lower().startswith("<!doctype") or text.lower().startswith("<html"):
            return "html"
        return "xml"
    
    # Check for Markdown format
    if any(text.startswith(marker) for marker in ["#", "-", "*", "```", "~~~"]):
        return "markdown"
    
    return "text"


def _truncate_json(text: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """Truncate JSON content at object/array boundaries to maintain valid structure.
    
    Strategy:
    1. If content already fits, return as-is
    2. Find the last complete object/array boundary before limit
    3. Close any open brackets/braces to maintain validity
    """
    text = text or ""
    original_len = len(text)
    limit = max(0, int(limit)) if limit is not None else original_len
    
    if original_len <= limit:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}
    
    # Find the best truncation point within the limit
    # Look for the last complete JSON value before the limit
    window = text[:limit]
    
    # Track bracket/brace depth
    depth = 0
    in_string = False
    escape_next = False
    last_valid_end = 0
    
    for i, char in enumerate(window):
        if escape_next:
            escape_next = False
            continue
        
        if char == "\\":
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if not in_string:
            if char in "{[":
                depth += 1
                last_valid_end = i + 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    last_valid_end = i + 1
            elif char in ",":
                if depth == 0:
                    last_valid_end = i + 1
    
    # If we found a valid boundary, use it
    if last_valid_end > 0:
        truncated = window[:last_valid_end].rstrip()
    else:
        # Fallback: just cut at limit and try to close brackets
        truncated = window.rstrip()
    
    # Try to close any open brackets/braces
    open_braces = truncated.count("{") - truncated.count("}")
    open_brackets = truncated.count("[") - truncated.count("]")
    
    # Close in reverse order
    closing = ""
    if open_brackets > 0:
        closing = "]" * open_brackets + closing
    if open_braces > 0:
        closing = "}" * open_braces + closing
    
    if closing:
        # Add closing characters, but respect the limit
        available = limit - len(truncated)
        if available >= len(closing):
            truncated = truncated + closing
        else:
            # Can't close properly, fallback to raw truncation
            truncated = text[:limit]
    
    return truncated, {
        "truncated": True,
        "original_len": original_len,
        "new_len": len(truncated),
        "format": "json",
        "closures_added": len(closing) if closing else 0,
    }


def _truncate_tsv(text: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """Truncate TSV content at row boundaries to maintain valid structure.
    
    Strategy:
    1. Always include the header row
    2. Add complete rows until the limit is reached
    3. Never cut a row in the middle
    """
    text = text or ""
    original_len = len(text)
    limit = max(0, int(limit)) if limit is not None else original_len
    
    if original_len <= limit:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}
    
    lines = text.split("\n")
    if not lines:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}

    if limit <= 0:
        return "", {
            "truncated": True,
            "original_len": original_len,
            "new_len": 0,
            "format": "tsv",
            "rows_kept": 0,
            "rows_dropped": len(lines),
        }

    # Always include header
    header = lines[0]
    if len(header) > limit:
        truncated_header, meta = _truncate(header, limit)
        return truncated_header, {
            "truncated": True,
            "original_len": original_len,
            "new_len": len(truncated_header),
            "format": "tsv",
            "rows_kept": 1 if truncated_header else 0,
            "rows_dropped": max(len(lines) - (1 if truncated_header else 0), 0),
            "header_truncated": bool(meta.get("truncated")),
        }

    result_lines = [header]
    current_len = len(header)

    # Add complete rows while under limit
    for line in lines[1:]:
        line_len = len(line) + 1
        if current_len + line_len <= limit:
            result_lines.append(line)
            current_len += line_len
        else:
            break
    
    truncated = "\n".join(result_lines)
    rows_kept = len(result_lines)
    rows_dropped = len(lines) - rows_kept
    
    return truncated, {
        "truncated": rows_dropped > 0,
        "original_len": original_len,
        "new_len": len(truncated),
        "format": "tsv",
        "rows_kept": rows_kept,
        "rows_dropped": rows_dropped,
    }


def _truncate_csv(text: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """Truncate CSV content at row boundaries to maintain valid structure.
    
    Similar to TSV but handles quoted fields with embedded newlines.
    """
    text = text or ""
    original_len = len(text)
    limit = max(0, int(limit)) if limit is not None else original_len
    
    if original_len <= limit:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}
    
    # Simple approach: keep whole lines until the budget is exhausted.
    lines = text.split("\n")
    result_lines = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1

        if current_len + line_len > limit:
            break

        result_lines.append(line)
        current_len += line_len

    truncated = "\n".join(result_lines)
    rows_kept = len(result_lines)
    rows_dropped = len(lines) - rows_kept
    
    return truncated, {
        "truncated": rows_dropped > 0,
        "original_len": original_len,
        "new_len": len(truncated),
        "format": "csv",
        "rows_kept": rows_kept,
        "rows_dropped": rows_dropped,
    }


def _truncate_format_aware(text: str, limit: int, strategy: str = "truncate") -> Tuple[str, Dict[str, Any]]:
    """Truncate text while respecting its format boundaries.
    
    Args:
        text: The text to truncate
        limit: Maximum length
        strategy: Fallback strategy ("truncate" or "sentence")
        
    Returns:
        Tuple of (truncated_text, metadata)
    """
    text = text or ""
    original_len = len(text)
    limit = max(0, int(limit)) if limit is not None else original_len
    
    if original_len <= limit:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}
    
    # Detect format and apply appropriate truncation
    fmt = _detect_content_format(text)
    
    if fmt == "json":
        return _truncate_json(text, limit)
    elif fmt == "tsv":
        return _truncate_tsv(text, limit)
    elif fmt == "csv":
        return _truncate_csv(text, limit)
    elif strategy == "sentence":
        return _truncate_sentencewise(text, limit)
    else:
        return _truncate(text, limit)


def _truncate_sentencewise(text: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    """Rule-based summarizer that prefers sentence boundaries within the limit.

    - If a sentence boundary is found within limit, cut there; otherwise fallback to raw truncation.
    - Recognizes common sentence delimiters in English and Chinese.
    """
    text = text or ""
    original_len = len(text)
    limit = max(0, int(limit)) if limit is not None else original_len
    if original_len <= limit:
        return text, {"truncated": False, "original_len": original_len, "new_len": original_len}

    # Find last boundary within limit
    boundaries = ".!?。！？\n"
    cut = -1
    # Search up to limit (exclusive)
    window = text[:limit]
    for i in range(len(window) - 1, -1, -1):
        if window[i] in boundaries:
            cut = i + 1  # include the delimiter
            break
    if cut <= 0:
        # Fallback to raw truncation
        return _truncate(text, limit)
    new_text = text[:cut].rstrip()
    return new_text, {"truncated": True, "original_len": original_len, "new_len": len(new_text)}


def _summarize(text: str, limit: int, strategy: str) -> Tuple[str, Dict[str, Any]]:
    """Summarize/truncate text using format-aware truncation.
    
    Args:
        text: The text to summarize
        limit: Maximum length
        strategy: Truncation strategy
            - "truncate": Raw character truncation
            - "sentence": Prefer sentence boundaries
            - "format_aware": Auto-detect format and truncate appropriately (default)
    
    Returns:
        Tuple of (truncated_text, metadata)
    """
    strat = (strategy or "format_aware").lower()
    
    # Use format-aware truncation by default or when explicitly requested
    if strat in ("format_aware", "auto", "smart"):
        out, meta = _truncate_format_aware(text, limit, strategy="sentence")
        meta["strategy"] = "format_aware"
    elif strat == "sentence":
        out, meta = _truncate_sentencewise(text, limit)
        meta["strategy"] = "sentence"
    else:
        # For "truncate" or unknown strategies, use format-aware as fallback
        out, meta = _truncate_format_aware(text, limit, strategy="truncate")
        meta["strategy"] = "truncate"
    
    # Add format detection info to metadata
    if "format" not in meta:
        meta["format"] = _detect_content_format(text)
    
    # Log truncation details when debug is enabled
    if _debug_on() and meta.get("truncated"):
        _BUD_LOGGER.debug(
            {
                "event": "summarize.truncated",
                "strategy": meta.get("strategy"),
                "format": meta.get("format"),
                "original_len": meta.get("original_len"),
                "new_len": meta.get("new_len"),
                "reduction_ratio": round(1 - meta.get("new_len", 0) / max(meta.get("original_len", 1), 1), 2),
            }
        )
    
    return out, meta


def _priority_key(s: Dict[str, Any]) -> Tuple[int, int]:
    kind = s.get("kind") or ""
    try:
        group = PRIORITY_ORDER.index(kind)
    except ValueError:
        # Handle pinned:* with the highest priority
        if isinstance(kind, str) and kind.startswith("pinned"):
            group = 0
        # Handle dep:* variants and hierarchy types defensively
        elif isinstance(kind, str) and kind.startswith("dep:"):
            if "requires" in kind:
                group = 1  # dep:requires
            elif "refers" in kind:
                group = 2  # dep:refers
            else:
                group = 2
        elif kind == "ancestor":
            group = 3  # ancestor
        elif kind == "h_sibling":
            group = 5  # h_sibling
        elif kind == "sibling":
            group = 6  # sibling
        else:
            group = 7  # manual and others
    return (group, int(s.get("task_id") or 0))


def apply_budget(
    bundle: Dict[str, Any],
    max_chars: Optional[int] = None,
    per_section_max: Optional[int] = None,
    strategy: str = "truncate",
) -> Dict[str, Any]:
    """Apply a simple character budget to a context bundle.

    - max_chars: total budget across all sections (applies to section contents only)
    - per_section_max: cap for each section's content
    - strategy: summarization strategy for trimming content within caps
        * "truncate" (default): raw character truncation
        * "sentence": prefer sentence boundaries when cutting

    If neither is provided, the bundle is returned unchanged.

    Each section will include a `budget` metadata object with at least:
    - original_len, new_len, truncated, strategy (from summarizer)
    - allowed: effective cap applied to the section this round
    - allowed_by_total: cap imposed by remaining total budget (if any)
    - allowed_by_per_section: cap imposed by per-section limit (if any)
    - truncated_reason: one of {"none", "per_section", "total", "both"}
    - group, index: ordering metadata; group maps to PRIORITY_ORDER
    """
    if not isinstance(bundle, dict):
        return bundle
    if max_chars is None and per_section_max is None:
        return bundle

    sections: List[Dict[str, Any]] = list(bundle.get("sections", []))
    # Ensure stable deterministic ordering for budgeting
    sections = sorted(sections, key=_priority_key)

    if _debug_on():
        _BUD_LOGGER.debug(
            {
                "event": "apply_budget.start",
                "task_id": bundle.get("task_id"),
                "sections": len(sections),
                "max_chars": max_chars,
                "per_section_max": per_section_max,
                "strategy": (strategy or "truncate").lower(),
            }
        )

    remaining = int(max_chars) if max_chars is not None else None
    per_cap = int(per_section_max) if per_section_max is not None else None

    # Preserve original for accounting
    original_sections = sections

    new_sections: List[Dict[str, Any]] = []
    combined_parts: List[str] = []

    for idx, s in enumerate(sections):
        content = s.get("content") or ""
        original_len = len(content)
        kind = s.get("kind") or ""

        # Handle pinned sections: never trim and do not consume total budget
        is_pinned = bool(s.get("pinned") or (isinstance(kind, str) and kind.startswith("pinned")))
        if is_pinned:
            # Determine ordering group for metadata (mirrors _priority_key)
            try:
                group = PRIORITY_ORDER.index("pinned")
            except ValueError:
                group = 0

            meta = {
                "truncated": False,
                "original_len": original_len,
                "new_len": original_len,
                "strategy": "none",
                "allowed": original_len,
                "allowed_by_per_section": original_len,
                "allowed_by_total": original_len,
                "truncated_reason": "none",
                "group": group,
                "index": idx,
                "pinned": True,
            }

            s2 = dict(s)
            s2["content"] = content
            s2["budget"] = meta
            new_sections.append(s2)

            header = s2.get("short_name") or s2.get("name") or f"Task {s2.get('task_id')}"
            combined_parts.append(f"## {header}\n\n{content}")
            # Do NOT decrement remaining for pinned sections
            continue

        # Calculate effective allowances before summarization
        allowed_by_per = min(original_len, per_cap) if per_cap is not None else original_len
        allowed_by_total = min(original_len, max(0, remaining)) if remaining is not None else original_len
        allow = min(allowed_by_per, allowed_by_total)

        truncated, meta = _summarize(content, allow, strategy)

        # Determine ordering group for metadata (mirrors _priority_key)
        try:
            group = PRIORITY_ORDER.index(kind)
        except ValueError:
            if isinstance(kind, str) and kind.startswith("dep:"):
                if "requires" in kind:
                    group = 1  # dep:requires
                elif "refers" in kind:
                    group = 2  # dep:refers
                else:
                    group = 2
            elif kind == "ancestor":
                group = 3  # ancestor
            elif kind == "h_sibling":
                group = 5  # h_sibling
            elif kind == "sibling":
                group = 6  # sibling
            else:
                group = 7  # manual and others

        # Classify truncation reason
        per_applied = (per_cap is not None) and (allowed_by_per < original_len)
        tot_applied = (remaining is not None) and (allowed_by_total < original_len)
        if not meta.get("truncated"):
            reason = "none"
        elif per_applied and tot_applied:
            reason = "both"
        elif per_applied:
            reason = "per_section"
        elif tot_applied:
            reason = "total"
        else:
            reason = "none"

        meta.update(
            {
                "allowed": allow,
                "allowed_by_per_section": allowed_by_per,
                "allowed_by_total": allowed_by_total,
                "truncated_reason": reason,
                "group": group,
                "index": idx,
            }
        )

        s2 = dict(s)
        s2["content"] = truncated
        s2["budget"] = meta
        new_sections.append(s2)

        header = s2.get("short_name") or s2.get("name") or f"Task {s2.get('task_id')}"
        combined_parts.append(f"## {header}\n\n{truncated}")

        if remaining is not None:
            remaining -= len(truncated)
            if remaining <= 0:
                remaining = 0

    combined = "\n\n".join(combined_parts)

    out = dict(bundle)
    out["sections"] = new_sections
    out["combined"] = combined
    out["budget_info"] = {
        "max_chars": max_chars,
        "per_section_max": per_section_max,
        "strategy": (strategy or "truncate").lower(),
        "total_original_chars": sum(len(s.get("content") or "") for s in original_sections),
        "total_new_chars": sum(len(s.get("content") or "") for s in new_sections),
    }

    if _debug_on():
        _BUD_LOGGER.debug(
            {
                "event": "apply_budget.done",
                "task_id": bundle.get("task_id"),
                "sections": len(new_sections),
                "total_original_chars": out["budget_info"]["total_original_chars"],
                "total_new_chars": out["budget_info"]["total_new_chars"],
            }
        )
    return out
