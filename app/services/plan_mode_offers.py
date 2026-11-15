"""Build and suppress structured Plan-mode offers.

An offer is only a UI receipt. It never enables Plan mode, creates a Plan, or
starts execution; the user must accept it through the client.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4


def session_offer_settings(session_id: str | None) -> dict[str, Any]:
    """Read the current session's offer-related settings without model calls."""
    if not session_id:
        return {}
    from app.database import get_db
    from app.routers.chat.session_helpers import (
        _extract_session_settings,
        _load_session_metadata_dict,
    )

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id)
    return _extract_session_settings(metadata) or {}


def build_plan_mode_offer(
    *,
    reason: str,
    query: str,
    settings: Mapping[str, Any],
    bound_plan_id: int | None,
    offer_id: str | None = None,
) -> dict[str, Any]:
    """Return either a pending offer or a no-card suppression receipt."""
    suppressed = None
    if settings.get("plan_offer_dismissed") is True:
        suppressed = "dismissed"
    elif settings.get("plan_mode") is True:
        suppressed = "already_enabled"
    elif bound_plan_id is not None:
        suppressed = "already_bound"
    if suppressed:
        return {
            "success": True,
            "operation": "offer",
            "suppressed": suppressed,
            "message": "Plan-mode offer suppressed; continue with the current request.",
        }

    clean_reason = (
        str(reason or "").strip()
        or "This task has multiple research and execution stages."
    )
    clean_query = str(query or "").strip()
    offer = {
        "offer_id": str(offer_id or f"offer_{uuid4().hex}"),
        "status": "pending",
        "reason": clean_reason,
        "query": clean_query,
    }
    return {
        "success": True,
        "operation": "offer",
        "offer": offer,
        "message": (
            "This task may benefit from read-only planning. Ask the user whether to enter Plan mode "
            "and create a structured Plan. Do not execute or create the Plan until the user accepts."
        ),
    }


def plan_mode_offer_from_result(result: Any) -> dict[str, Any]:
    """Project a pending offer carried by a completed DeepThinkResult."""
    offer = getattr(result, "plan_mode_offer", None)
    if not isinstance(offer, dict):
        return {}
    offer_id = str(offer.get("offer_id") or "").strip()
    query = str(offer.get("query") or "").strip()
    if not offer_id or not query:
        return {}
    return {
        "plan_mode_offer": {
            "offer_id": offer_id,
            "status": str(offer.get("status") or "pending"),
            "reason": str(offer.get("reason") or "").strip(),
            "query": query,
        }
    }
