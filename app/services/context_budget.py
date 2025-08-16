from typing import Any, Dict, List, Optional, Tuple
import os
import logging

# Priority order for budgeting. We keep deterministic ordering.
# Include TF-IDF retrieved items between dep:refers and sibling.
PRIORITY_ORDER = ("dep:requires", "dep:refers", "retrieved", "sibling", "manual")


_BUD_LOGGER = logging.getLogger("app.context.budget")


def _debug_on() -> bool:
    v = os.environ.get("BUDGET_DEBUG") or os.environ.get("CTX_DEBUG") or os.environ.get("CONTEXT_DEBUG")
    if not v:
        return False
    v = str(v).strip().lower()
    return v in {"1", "true", "yes", "on"}


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
    strat = (strategy or "truncate").lower()
    if strat == "sentence":
        out, meta = _truncate_sentencewise(text, limit)
    else:
        out, meta = _truncate(text, limit)
    meta["strategy"] = "sentence" if strat == "sentence" else "truncate"
    return out, meta


def _priority_key(s: Dict[str, Any]) -> Tuple[int, int]:
    kind = s.get("kind") or ""
    try:
        group = PRIORITY_ORDER.index(kind)
    except ValueError:
        # Handle dep:* variants defensively
        if isinstance(kind, str) and kind.startswith("dep:"):
            if "requires" in kind:
                group = 0
            elif "refers" in kind:
                group = 1
            else:
                group = 1
        else:
            group = 2 if kind == "sibling" else 3
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
    """
    if not isinstance(bundle, dict):
        return bundle
    if max_chars is None and per_section_max is None:
        return bundle

    sections: List[Dict[str, Any]] = list(bundle.get("sections", []))
    # Ensure stable deterministic ordering for budgeting
    sections = sorted(sections, key=_priority_key)

    if _debug_on():
        _BUD_LOGGER.debug({
            "event": "apply_budget.start",
            "task_id": bundle.get("task_id"),
            "sections": len(sections),
            "max_chars": max_chars,
            "per_section_max": per_section_max,
            "strategy": (strategy or "truncate").lower(),
        })

    remaining = int(max_chars) if max_chars is not None else None
    per_cap = int(per_section_max) if per_section_max is not None else None

    # Preserve original for accounting
    original_sections = sections

    new_sections: List[Dict[str, Any]] = []
    combined_parts: List[str] = []

    for s in sections:
        content = s.get("content") or ""
        allow = len(content)
        if per_cap is not None:
            allow = min(allow, per_cap)
        if remaining is not None:
            allow = min(allow, max(0, remaining))

        truncated, meta = _summarize(content, allow, strategy)

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
        _BUD_LOGGER.debug({
            "event": "apply_budget.done",
            "task_id": bundle.get("task_id"),
            "sections": len(new_sections),
            "total_original_chars": out["budget_info"]["total_original_chars"],
            "total_new_chars": out["budget_info"]["total_new_chars"],
        })
    return out
