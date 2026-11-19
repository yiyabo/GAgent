"""Deterministic Plan-mode intent for explicit requests and typed acceptance.

An explicit request to create a plan outranks a session's earlier
"Continue directly" dismissal: the user has already stated what they want,
so asking again would be wrong. Typing the offer button's label as a message
counts as accepting the pending offer (including on a stale client that never
rendered the card), and reuses the *original* query with its attachments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.plans.edit_policy import plan_change_deferred

_OFFER_QUERY_CAP = 20000
_CONFIRMER_MAX_CHARS = 200

# Accept only a clearly affirmative, offer-shaped message; never interpret a
# sentence about something else as consent.
_CONFIRMER_RE = re.compile(
    r"^(?:"
    r"进入\s*plan\s*模式(?:并|再|然后)?\s*(?:创建|生成|制定)\s*(?:一个)?\s*(?:plan|计划)?"
    r"|进入\s*plan\s*模式"
    r"|(?:创建|生成|制定)\s*(?:一个)?\s*(?:plan|计划)"
    r"|按推荐(?:继续)?"
    r"|enter\s+plan\s+mode(?:\s+and\s+create\s+(?:a\s+)?plan)?"
    r"|create\s+(?:a\s+)?plan"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanModeAcceptance:
    plan_mode: bool
    clear_dismissed: bool
    query: str | None = None
    offer_id: str | None = None
    source: str = "none"


def _message_metadata(item: dict[str, Any]) -> dict[str, Any] | None:
    meta = item.get("metadata")
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except ValueError:
            meta = None
    return meta if isinstance(meta, dict) else None


def _trim_current_turn(
    messages: list[dict[str, Any]], current_message: str | None
) -> list[dict[str, Any]]:
    """Drop the turn the client just posted, leaving the previous exchange.

    Chat requests send the history *including* the user message being sent and
    an empty assistant placeholder for the answer. Both are newer than the
    offer we are looking for, so scanning would stop on the user message and
    never see the card.
    """
    items = list(messages)
    current = str(current_message or "").strip()
    while items:
        last = items[-1]
        role = last.get("role")
        content = str(last.get("content") or "").strip()
        meta = _message_metadata(last)
        has_offer = isinstance(meta, dict) and isinstance(meta.get("plan_mode_offer"), dict)
        if role == "assistant" and not content and not has_offer:
            items.pop()
            continue
        if role == "user" and current and content == current:
            items.pop()
            continue
        break
    return items


def _latest_pending_offer(
    messages: list[dict[str, Any]] | None,
    *,
    current_message: str | None = None,
) -> dict[str, str] | None:
    """Pending offer on an assistant message after the last user turn.

    A dismissed session must not resurrect its old offer on an unrelated
    message: the user's later "Continue directly" stands.
    """
    if not messages:
        return None
    for item in reversed(_trim_current_turn(list(messages), current_message)):
        if item.get("role") == "user":
            return None
        if item.get("role") != "assistant":
            continue
        meta = _message_metadata(item)
        if not isinstance(meta, dict):
            continue
        offer = _offer_from_metadata(meta)
        if offer is None:
            continue
        return offer
    return None


def _offer_from_metadata(meta: Any) -> dict[str, str] | None:
    """The pending offer on one stored assistant message, if it still stands."""
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except ValueError:
            meta = None
    if not isinstance(meta, dict):
        return None
    offer = meta.get("plan_mode_offer")
    if not isinstance(offer, dict):
        return None
    if str(offer.get("status") or "") != "pending":
        return None
    offer_id = str(offer.get("offer_id") or "").strip()
    query = str(offer.get("query") or "").strip()[:_OFFER_QUERY_CAP]
    if not offer_id or not query:
        return None
    return {"offer_id": offer_id, "query": query}


def _stored_row(row: Any) -> tuple[Any, Any, Any]:
    """(role, content, metadata) from a sqlite row or plain tuple."""
    try:
        return row["role"], row["content"], row["metadata"]
    except (KeyError, TypeError, IndexError):
        return row[0], row[1], row[2]


def _latest_pending_offer_from_session(session_id: str | None) -> dict[str, str] | None:
    """Server-side fallback: the client never uploads per-message metadata.

    Chat requests send ``history`` with ``metadata: null`` on every turn, so the
    offer card is invisible to the request payload. Read it where it is stored:
    only the session's *last* assistant reply may be accepted, which keeps a
    dismissed or superseded offer from being revived.
    """
    if not session_id:
        return None
    from app.database import get_db

    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT role, content, metadata FROM chat_messages "
                "WHERE session_id=? ORDER BY id DESC LIMIT 6",
                (session_id,),
            ).fetchall()
    except Exception:
        return None
    for row in rows:
        role, content, stored = _stored_row(row)
        if role != "assistant":
            continue
        offer = _offer_from_metadata(stored)
        if offer is not None:
            return offer
        if str(content or "").strip():
            # A newer real reply supersedes the card.
            return None
    return None


def resolve_plan_mode_acceptance(
    *,
    message: str,
    plan_bound: bool,
    settings: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> PlanModeAcceptance:
    """Plan mode when the user explicitly asks for a Plan or accepts an offer."""
    from app.routers.chat.request_routing import _has_explicit_plan_request

    text = str(message or "").strip()
    if plan_bound or settings.get("plan_mode") is True or not text or plan_change_deferred(text):
        return PlanModeAcceptance(plan_mode=False, clear_dismissed=False)

    pending = _latest_pending_offer(messages, current_message=text) or (
        _latest_pending_offer_from_session(session_id)
    )
    confirmer = len(text) <= _CONFIRMER_MAX_CHARS and bool(_CONFIRMER_RE.match(text))
    if pending and confirmer:
        return PlanModeAcceptance(
            plan_mode=True,
            clear_dismissed=settings.get("plan_offer_dismissed") is True,
            query=pending["query"],
            offer_id=pending["offer_id"],
            source="typed_offer_accept",
        )

    if _has_explicit_plan_request(text):
        return PlanModeAcceptance(
            plan_mode=True,
            clear_dismissed=settings.get("plan_offer_dismissed") is True,
            source="explicit_plan_request",
        )

    return PlanModeAcceptance(plan_mode=False, clear_dismissed=False)


def plan_acceptance_metadata(extra_context: dict[str, Any] | None) -> dict[str, Any]:
    """Tell the client that the server switched this session into Plan mode."""
    source = str((extra_context or {}).get("plan_acceptance_source") or "").strip()
    if not source:
        return {}
    return {"plan_mode": True, "plan_acceptance_source": source}


def persist_plan_mode_enabled(session_id: str | None, *, enabled: bool) -> None:
    """Persist the toggle (and clear the dismissal) on the owning session."""
    if not session_id:
        return
    from app.routers.chat.session_helpers import _update_session_metadata

    def _updater(metadata: dict[str, Any]) -> dict[str, Any]:
        metadata["plan_mode"] = enabled
        if enabled:
            metadata.pop("plan_offer_dismissed", None)
        return metadata

    _update_session_metadata(session_id, _updater)


__all__ = [
    "PlanModeAcceptance",
    "persist_plan_mode_enabled",
    "plan_acceptance_metadata",
    "resolve_plan_mode_acceptance",
]
