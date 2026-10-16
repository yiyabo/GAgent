"""Subject seeding and legacy answer-hook compatibility.

Real errors remain in tool receipts and run history. Final prose is produced by
this turn's model loop; historical last-error snapshots never append a footer.
The facade names remain available to existing imports and monkeypatches.
"""

from __future__ import annotations

import re
from typing import Any

from .continuation_hints import _current_user_turn_index_from_history
from .request_routing import RequestRoutingDecision
from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    subject_identity_matches,
)


_TRACEBACK_MARKER = "Traceback (most recent call last)"
_EXCEPTION_LINE_RE = re.compile(
    r"^([A-Za-z_][\w.]*?(?:Error|Exception|Warning|Exit|Interrupt|Timeout|Stop\w+))\s*(?::\s*(.*))?$"
)
_EXCEPTION_MENTION_RE = re.compile(r"([A-Za-z_][\w.]*?(?:Error|Exception|Stop\w+))\b")
_CELL_FRAME_RE = re.compile(r'File "<cell>", line (\d+)')


def _humanize_failure_message(message: str, *, limit: int = 240) -> str:
    """Compress a raw traceback wall into one readable line for the user-facing
    caveat (full detail stays in logs and tool results).

    Takes the final ``XxxError: message`` line when present; otherwise the last
    exception-type mention (upstream may truncate the wall before the exception
    line); otherwise the last non-empty line. Appends the offending code-mode
    cell line number when visible. Non-traceback text passes through, capped.
    """
    text = str(message or "").strip()
    if not text:
        return text
    if _TRACEBACK_MARKER not in text:
        return text if len(text) <= limit else text[: limit - 1] + "…"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    summary = ""
    for ln in reversed(lines):
        match = _EXCEPTION_LINE_RE.match(ln)
        if match:
            exc_type = match.group(1).rsplit(".", 1)[-1]
            detail = (match.group(2) or "").strip()
            summary = f"{exc_type}: {detail}" if detail else exc_type
            break
    if not summary:
        mentions = _EXCEPTION_MENTION_RE.findall(text + " ")
        if mentions:
            summary = mentions[-1].rsplit(".", 1)[-1]
        elif lines:
            summary = lines[-1]
    cell_frames = _CELL_FRAME_RE.findall(text)
    if cell_frames:
        # Traceback frames run outermost → innermost; the last <cell> frame is
        # the user's failing line.
        summary = f"{summary}（cell 第 {cell_frames[-1]} 行）"
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"


def _seed_active_subject_from_routing(
    agent: Any,
    routing_decision: RequestRoutingDecision,
) -> None:
    subject = (
        dict(routing_decision.subject_resolution)
        if isinstance(routing_decision.subject_resolution, dict)
        else {}
    )
    kind = str(subject.get("kind") or "none").strip().lower()
    canonical_ref = canonicalize_subject_ref(
        subject.get("canonical_ref") or subject.get("display_ref")
    )
    if kind == "none" or not canonical_ref:
        return
    display_ref = str(subject.get("display_ref") or canonical_ref).strip() or canonical_ref
    aliases = build_subject_aliases(subject.get("aliases"), canonical_ref, display_ref)

    current_turn = int(
        (getattr(agent, "extra_context", {}) or {}).get("current_user_turn_index")
        or _current_user_turn_index_from_history(getattr(agent, "history", None))
    )
    existing = (
        dict((getattr(agent, "extra_context", {}) or {}).get("active_subject") or {})
        if isinstance((getattr(agent, "extra_context", {}) or {}).get("active_subject"), dict)
        else {}
    )
    same_subject = subject_identity_matches(
        existing,
        candidate_ref=canonical_ref,
        candidate_display_ref=display_ref,
        candidate_aliases=aliases,
    )
    verification_state = (
        str(existing.get("verification_state") or "").strip() if same_subject else "unresolved"
    ) or "unresolved"
    active_subject = {
        "kind": kind,
        "canonical_ref": canonical_ref,
        "display_ref": display_ref,
        "aliases": aliases,
        "verification_state": verification_state,
        "salience": 5,
        "last_tool_scope": existing.get("last_tool_scope") if same_subject else None,
        "created_turn": existing.get("created_turn") if same_subject else current_turn,
        "last_referenced_turn": current_turn,
        "last_verified_turn": existing.get("last_verified_turn") if same_subject else None,
    }
    agent.extra_context["active_subject"] = active_subject


def _apply_grounded_local_answer(
    agent: Any,
    answer: str,
    routing_decision: RequestRoutingDecision,
) -> str:
    """Compatibility hook: final prose belongs to the current model turn.

    Tool failures remain in tool results, run events and model context. A
    persisted last-error snapshot cannot establish whether the current goal
    failed: recovery can succeed at a corrected path, or in a later turn.
    Do not append historical error banners after the model has answered.
    """
    return str(answer or "").strip()
