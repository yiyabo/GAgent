"""Bounded, one-confirmation Plan revision proposals.

A proposal is session metadata, not permission to mutate arbitrary Plans.  It is
scoped to the owning session and Plan, contains the exact optimize parameters,
and is consumed only after that optimize succeeds.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

PENDING_REVISION_KEY = "pending_plan_revision_proposal"
MAX_CHANGES = 32
MAX_TEXT = 4000

_NEGATED = re.compile(r"不要|不需要|无需|不能|不允许|禁止|别|do not|don't|without", re.I)
_REVISION = re.compile(
    r"并入|加入|采纳|落实|授权\s*(?:修改|修订|优化)|应用\s*(?:修订|修改)|"
    r"按.{0,12}(?:建议|方案).{0,12}(?:修改|修订|优化)|"
    r"\b(?:apply|accept|incorporate)\b.{0,40}\b(?:revision|changes?|recommendations?|plan)\b",
    re.I,
)
_APPROVAL = re.compile(
    r"^\s*(?:\d+[.、)]\s*)?(?:同意|接受|可以|好的|好|确认|批准|"
    r"同意并入|同意修改|同意修订|按提案执行|按建议修改|"
    r"yes|ok|okay|approve|approved|apply it|go ahead)[!！。\.\s]*$",
    re.I,
)


def explicit_revision_request(message: Any) -> bool:
    """Recognize authorization split across Chinese clauses without bare assent."""
    text = str(message or "").strip()
    if not text or _NEGATED.search(text):
        return False
    if re.search(r"[?？]|[吗么]\s*$", text):
        return False
    return bool(_REVISION.search(text))


def is_revision_approval(message: Any) -> bool:
    text = str(message or "").strip()
    return bool(text and not _NEGATED.search(text) and _APPROVAL.fullmatch(text))


def _read_metadata(session_id: str, owner_id: str | None) -> dict[str, Any]:
    from app.database import get_db
    from app.routers.chat.session_helpers import _load_session_metadata_dict

    with get_db() as conn:
        return dict(_load_session_metadata_dict(conn, session_id, owner_id=owner_id) or {})


def _write_metadata(session_id: str, updater: Any, owner_id: str | None) -> None:
    from app.routers.chat.session_helpers import _update_session_metadata

    _update_session_metadata(session_id, updater, owner_id=owner_id)


def stage_revision_proposal(
    *,
    session_id: str | None,
    owner_id: str | None,
    bound_plan_id: int | None,
    params: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Persist the exact proposed optimize call; never mutates the Plan itself."""
    changes = params.get("changes")
    if not session_id or bound_plan_id is None or not isinstance(changes, list) or not changes:
        return None
    requested_plan_id = params.get("plan_id")
    if requested_plan_id is not None and str(requested_plan_id) != str(bound_plan_id):
        return None
    clean_changes = [dict(item) for item in changes[:MAX_CHANGES] if isinstance(item, Mapping)]
    if not clean_changes:
        return None
    proposal_id = f"revision_{uuid4().hex}"
    try:
        plan_id = int(bound_plan_id)
    except (TypeError, ValueError):
        return None
    record = {
        "proposal_id": proposal_id,
        "plan_id": plan_id,
        "status": "pending",
        "params": {
            "operation": "optimize",
            "plan_id": plan_id,
            "changes": clean_changes,
            "reason": str(params.get("reason") or "User-approved Plan revision")[:MAX_TEXT],
        },
    }

    def updater(metadata: dict[str, Any]) -> dict[str, Any]:
        metadata[PENDING_REVISION_KEY] = record
        return metadata

    _write_metadata(session_id, updater, owner_id)
    return {
        "success": False,
        "executed": False,
        "operation": "propose",
        "error_code": "plan_revision_confirmation_required",
        "proposal_id": proposal_id,
        "plan_id": plan_id,
        "proposal": record,
        "message": "已保存一份限定当前计划的修订提案，尚未修改计划。用户回复“同意”后，下一轮可应用这份提案。",
    }


