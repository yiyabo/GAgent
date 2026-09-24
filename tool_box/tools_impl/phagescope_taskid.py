"""PhageScope taskid normalization and remote alias resolution helpers."""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PHAGESCOPE_TASKID_RE = re.compile(r"(?<![A-Za-z0-9])(\d{4,})(?![A-Za-z0-9])")
_PHAGESCOPE_TRACKING_JOB_RE = re.compile(r"^act_[A-Za-z0-9]+$")
_PHAGESCOPE_TASKID_HINT_RE = re.compile(
    r"(?:remote[_\s-]?task[_\s-]?id|task[_\s-]?id|task)\s*[:=]?\s*['\"]?(\d{4,})",
    flags=re.IGNORECASE,
)


def _normalize_phagescope_taskid(value: Any) -> Optional[str]:
    # bool is a subclass of int in Python; never treat True/False as a task id.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return text
    match = _PHAGESCOPE_TASKID_RE.search(text)
    if match:
        return match.group(1)
    return None


def _lookup_remote_taskid_by_tracking_job(
    job_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    token = str(job_id or "").strip()
    if not token:
        return None
    try:
        from app.database import get_db  # lazy import to avoid tool bootstrap cycles

        with get_db() as conn:
            row = None
            if session_id:
                row = conn.execute(
                    """
                    SELECT remote_taskid
                    FROM phagescope_tracking
                    WHERE job_id=? AND session_id=?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (token, session_id),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT remote_taskid
                    FROM phagescope_tracking
                    WHERE job_id=?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (token,),
                ).fetchone()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve PhageScope tracking alias %s: %s", token, exc)
        return None

    if not row:
        return None
    return _normalize_phagescope_taskid(row["remote_taskid"])


def _extract_taskid_from_payload(value: Any) -> Optional[str]:
    if value is None:
        return None

    normalized = _normalize_phagescope_taskid(value)
    if normalized:
        return normalized

    if isinstance(value, dict):
        for key in ("taskid", "task_id", "remote_taskid", "remote_task_id"):
            candidate = value.get(key)
            normalized = _normalize_phagescope_taskid(candidate)
            if normalized:
                return normalized
        for nested in value.values():
            nested_taskid = _extract_taskid_from_payload(nested)
            if nested_taskid:
                return nested_taskid
        return None

    if isinstance(value, list):
        for item in value:
            nested_taskid = _extract_taskid_from_payload(item)
            if nested_taskid:
                return nested_taskid
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        hint_match = _PHAGESCOPE_TASKID_HINT_RE.search(text)
        if hint_match:
            return hint_match.group(1)
    return None


def _lookup_remote_taskid_by_action_run(
    run_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    token = str(run_id or "").strip()
    if not token:
        return None
    try:
        from app.database import get_db  # lazy import to avoid tool bootstrap cycles

        with get_db() as conn:
            row = None
            if session_id:
                row = conn.execute(
                    """
                    SELECT id, session_id, user_message, context_json, structured_json, result_json
                    FROM chat_action_runs
                    WHERE id=? AND session_id=?
                    LIMIT 1
                    """,
                    (token, session_id),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT id, session_id, user_message, context_json, structured_json, result_json
                    FROM chat_action_runs
                    WHERE id=?
                    LIMIT 1
                    """,
                    (token,),
                ).fetchone()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve action run alias %s: %s", token, exc)
        return None

    if row is None:
        return None

    for field in ("result_json", "context_json", "structured_json"):
        raw = row[field]
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        taskid = _extract_taskid_from_payload(parsed)
        if taskid:
            return taskid

    user_message = row["user_message"]
    if isinstance(user_message, str) and user_message.strip():
        taskid = _extract_taskid_from_payload(user_message)
        if taskid:
            return taskid
    return None


def _resolve_phagescope_taskid(
    taskid: Any,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    from . import phagescope as facade

    normalized = _normalize_phagescope_taskid(taskid)
    if normalized:
        return normalized

    task_text = str(taskid or "").strip()
    if not task_text:
        return None
    if not _PHAGESCOPE_TRACKING_JOB_RE.fullmatch(task_text):
        return None

    resolved = facade._lookup_remote_taskid_by_tracking_job(task_text, session_id=session_id)
    if resolved:
        return resolved
    return facade._lookup_remote_taskid_by_action_run(task_text, session_id=session_id)


__all__ = [
    "_PHAGESCOPE_TASKID_RE", "_PHAGESCOPE_TRACKING_JOB_RE", "_PHAGESCOPE_TASKID_HINT_RE",
    "_normalize_phagescope_taskid", "_lookup_remote_taskid_by_tracking_job",
    "_extract_taskid_from_payload", "_lookup_remote_taskid_by_action_run",
    "_resolve_phagescope_taskid",
]
