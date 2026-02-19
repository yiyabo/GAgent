"""Confirmation mechanism helpers for dangerous action workflows."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Dangerous actions that require explicit user confirmation.
ACTIONS_REQUIRING_CONFIRMATION = {
    ("plan_operation", "delete_plan"),      # Delete plan.
    ("task_operation", "delete_task"),      # Delete task.
    ("task_operation", "clear_tasks"),      # Clear tasks.
}

# Pending confirmation store: {confirmation_id: {session_id, actions, structured, created_at, ...}}
_pending_confirmations: Dict[str, Dict[str, Any]] = {}

def _generate_confirmation_id() -> str:
    """Generate a confirmation ID."""
    return f"confirm_{uuid4().hex[:12]}"

def _requires_confirmation(actions: List[Any]) -> bool:
    """Check whether action list contains a confirmation-required action."""
    for action in actions:
        key = (getattr(action, 'kind', None), getattr(action, 'name', None))
        if key in ACTIONS_REQUIRING_CONFIRMATION:
            return True
    return False

def _store_pending_confirmation(
    confirmation_id: str,
    session_id: str,
    actions: List[Any],
    structured: Any,
    plan_id: Optional[int] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Store a pending action awaiting confirmation."""
    _pending_confirmations[confirmation_id] = {
        "session_id": session_id,
        "actions": actions,
        "structured": structured,
        "plan_id": plan_id,
        "extra_context": extra_context or {},
        "created_at": datetime.now().isoformat(),
    }
    logger.info(f"[CONFIRMATION] Stored pending confirmation: {confirmation_id} for session {session_id}")

def _get_pending_confirmation(confirmation_id: str) -> Optional[Dict[str, Any]]:
    """Get pending confirmation payload by confirmation ID."""
    return _pending_confirmations.get(confirmation_id)

def _remove_pending_confirmation(confirmation_id: str) -> Optional[Dict[str, Any]]:
    """Remove and return pending confirmation payload."""
    return _pending_confirmations.pop(confirmation_id, None)

def _cleanup_old_confirmations(max_age_seconds: int = 600) -> None:
    """Clean expired pending confirmations (default: 10 minutes)."""
    now = datetime.now()
    expired = []
    for cid, data in _pending_confirmations.items():
        created = datetime.fromisoformat(data["created_at"])
        if (now - created).total_seconds() > max_age_seconds:
            expired.append(cid)
    for cid in expired:
        del _pending_confirmations[cid]
        logger.info(f"[CONFIRMATION] Cleaned up expired confirmation: {cid}")
