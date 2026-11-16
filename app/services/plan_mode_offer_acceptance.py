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
        offer = meta.get("plan_mode_offer")
        if not isinstance(offer, dict):
            continue
        if str(offer.get("status") or "") != "pending":
            return None
        offer_id = str(offer.get("offer_id") or "").strip()
        query = str(offer.get("query") or "").strip()[:_OFFER_QUERY_CAP]
        if not offer_id or not query:
            return None
        return {"offer_id": offer_id, "query": query}
    return None


def resolve_plan_mode_acceptance(
    *,
    message: str,
    plan_bound: bool,
    settings: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
) -> PlanModeAcceptance:
    """Plan mode when the user explicitly asks for a Plan or accepts an offer."""
    from app.routers.chat.request_routing import _has_explicit_plan_request

    text = str(message or "").strip()
    if plan_bound or settings.get("plan_mode") is True or not text:
        return PlanModeAcceptance(plan_mode=False, clear_dismissed=False)

    pending = _latest_pending_offer(messages, current_message=text)
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
