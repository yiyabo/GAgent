from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .models import ChatMessage, ChatResponse
from app.services.llm.structured_response import LLMAction
from app.services.llm.llm_service import LLMService, get_llm_service, get_llm_service_for_provider
from app.llm import LLMClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SEARCH_PROVIDERS = {"builtin", "perplexity", "tavily"}
VALID_BASE_MODELS = {
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3-max-2026-01-23",
    "qwen-turbo",
}
VALID_LLM_PROVIDERS = {"qwen"}
SESSION_RUNTIME_CONTEXT_KEYS = (
    "active_subject",
    "last_failure_state",
    "last_evidence_state",
    "last_subject_action_class",
    "recent_image_artifacts",
)
_PHAGESCOPE_TASKID_RE = re.compile(r"(?<![A-Za-z0-9])(\d{4,})(?![A-Za-z0-9])")
_PHAGESCOPE_TRACKING_JOB_RE = re.compile(r"^act_[A-Za-z0-9]+$")
_PHAGESCOPE_TASKID_HINT_RE = re.compile(
    r"(?:remote[_\s-]?task[_\s-]?id|task[_\s-]?id|task)\s*[:=]?\s*['\"]?(\d{4,})",
    flags=re.IGNORECASE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data conversion helpers
# ---------------------------------------------------------------------------

def _derive_conversation_id(session_id: Optional[str]) -> Optional[int]:
    """Map session_id to a stable integer ID."""
    if not session_id:
        return None
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return int(digest, 16)


def _convert_history_to_agent_format(
    history: Optional[List[ChatMessage]],
) -> List[Dict[str, Any]]:
    """Transform frontend history messages into agent-ready format."""
    if not history:
        return []
    return [{"role": msg.role, "content": msg.content} for msg in history]


def _loads_metadata(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:  # pragma: no cover - best effort parsing
        return None


def _normalize_search_provider(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate in VALID_SEARCH_PROVIDERS:
        return candidate
    return None


def _normalize_base_model(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate in VALID_BASE_MODELS:
        return candidate
    return None


def _normalize_llm_provider(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate in VALID_LLM_PROVIDERS:
        return candidate
    return None


def _dump_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    if not isinstance(metadata, dict):
        return None
    if not metadata:
        return None
    return json.dumps(metadata, ensure_ascii=False)


def _extract_session_settings(
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not metadata:
        return None
    settings: Dict[str, Any] = {}
    provider = _normalize_search_provider(metadata.get("default_search_provider"))
    if provider:
        settings["default_search_provider"] = provider
    base_model = _normalize_base_model(metadata.get("default_base_model"))
    if base_model:
        settings["default_base_model"] = base_model
    llm_provider = _normalize_llm_provider(metadata.get("default_llm_provider"))
    if llm_provider:
        settings["default_llm_provider"] = llm_provider
    return settings or None


def _extract_session_runtime_context(
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not metadata:
        return {}
    runtime: Dict[str, Any] = {}
    for key in SESSION_RUNTIME_CONTEXT_KEYS:
        value = metadata.get(key)
        if isinstance(value, dict):
            runtime[key] = dict(value)
        elif isinstance(value, list) and key == "recent_image_artifacts":
            runtime[key] = [dict(item) for item in value if isinstance(item, dict)]
    return runtime


# ---------------------------------------------------------------------------
# Database query helpers
# ---------------------------------------------------------------------------

def _lookup_plan_title(conn, plan_id: Optional[int]) -> Optional[str]:
    if plan_id is None:
        return None
    row = conn.execute("SELECT title FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        return None
    return row["title"]


def _normalize_owner_id(owner_id: Optional[str]) -> str:
    text = str(owner_id or "").strip()
    return text or "legacy-local"


def _lookup_session_owner(conn, session_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT owner_id FROM chat_sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    return _normalize_owner_id(row["owner_id"])


def lookup_session_owner(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    from ...database import get_db  # lazy import

    with get_db() as conn:
        return _lookup_session_owner(conn, session_id)


def _resolve_session_owner_id(
    conn,
    session_id: str,
    owner_id: Optional[str],
) -> str:
    normalized = str(owner_id or "").strip()
    if normalized:
        return normalized
    existing_owner_id = _lookup_session_owner(conn, session_id)
    if existing_owner_id:
        return existing_owner_id
    return _normalize_owner_id(None)


def _row_to_session_info(row) -> Dict[str, Any]:
    """Convert a SQLite row into a session info dictionary."""
    metadata = None
    if isinstance(row, dict) or hasattr(row, "keys"):
        try:
            metadata = _loads_metadata(row["metadata"])
        except Exception:
            metadata = None
    info = {
        "id": row["id"],
        "name": row["name"],
        "name_source": row["name_source"] if "name_source" in row.keys() else None,
        "is_user_named": (
            bool(row["is_user_named"])
            if "is_user_named" in row.keys() and row["is_user_named"] is not None
            else None
        ),
        "plan_id": row["plan_id"],
        "plan_title": row["plan_title"],
        "current_task_id": row["current_task_id"],
        "current_task_name": row["current_task_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_message_at": row["last_message_at"],
        "is_active": bool(row["is_active"]) if row["is_active"] is not None else True,
    }
    settings = _extract_session_settings(metadata)
    if settings:
        info["settings"] = settings
    else:
        info["settings"] = None
    return info


def _fetch_session_info(
    conn,
    session_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve information for a specific session."""
    params: List[Any] = [session_id]
    where_sql = "WHERE s.id = ?"
    if owner_id is not None:
        where_sql += " AND s.owner_id = ?"
        params.append(_normalize_owner_id(owner_id))

    row = conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.name_source,
            s.is_user_named,
            s.metadata,
            s.plan_id,
            s.plan_title,
            s.current_task_id,
            s.current_task_name,
            s.created_at,
            s.updated_at,
            s.is_active,
            COALESCE(
                s.last_message_at,
                (
                    SELECT MAX(m.created_at)
                    FROM chat_messages m
                    WHERE m.session_id = s.id
                )
            ) AS last_message_at
        FROM chat_sessions s
        """
        + where_sql,
        tuple(params),
    ).fetchone()
    if not row:
        return None
    return _row_to_session_info(row)


def _load_session_metadata_dict(
    conn,
    session_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    params: List[Any] = [session_id]
    where_sql = "WHERE id=?"
    if owner_id is not None:
        where_sql += " AND owner_id=?"
        params.append(_normalize_owner_id(owner_id))
    row = conn.execute(
        f"SELECT metadata FROM chat_sessions {where_sql}",
        tuple(params),
    ).fetchone()
    if not row:
        return {}
    data = _loads_metadata(row["metadata"])
    return data or {}


def _update_session_metadata(
    session_id: str,
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    owner_id: Optional[str] = None,
) -> None:
    from ...database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        _ensure_session_exists(session_id, conn, owner_id=owner_id)
        metadata = _load_session_metadata_dict(conn, session_id, owner_id=owner_id)
        updated = updater(dict(metadata))
        params: List[Any] = [json.dumps(updated, ensure_ascii=False), session_id]
        where_sql = "WHERE id=?"
        if owner_id is not None:
            where_sql += " AND owner_id=?"
            params.append(_normalize_owner_id(owner_id))
        conn.execute(
            """
            UPDATE chat_sessions
            SET metadata=?,
                updated_at=CURRENT_TIMESTAMP
            """
            + where_sql,
            tuple(params),
        )
        conn.commit()


def _load_session_runtime_context(
    session_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    from ...database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id, owner_id=owner_id)
    return _extract_session_runtime_context(metadata)


def _normalize_modulelist_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("{") or raw.startswith("["):
            try:
                parsed = json.loads(raw.replace("'", '"'))
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return [str(key) for key in parsed.keys()]
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        if "," in raw:
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [raw]
    return [str(value)]


def _find_key_recursive(value: Any, key: str) -> Optional[Any]:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        for item in value.values():
            found = _find_key_recursive(item, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_key_recursive(item, key)
            if found is not None:
                return found
    return None


def _find_all_keys_recursive(value: Any, key: str) -> List[Any]:
    matches: List[Any] = []
    if isinstance(value, dict):
        if key in value:
            matches.append(value.get(key))
        for item in value.values():
            matches.extend(_find_all_keys_recursive(item, key))
        return matches
    if isinstance(value, list):
        for item in value:
            matches.extend(_find_all_keys_recursive(item, key))
    return matches


def _normalize_phagescope_taskid(value: Any) -> Optional[str]:
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


def _lookup_phagescope_remote_taskid_by_job_id(
    job_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    from ...database import get_db  # lazy import to avoid cycles

    token = str(job_id or "").strip()
    if not token:
        return None
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
    if not row:
        return None
    return _normalize_phagescope_taskid(row["remote_taskid"])


def _extract_phagescope_taskid_from_payload(value: Any) -> Optional[str]:
    if value is None:
        return None

    normalized = _normalize_phagescope_taskid(value)
    if normalized:
        return normalized

    if isinstance(value, dict):
        for key in ("taskid", "task_id", "remote_taskid", "remote_task_id"):
            candidates = _find_all_keys_recursive(value, key)
            for candidate in candidates:
                normalized = _normalize_phagescope_taskid(candidate)
                if normalized:
                    return normalized
        for nested in value.values():
            nested_taskid = _extract_phagescope_taskid_from_payload(nested)
            if nested_taskid:
                return nested_taskid
        return None

    if isinstance(value, list):
        for item in value:
            nested_taskid = _extract_phagescope_taskid_from_payload(item)
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


def _lookup_phagescope_remote_taskid_from_action_run(
    run_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    from ...database import get_db  # lazy import to avoid cycles

    token = str(run_id or "").strip()
    if not token:
        return None

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
        taskid = _extract_phagescope_taskid_from_payload(parsed)
        if taskid:
            return taskid

    user_message = row["user_message"]
    if isinstance(user_message, str) and user_message.strip():
        taskid = _extract_phagescope_taskid_from_payload(user_message)
        if taskid:
            return taskid
    return None


def _resolve_phagescope_taskid_alias(
    taskid: Any,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    normalized = _normalize_phagescope_taskid(taskid)
    if normalized:
        return normalized

    task_text = str(taskid or "").strip()
    if not task_text:
        return None
    if not _PHAGESCOPE_TRACKING_JOB_RE.fullmatch(task_text):
        return None

    try:
        resolved = _lookup_phagescope_remote_taskid_by_job_id(
            task_text, session_id=session_id
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve PhageScope tracking job %s: %s", task_text, exc)
        resolved = None
    if resolved:
        return resolved

    try:
        resolved = _lookup_phagescope_remote_taskid_from_action_run(
            task_text, session_id=session_id
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve action run task alias %s: %s", task_text, exc)
        return None
    return resolved


def _extract_taskid_from_result(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    for key in ("taskid", "remote_taskid", "task_id", "remote_task_id"):
        candidates = _find_all_keys_recursive(result, key)
        for candidate in candidates:
            normalized = _normalize_phagescope_taskid(candidate)
            if normalized:
                return normalized
    return None


# ---------------------------------------------------------------------------
# PhageScope-specific helpers
# ---------------------------------------------------------------------------

def _extract_phagescope_task_snapshot(detail_result: Any) -> Dict[str, Any]:
    """Extract a compact status snapshot from phagescope task_detail output."""
    if not isinstance(detail_result, dict):
        return {}
    payload = detail_result.get("data")
    if not isinstance(payload, dict):
        return {}

    snapshot: Dict[str, Any] = {}
    results = payload.get("results")
    if isinstance(results, dict):
        for key in ("status", "task_status", "state", "taskstatus"):
            value = results.get(key)
            if isinstance(value, str) and value.strip():
                snapshot["remote_status"] = value.strip()
                break

    task_detail = payload.get("parsed_task_detail")
    if not isinstance(task_detail, dict) and isinstance(results, dict):
        raw_task_detail = results.get("task_detail")
        if isinstance(raw_task_detail, dict):
            task_detail = raw_task_detail
        elif isinstance(raw_task_detail, str) and raw_task_detail.strip():
            try:
                parsed = json.loads(raw_task_detail)
                if isinstance(parsed, dict):
                    task_detail = parsed
            except Exception:
                task_detail = None

    if not isinstance(task_detail, dict):
        return snapshot

    task_status = task_detail.get("task_status")
    if isinstance(task_status, str) and task_status.strip():
        snapshot["task_status"] = task_status.strip()

    queue = task_detail.get("task_que")
    if not isinstance(queue, list):
        return snapshot

    done_states = {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}
    failed_states = {"FAILED", "ERROR"}
    done = 0
    failed = 0
    waiting = 0
    running_modules: List[str] = []
    total = 0
    for item in queue:
        if not isinstance(item, dict):
            continue
        module_name = str(item.get("module") or "").strip()
        if not module_name:
            continue
        status_raw = item.get("module_satus") or item.get("module_status") or item.get("status")
        status_upper = str(status_raw or "").strip().upper()
        total += 1
        if status_upper in done_states:
            done += 1
            continue
        if status_upper in failed_states:
            failed += 1
            continue
        waiting += 1
        if len(running_modules) < 5:
            running_modules.append(module_name)

    snapshot["counts"] = {
        "done": done,
        "failed": failed,
        "waiting": waiting,
        "total": total,
    }
    if running_modules:
        snapshot["running_modules"] = running_modules
    return snapshot


def _build_phagescope_submit_background_summary(
    *,
    taskid: str,
    background_job_id: str,
    module_items: Optional[List[str]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    snapshot = snapshot or {}
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    done = counts.get("done") if isinstance(counts.get("done"), int) else 0
    total_from_snapshot = counts.get("total") if isinstance(counts.get("total"), int) else 0
    total = total_from_snapshot or len(module_items or [])
    progress_text = f"{done}/{total}" if total > 0 else "0/?"

    remote_status = (
        str(snapshot.get("remote_status") or "").strip()
        or str(snapshot.get("task_status") or "").strip()
        or "submitted"
    )
    running_modules = snapshot.get("running_modules") if isinstance(snapshot.get("running_modules"), list) else []
    running_suffix = ""
    if running_modules:
        running_suffix = f"\uff0c\u8fdb\u884c\u4e2d\u6a21\u5757\uff1a{', '.join(str(x) for x in running_modules[:3])}"

    return (
        f"PhageScope \u4efb\u52a1\u5df2\u63d0\u4ea4\uff08taskid={taskid}\uff09\u3002"
        f"\u5df2\u5b8c\u6210\uff1asubmit\u3002"
        f"\u540e\u53f0\u8fd0\u884c\u4e2d\uff1a\u540e\u53f0\u4efb\u52a1ID={background_job_id}\uff0c\u72b6\u6001={remote_status}\uff0c\u6a21\u5757\u8fdb\u5ea6={progress_text}{running_suffix}\u3002"
        "\u4e0b\u4e00\u6b65\uff1a\u5728\u300c\u540e\u53f0\u4efb\u52a1\u300d\u5237\u65b0\u67e5\u770b\u6700\u65b0\u72b6\u6001\uff1b\u4efb\u52a1\u5b8c\u6210\u540e\u518d\u6267\u884c result/save_all/download\u3002"
    )


def _is_empty_phagescope_param(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _backfill_phagescope_submit_params(
    submit_params: Dict[str, Any],
    *,
    sorted_actions: Sequence[LLMAction],
    submit_action: LLMAction,
    user_message: Optional[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Best-effort backfill for submit params from earlier PhageScope actions and message."""
    patched = dict(submit_params or {})
    backfilled_keys: List[str] = []
    candidates = (
        "userid",
        "phageid",
        "phageids",
        "modulelist",
        "analysistype",
        "inputtype",
        "sequence_ids",
        "sequence",
        "file_path",
    )

    previous_values: Dict[str, Any] = {}
    for action in sorted_actions:
        if action is submit_action:
            break
        if action.kind != "tool_operation" or action.name != "phagescope":
            continue
        if not isinstance(action.parameters, dict):
            continue
        for key in candidates:
            value = action.parameters.get(key)
            if not _is_empty_phagescope_param(value):
                previous_values[key] = value

    for key, value in previous_values.items():
        if _is_empty_phagescope_param(patched.get(key)):
            patched[key] = value
            backfilled_keys.append(key)

    # If userid is still missing, try extracting an email-like identifier from user message.
    if _is_empty_phagescope_param(patched.get("userid")) and isinstance(user_message, str):
        email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_message)
        if email_match:
            patched["userid"] = email_match.group(0)
            backfilled_keys.append("userid(from_message)")

    return patched, backfilled_keys


def _record_phagescope_task_memory(
    session_id: str, params: Dict[str, Any], result: Any
) -> Optional[str]:
    taskid = _extract_taskid_from_result(result)
    if not taskid:
        return None
    entry = {
        "taskid": taskid,
        "userid": params.get("userid"),
        "phageid": params.get("phageid"),
        "modulelist": _normalize_modulelist_value(params.get("modulelist")),
        "created_at": datetime.utcnow().isoformat(),
    }

    def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
        tasks = metadata.get("phagescope_recent_tasks")
        if not isinstance(tasks, list):
            tasks = []
        tasks = [item for item in tasks if item.get("taskid") != taskid]
        tasks.insert(0, entry)
        metadata["phagescope_recent_tasks"] = tasks[:10]
        metadata["phagescope_last_taskid"] = taskid
        return metadata

    _update_session_metadata(session_id, _updater)
    return taskid


def _lookup_phagescope_task_memory(
    session_id: str,
    *,
    userid: Optional[str],
    phageid: Optional[str],
    modulelist: Optional[Any],
) -> Optional[str]:
    from ...database import get_db  # lazy import to avoid cycles

    module_items = _normalize_modulelist_value(modulelist)
    module_set = {item.lower() for item in module_items}
    phageid_value = phageid.strip() if isinstance(phageid, str) else None
    userid_value = userid.strip() if isinstance(userid, str) else None

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id)

    tasks = metadata.get("phagescope_recent_tasks")
    if not isinstance(tasks, list):
        return metadata.get("phagescope_last_taskid")

    for item in tasks:
        if not isinstance(item, dict):
            continue
        if userid_value and item.get("userid") and item.get("userid") != userid_value:
            continue
        if phageid_value and item.get("phageid") and item.get("phageid") != phageid_value:
            continue
        if module_set:
            stored = {str(val).lower() for val in item.get("modulelist", [])}
            if stored and not module_set.issubset(stored):
                continue
        taskid = item.get("taskid")
        if taskid:
            return str(taskid)
    return metadata.get("phagescope_last_taskid")


# ---------------------------------------------------------------------------
# Session and plan management
# ---------------------------------------------------------------------------

def _get_session_settings(
    session_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    from ...database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id, owner_id=owner_id)
    settings = _extract_session_settings(metadata)
    return settings or {}


def _get_session_current_task(
    session_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Optional[int]:
    """Get the current_task_id from session if set."""
    from ...database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        params: List[Any] = [session_id]
        where_sql = "WHERE id=?"
        if owner_id is not None:
            where_sql += " AND owner_id=?"
            params.append(_normalize_owner_id(owner_id))
        row = conn.execute(
            f"SELECT current_task_id FROM chat_sessions {where_sql}",
            tuple(params),
        ).fetchone()
    if not row:
        return None
    task_id = row["current_task_id"]
    if task_id is not None:
        try:
            return int(task_id)
        except (TypeError, ValueError):
            return None
    return None


def _ensure_session_exists(
    session_id: str,
    conn,
    plan_id: Optional[int] = None,
    owner_id: Optional[str] = None,
) -> Optional[int]:
    """Ensure the chat_sessions table contains this session."""
    normalized_owner_id = _resolve_session_owner_id(conn, session_id, owner_id)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, plan_id, owner_id FROM chat_sessions WHERE id = ?",
        (session_id,),
    )
    row = cursor.fetchone()
    if not row:
        plan_title = _lookup_plan_title(conn, plan_id)
        cursor.execute(
            """
            INSERT INTO chat_sessions (
                id,
                owner_id,
                name,
                name_source,
                is_user_named,
                metadata,
                plan_id,
                plan_title,
                last_message_at,
                created_at,
                updated_at,
                is_active
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
            )
            """,
            (
                session_id,
                normalized_owner_id,
                f"Session {session_id[:8]}",
                "default",
                0,
                None,
                plan_id,
                plan_title,
            ),
        )
        logger.info("Created new chat session: %s (plan_id=%s)", session_id, plan_id)
        return plan_id

    existing_owner_id = _normalize_owner_id(row["owner_id"])
    if existing_owner_id != normalized_owner_id:
        raise PermissionError(f"Session {session_id} belongs to another owner")

    current_plan_id = row["plan_id"]
    if plan_id is not None and current_plan_id != plan_id:
        plan_title = _lookup_plan_title(conn, plan_id)
        cursor.execute(
            """
            UPDATE chat_sessions
            SET plan_id=?,
                plan_title=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (plan_id, plan_title, session_id),
        )
        logger.info(
            "Updated chat session %s binding to plan %s (was %s)",
            session_id,
            plan_id,
            current_plan_id,
        )
        return plan_id
    return current_plan_id


def _resolve_plan_binding(
    session_id: Optional[str],
    requested_plan_id: Optional[int],
    *,
    owner_id: Optional[str] = None,
) -> Optional[int]:
    """Determine the final bound plan ID based on session state and request parameters."""
    if not session_id:
        return requested_plan_id

    from ...database import get_db  # lazy import

    with get_db() as conn:
        current_plan_id = _ensure_session_exists(
            session_id,
            conn,
            requested_plan_id,
            owner_id=owner_id,
        )
        if current_plan_id is not None:
            return current_plan_id
    return requested_plan_id


def _set_session_plan_id(
    session_id: str,
    plan_id: Optional[int],
    *,
    owner_id: Optional[str] = None,
) -> None:
    """Update the plan binding for the session."""
    from ...database import get_db  # lazy import

    with get_db() as conn:
        _ensure_session_exists(session_id, conn, owner_id=owner_id)
        plan_title = _lookup_plan_title(conn, plan_id)
        params: List[Any] = [plan_id, plan_title, session_id]
        where_sql = "WHERE id=?"
        if owner_id is not None:
            where_sql += " AND owner_id=?"
            params.append(_normalize_owner_id(owner_id))
        conn.execute(
            """
            UPDATE chat_sessions
            SET plan_id=?,
                plan_title=?,
                updated_at=CURRENT_TIMESTAMP
            """
            + where_sql,
            tuple(params),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------

def _save_chat_message(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    owner_id: Optional[str] = None,
) -> None:
    """Persist chat message."""
    try:
        from ...database import get_db  # lazy import to avoid circular deps

        with get_db() as conn:
            _ensure_session_exists(session_id, conn, owner_id=owner_id)
            cursor = conn.cursor()
            metadata_json = (
                json.dumps(metadata, ensure_ascii=False) if metadata else None
            )
            logger.info(
                "[CHAT][SAVE] session=%s role=%s content=%s metadata=%s",
                session_id,
                role,
                content,
                metadata,
            )
            cursor.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, metadata_json),
            )

            # Process message through chat memory middleware
            try:
                from ...services.memory.chat_memory_middleware import get_chat_memory_middleware

                middleware = get_chat_memory_middleware()
                # Run async middleware in background (fire and forget)
                asyncio.create_task(
                    middleware.process_message(
                        content=content,
                        role=role,
                        session_id=session_id
                    )
                )
            except Exception as mem_err:
                logger.warning(f"Failed to process chat memory: {mem_err}")
            cursor.execute(
                """
                UPDATE chat_sessions
                SET last_message_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (session_id,),
            )
            conn.commit()
    except PermissionError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to save chat message: %s", exc)


def _load_chat_history(
    session_id: str,
    limit: int = 50,
    before_id: Optional[int] = None,
    *,
    owner_id: Optional[str] = None,
) -> Tuple[List[ChatMessage], bool]:
    """Load session history."""
    try:
        from ...database import get_db  # lazy import

        with get_db() as conn:
            cursor = conn.cursor()
            params: List[Any] = [session_id]
            owner_clause = ""
            if owner_id is not None:
                owner_clause = (
                    "AND EXISTS ("
                    "SELECT 1 FROM chat_sessions s "
                    "WHERE s.id = chat_messages.session_id AND s.owner_id = ?"
                    ")"
                )
                params.append(_normalize_owner_id(owner_id))
            before_clause = ""
            if before_id is not None:
                before_clause = "AND id < ?"
                params.append(before_id)
            params.append(limit + 1)
            cursor.execute(
                f"""
                SELECT id, role, content, metadata, created_at
                FROM chat_messages
                WHERE session_id = ?
                {owner_clause}
                {before_clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            )
            rows = cursor.fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()

        messages = [
            ChatMessage(
                id=msg_id,
                role=role,
                content=content,
                timestamp=created_at,
                metadata=_loads_metadata(metadata_raw),
            )
            for msg_id, role, content, metadata_raw, created_at in rows
        ]
        return messages, has_more
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load history: %s", exc)
        return [], False


def _save_assistant_response(
    session_id: Optional[str],
    response: ChatResponse,
    *,
    owner_id: Optional[str] = None,
) -> ChatResponse:
    """Persist assistant response."""
    if session_id and response.response:
        _save_chat_message(
            session_id,
            "assistant",
            response.response,
            metadata=response.metadata,
            owner_id=owner_id,
        )
    return response


def _update_message_metadata_by_tracking(
    session_id: Optional[str],
    tracking_id: Optional[str],
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    if not session_id or not tracking_id:
        return
    from ...database import get_db  # lazy import

    pattern = f'%"tracking_id": "{tracking_id}"%'
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, metadata FROM chat_messages WHERE session_id=? AND metadata LIKE ? ORDER BY id DESC LIMIT 1",
                (session_id, pattern),
            ).fetchone()
            if not row:
                return
            current = _loads_metadata(row["metadata"]) or {}
            updated = updater(dict(current))
            conn.execute(
                "UPDATE chat_messages SET metadata=? WHERE id=?",
                (json.dumps(updated, ensure_ascii=False), row["id"]),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Failed to update chat message metadata for %s: %s", tracking_id, exc
        )

def _update_message_content_by_tracking(
    session_id: Optional[str],
    tracking_id: Optional[str],
    content: str,
) -> None:
    if not session_id or not tracking_id:
        return
    from ...database import get_db  # lazy import

    pattern = f'%"tracking_id": "{tracking_id}"%'
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM chat_messages WHERE session_id=? AND metadata LIKE ? ORDER BY id DESC LIMIT 1",
                (session_id, pattern),
            ).fetchone()
            if not row:
                return
            conn.execute(
                "UPDATE chat_messages SET content=? WHERE id=?",
                (content, row["id"]),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Failed to update chat message content for %s: %s", tracking_id, exc
        )


def _merge_async_metadata(
    existing: Optional[Dict[str, Any]],
    *,
    status: str,
    tracking_id: str,
    plan_id: Optional[int],
    actions: List[Dict[str, Any]],
    actions_summary: Optional[List[Dict[str, Any]]],
    tool_results: List[Dict[str, Any]],
    artifact_gallery: Optional[List[Dict[str, Any]]] = None,
    errors: List[str],
    job_id: Optional[str] = None,
    job_payload: Optional[Dict[str, Any]] = None,
    job_type: Optional[str] = None,
    final_summary: Optional[str] = None,
    analysis_text: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = dict(existing or {})
    metadata["status"] = status
    metadata["tracking_id"] = tracking_id
    if plan_id is not None:
        metadata["plan_id"] = plan_id
    metadata["actions"] = actions
    metadata["action_list"] = actions
    if actions_summary:
        metadata["actions_summary"] = actions_summary
    elif "actions_summary" in metadata:
        metadata.pop("actions_summary")
    if tool_results:
        metadata["tool_results"] = tool_results
    elif "tool_results" in metadata:
        metadata.pop("tool_results")
    if artifact_gallery:
        metadata["artifact_gallery"] = artifact_gallery
    elif "artifact_gallery" in metadata:
        metadata.pop("artifact_gallery")
    metadata["errors"] = errors or []
    if "raw_actions" not in metadata and actions:
        metadata["raw_actions"] = actions
    if final_summary:
        metadata["final_summary"] = final_summary
    if analysis_text:
        metadata["analysis_text"] = analysis_text

    if job_id:
        metadata["type"] = "job_log"
        metadata["job_id"] = job_id
        metadata["job_type"] = job_type or metadata.get("job_type") or "chat_action"
        if job_payload:
            metadata["job"] = job_payload
            metadata["job_status"] = job_payload.get("status")
            metadata.setdefault("plan_id", job_payload.get("plan_id"))
            if "logs" in job_payload:
                metadata["job_logs"] = job_payload.get("logs")

    latest_decomposition_job: Optional[Dict[str, Any]] = (
        metadata.get("decomposition_job")
        if isinstance(metadata.get("decomposition_job"), dict)
        else None
    )

    for action in actions or []:
        details = action.get("details") or {}
        embedded_job = details.get("decomposition_job")
        if isinstance(embedded_job, dict):
            embedded_job_id = embedded_job.get("job_id")
            if isinstance(embedded_job_id, str) and embedded_job_id.strip():
                job_summary: Dict[str, Any] = {
                    "job_id": embedded_job_id,
                    "job_type": embedded_job.get("job_type") or "plan_decompose",
                    "status": embedded_job.get("status"),
                    "plan_id": embedded_job.get("plan_id"),
                    "task_id": embedded_job.get("task_id"),
                    "mode": embedded_job.get("mode"),
                    "error": embedded_job.get("error"),
                    "created_at": embedded_job.get("created_at"),
                    "started_at": embedded_job.get("started_at"),
                    "finished_at": embedded_job.get("finished_at"),
                }
                if isinstance(embedded_job.get("stats"), dict):
                    job_summary["stats"] = embedded_job.get("stats")
                if isinstance(embedded_job.get("params"), dict):
                    job_summary["params"] = embedded_job.get("params")
                if isinstance(embedded_job.get("metadata"), dict):
                    job_summary["metadata"] = embedded_job.get("metadata")
                latest_decomposition_job = job_summary
        if embedded_job and "job_id" not in metadata:
            metadata["type"] = "job_log"
            metadata["job"] = embedded_job
            metadata["job_id"] = embedded_job.get("job_id")
            metadata["job_status"] = embedded_job.get("status")
            if embedded_job.get("job_type"):
                metadata["job_type"] = embedded_job.get("job_type")
            metadata.setdefault("plan_id", embedded_job.get("plan_id"))
            metadata["job_logs"] = embedded_job.get("logs")
        if "target_task_name" not in metadata:
            if "target_task_name" in details:
                metadata["target_task_name"] = details["target_task_name"]
            elif "title" in details:
                metadata["target_task_name"] = details["title"]

    if latest_decomposition_job:
        metadata["decomposition_job"] = latest_decomposition_job
        metadata["decomposition_job_id"] = latest_decomposition_job.get("job_id")
        metadata["decomposition_job_status"] = latest_decomposition_job.get("status")

    return metadata


def _get_llm_service_for_provider(
    provider: Optional[str],
    model: Optional[str] = None,
) -> LLMService:
    normalized = _normalize_llm_provider(provider)
    if normalized:
        return get_llm_service_for_provider(normalized, model)
    return get_llm_service()