def approved_revision(
    *,
    session_id: str | None,
    owner_id: str | None,
    bound_plan_id: int | None,
    message: Any,
) -> dict[str, Any] | None:
    if not session_id or bound_plan_id is None or not is_revision_approval(message):
        return None
    pending = _read_metadata(session_id, owner_id).get(PENDING_REVISION_KEY)
    if not isinstance(pending, Mapping) or pending.get("status") != "pending":
        return None
    if pending.get("plan_id") != bound_plan_id:
        return None
    params = pending.get("params")
    if not isinstance(params, Mapping) or not isinstance(params.get("changes"), list):
        return None
    return dict(pending)


def consume_revision_proposal(
    *,
    session_id: str | None,
    owner_id: str | None,
    plan_id: Any,
) -> None:
    if not session_id:
        return

    def updater(metadata: dict[str, Any]) -> dict[str, Any]:
        pending = metadata.get(PENDING_REVISION_KEY)
        if isinstance(pending, Mapping) and str(pending.get("plan_id")) == str(plan_id):
            metadata.pop(PENDING_REVISION_KEY, None)
        return metadata

    _write_metadata(session_id, updater, owner_id)


def configure_revision_context(agent: Any, routing_decision: Any) -> bool:
    """Set the approved proposal and return this turn's Plan-edit permission."""
    from app.services.plans.edit_policy import user_requested_plan_edit

    message = routing_decision.effective_user_message
    approved = approved_revision(
        session_id=getattr(agent, "session_id", None),
        owner_id=str(agent.extra_context.get("owner_id") or "").strip() or None,
        bound_plan_id=agent.plan_session.plan_id,
        message=message,
    )
    if approved:
        agent.extra_context["approved_plan_revision_proposal"] = approved
    else:
        agent.extra_context.pop("approved_plan_revision_proposal", None)
    return bool(
        routing_decision.plan_create_required
        or routing_decision.plan_optimize_required
        or approved
        or explicit_revision_request(message)
        or user_requested_plan_edit(message)
    )


def revision_scope_denial(
    *, agent: Any, operation: str, params: Mapping[str, Any]
) -> dict[str, Any] | None:
    """A proposal approval authorizes only its exact stored Plan changes."""
    approved = agent.extra_context.get("approved_plan_revision_proposal")
    if operation != "optimize" or not isinstance(approved, Mapping):
        return None
    expected = approved.get("params")
    if not isinstance(expected, Mapping):
        return {"success": False, "error_code": "plan_revision_proposal_invalid"}
    bound_plan_id = getattr(agent.plan_session, "plan_id", None)
    actual = dict(params)
    actual.setdefault("operation", operation)
    actual.setdefault("plan_id", bound_plan_id)
    actual.setdefault("reason", str(expected.get("reason") or "User-approved Plan revision"))
    normalized_expected = dict(expected)
    normalized_expected.setdefault("operation", operation)
    normalized_expected.setdefault("plan_id", bound_plan_id)
    normalized_expected.setdefault("reason", "")
    if actual == normalized_expected:
        return None
    return {
        "success": False,
        "error_code": "plan_revision_proposal_mismatch",
        "message": "本次授权仅适用于已展示的修订提案，不能扩大或替换修改内容。",
        "proposal_id": approved.get("proposal_id"),
        "plan_id": bound_plan_id,
    }


def revision_denial(
    *, agent: Any, operation: str, denied: Mapping[str, Any], params: Mapping[str, Any]
) -> dict[str, Any]:
    if operation == "optimize" and denied.get("error_code") == "plan_edit_not_requested":
        proposed = stage_revision_proposal(
            session_id=agent.session_id,
            owner_id=str(agent.extra_context.get("owner_id") or "").strip() or None,
            bound_plan_id=agent.plan_session.plan_id,
            params=params,
        )
        if proposed:
            return proposed
    return dict(denied)


def consume_agent_revision(agent: Any, result: Mapping[str, Any], params: Mapping[str, Any]) -> None:
    if not result.get("success") or result.get("operation") != "optimize":
        return
    consume_revision_proposal(
        session_id=agent.session_id,
        owner_id=str(agent.extra_context.get("owner_id") or "").strip() or None,
        plan_id=result.get("plan_id") or params.get("plan_id") or agent.plan_session.plan_id,
    )
