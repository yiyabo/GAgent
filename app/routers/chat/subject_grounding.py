"""Subject seeding and grounded-answer cluster of ``agent`` (W5a cluster ⑨).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8.  The plan's ⑨ was
``action_loop.py``; no module-level action-loop cluster exists (the action loop
is class-body code, ``StructuredChatAgent.process_unified_stream`` territory for
W5c), so ⑨ is realised as this module instead — the two self-contained
module-level helpers that carry subject identity and evidence grounding across
turns: `_seed_active_subject_from_routing` (turn bookkeeping for the active
subject) and `_apply_grounded_local_answer` (failure-caveat append).  ``agent.py``
re-exports both, so the class call sites and
app/tests/tools/test_execution_semantics_regressions.py's direct import of
``_apply_grounded_local_answer`` are unchanged.

Patch surface: **zero body deviations**.  Neither name is patched in ``app/`` or
``app/tests/``; the facade aliases this cluster read (`canonicalize_subject_ref`,
`build_subject_aliases`, `subject_identity_matches` from ``subject_identity``) are
imported here directly from that source module, and
``_current_user_turn_index_from_history`` is imported from its new home
``continuation_hints.py`` — so every call expression stays verbatim.

No logger is used in this cluster; both user-visible strings
("⚠️ 本次操作未被验证成功：...") and every metadata key/status literal are
byte-identical.
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
from app.services.deep_think.text_utils import _answer_discloses_limitation


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


_NO_OUTPUT_PATH_RE = re.compile(r"/[^\s,，;；]+")


def _failure_is_scratch_only(message: str) -> bool:
    """NO_OUTPUT contract failures whose referenced paths all live in scratch
    areas (raw_files/, /tmp/, /scratch/). Scratch intermediates are not
    deliverables and must never brand the user-facing answer (2026-10-08 geo
    case: a timed-out code cell's guessed contract files under raw_files/
    branded an otherwise successful, self-disclosed answer)."""
    text = str(message or "")
    if "NO_OUTPUT" not in text:
        return False
    paths = _NO_OUTPUT_PATH_RE.findall(text)
    if not paths:
        return False
    return all(
        ("raw_files/" in path) or path.startswith("/tmp/") or ("/scratch/" in path)
        for path in paths
    )


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
    """Lightweight evidence-based grounding for local tool results.

    Phase 2 removed the full intent-type-driven grounding. This version only
    appends a caveat when there is concrete failure evidence that contradicts
    the LLM's answer, regardless of intent_type.

    Caveat grading (2026-10-09): scratch-only NO_OUTPUT contract failures
    (missing files under raw_files//tmp/scratch — intermediates, not
    deliverables) never brand the answer, and neither does a failure the
    answer already disclosed in its own words (see
    text_utils._answer_discloses_limitation).
    """
    text = str(answer or "").strip()
    if not text:
        return text

    extra = getattr(agent, "extra_context", {}) or {}
    failure_state = extra.get("last_failure_state")
    evidence_state = extra.get("last_evidence_state")

    # If the last evidence shows verified success, trust the answer
    if isinstance(evidence_state, dict):
        if str(evidence_state.get("status") or "").strip().lower() == "verified":
            verified_facts = evidence_state.get("verified_facts")
            if isinstance(verified_facts, list) and verified_facts:
                return text

    already_disclosed = _answer_discloses_limitation(text)

    if isinstance(failure_state, dict) and str(failure_state.get("error_message") or "").strip():
        raw_message = str(failure_state.get("error_message") or "")
        if not _failure_is_scratch_only(raw_message) and not already_disclosed:
            message = _humanize_failure_message(raw_message)
            if message and message.lower() not in text.lower():
                return f"{text}\n\n⚠️ 本次操作未被验证成功：{message}"

    if isinstance(evidence_state, dict) and str(evidence_state.get("status") or "").strip().lower() == "failed":
        unresolved = evidence_state.get("unresolved")
        if isinstance(unresolved, list) and not already_disclosed:
            details = "；".join(str(item).strip() for item in unresolved if str(item).strip())
            details = _humanize_failure_message(details)
            if details and details.lower() not in text.lower():
                return f"{text}\n\n⚠️ 本次操作未被验证成功：{details}"

    return text
