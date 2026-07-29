"""Resolve user references like 方向3 against prior assistant-numbered lists."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_DIR_HEADING_RE = re.compile(
    r"(?m)^(?:#{1,6}\s*)?(?:\*\*)?(?:研究方向|方向)\s*"
    r"([0-9]{1,2}|[一二三四五六七八九十]+)\s*"
    r"[：:\.、\.\)\]】]?\s*(.+?)\s*(?:\*\*)?\s*$"
)
_DIR_TABLE_RE = re.compile(
    r"\|\s*\d+\s*\|\s*方向\s*([0-9]{1,2})\s*[（(]([^|）)]+)[）)]"
)
_USER_REF_RE = re.compile(
    r"(?:选择|选|要|做|针对|关于|探讨|深入|按照|根据)?"
    r"\s*(?:的)?"
    r"\s*(?:研究方向|方向)\s*([0-9]{1,2}|[一二三四五六七八九十]+)"
)

_CN_NUM = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


def _norm_num(raw: str) -> Optional[str]:
    s = str(raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        return str(int(s))
    return _CN_NUM.get(s)


def _clean_title(title: str) -> str:
    t = str(title or "").strip()
    t = re.sub(r"^[*#`\s]+|[*#`\s]+$", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    # drop trailing markdown table junk
    t = t.split("|")[0].strip()
    return t


def extract_direction_map(text: str) -> Dict[str, str]:
    """Parse 研究方向N / 方向N titles from an assistant message."""
    out: Dict[str, str] = {}
    if not text:
        return out
    body = str(text)

    for m in _DIR_HEADING_RE.finditer(body):
        num = _norm_num(m.group(1))
        title = _clean_title(m.group(2))
        if not num or len(title) < 2:
            continue
        # Prefer longer / more specific titles
        prev = out.get(num, "")
        if not prev or len(title) >= len(prev):
            out[num] = title

    for m in _DIR_TABLE_RE.finditer(body):
        num = _norm_num(m.group(1))
        title = _clean_title(m.group(2))
        if not num or len(title) < 2:
            continue
        prev = out.get(num, "")
        # Table cells are short aliases — keep existing long heading if present
        if not prev:
            out[num] = title
        elif len(prev) < 12 and len(title) > len(prev):
            out[num] = title

    return out


def build_direction_map_from_history(history: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build 方向N → title from assistant history.
    Prefer more recent messages; within a message prefer longer titles.
    """
    merged: Dict[str, str] = {}
    if not history:
        return merged

    # Oldest → newest so newer overrides
    for msg in history:
        role = str(msg.get("role") or "").lower()
        if role not in {"assistant", "ai", "model"}:
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        chunk = extract_direction_map(str(content or ""))
        if not chunk:
            continue
        for num, title in chunk.items():
            prev = merged.get(num, "")
            if not prev:
                merged[num] = title
            elif len(title) > len(prev) + 4:
                # Newer longer definition wins
                merged[num] = title
            else:
                # Newer still wins for same number (conversation may renumber)
                # but keep longer if newer is a short alias of the same topic
                if title in prev or prev in title:
                    merged[num] = prev if len(prev) >= len(title) else title
                else:
                    merged[num] = title
    return merged


def user_referenced_direction_ids(user_message: str) -> List[str]:
    refs: List[str] = []
    for m in _USER_REF_RE.finditer(str(user_message or "")):
        num = _norm_num(m.group(1))
        if num and num not in refs:
            refs.append(num)
    return refs


def maybe_inject_direction_resolution(
    user_message: str,
    history: List[Dict[str, Any]],
) -> str:
    """
    When the user says 方向N / 研究方向N, inject a grounded title map from history
    so the model cannot swap in an unrelated topic that reuses the number.
    """
    text = str(user_message or "")
    refs = user_referenced_direction_ids(text)
    if not refs:
        return text

    dmap = build_direction_map_from_history(history or [])
    lines: List[str] = [
        "",
        "",
        "=== GROUNDED NUMBERED REFERENCES (CRITICAL — DO NOT SUBSTITUTE) ===",
        "The user referred to numbered direction(s). Resolve them ONLY from your prior assistant messages in this conversation.",
    ]

    if not dmap:
        lines.extend(
            [
                "No prior 方向/研究方向 numbered list was found in history.",
                "You MUST ask the user to restate the full title. Do NOT invent a new topic for that number.",
            ]
        )
        return text + "\n".join(lines)

    lines.append("Resolved ids for this turn (use these exact titles):")
    for num in refs:
        if num in dmap:
            lines.append(f"- 方向{num} / 研究方向{num} = 「{dmap[num]}」")
        else:
            lines.append(
                f"- 方向{num}: NOT FOUND in prior list — ask the user to clarify; do NOT invent a topic."
            )

    lines.append("Full direction id → title map recovered from history:")
    for num in sorted(dmap.keys(), key=lambda x: int(x) if str(x).isdigit() else 99):
        mark = " ← USER SELECTED" if num in refs else ""
        lines.append(f"  · 方向{num}: {dmap[num]}{mark}")

    lines.extend(
        [
            "Rules:",
            "1) Start the reply by restating the resolved title, e.g. 「按你的选择，方向3 = …」.",
            "2) Do NOT replace it with a different research topic that merely reuses the same number.",
            "3) Priority-rank tables refer to the same 方向N ids as the full list "
            "(方向3 means that id, NOT 'the third row of the priority table').",
            "4) If what you planned to write conflicts with the resolved title, the resolved title WINS.",
            "5) Only after locking the title may you design a detailed research protocol.",
        ]
    )
    return text + "\n".join(lines)
