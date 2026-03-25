from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.database import get_db
from app.services.realtime_bus import EventSubscription, get_realtime_bus, route_control_message
from app.services.plans.decomposition_jobs import plan_decomposition_jobs
from app.services.request_principal import ensure_owner_access, get_request_owner_id
from . import register_router

job_router = APIRouter(prefix="/jobs", tags=["jobs"])
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_CLAUDE_LOG_DIR = _PROJECT_ROOT / "runtime" / "claude_code_logs"
_logger = logging.getLogger(__name__)


def _sse_message(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class AsyncJobStatusResponse(BaseModel):
    job_id: str
    job_type: str = "plan_decompose"
    status: str
    plan_id: Optional[int] = None
    task_id: Optional[int] = None
    mode: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    stats: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    action_logs: List[Dict[str, Any]] = Field(default_factory=list)
    action_cursor: Optional[str] = None


class JobLogTailResponse(BaseModel):
    job_id: str
    log_path: str
    total_lines: int
    lines: List[str]
    truncated: bool


class JobControlRequest(BaseModel):
    action: Literal["pause", "resume", "skip_step"]


class JobControlResponse(BaseModel):
    success: bool
    job_id: str
    action: str
    status: Optional[str] = None
    message: str


class BackgroundTaskItem(BaseModel):
    category: Literal["task_creation", "phagescope", "claude_code"]
    job_id: str
    job_type: str
    status: str
    label: str
    session_id: Optional[str] = None
    plan_id: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    taskid: Optional[str] = None
    remote_status: Optional[str] = None
    phase: Optional[str] = None
    counts: Optional[Dict[str, int]] = None
    progress_percent: Optional[int] = None
    progress_status: Optional[str] = None
    progress_text: Optional[str] = None
    current_step: Optional[int] = None
    total_steps: Optional[int] = None
    done_steps: Optional[int] = None
    current_task_id: Optional[int] = None
    error: Optional[str] = None


class BackgroundTaskGroup(BaseModel):
    key: Literal["task_creation", "phagescope", "claude_code"]
    label: str
    total: int
    running: int
    queued: int
    succeeded: int
    failed: int
    items: List[BackgroundTaskItem] = Field(default_factory=list)


class BackgroundTaskBoardResponse(BaseModel):
    generated_at: str
    total: int
    groups: Dict[str, BackgroundTaskGroup]


def _is_safe_job_id(job_id: str) -> bool:
    if not job_id:
        return False
    for ch in job_id:
        if ch.isalnum() or ch in {"-", "_"}:
            continue
        return False
    return True


def _tail_file_lines(path: Path, max_lines: int) -> tuple[List[str], int]:
    lines: deque[str] = deque(maxlen=max_lines)
    total = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            total += 1
            lines.append(raw.rstrip("\n"))
    return list(lines), total


def _format_job_event_line(event: Dict[str, Any]) -> str:
    timestamp = str(event.get("timestamp") or "").strip()
    level = str(event.get("level") or "info").upper()
    message = str(event.get("message") or "").strip()
    metadata = event.get("metadata")

    parts: List[str] = []
    if timestamp:
        parts.append(f"[{timestamp}]")
    parts.append(level)
    if message:
        parts.append(message)

    line = " ".join(parts).strip()
    if isinstance(metadata, dict) and metadata:
        line = f"{line} {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}".strip()
    return line


def _build_job_log_fallback(job_id: str, tail: int) -> Optional[JobLogTailResponse]:
    payload = plan_decomposition_jobs.get_job_payload(job_id)
    if payload is None:
        return None

    event_rows = payload.get("logs")
    if not isinstance(event_rows, list) or not event_rows:
        event_rows = payload.get("action_logs")
    if not isinstance(event_rows, list):
        event_rows = []

    rendered = [
        line
        for line in (_format_job_event_line(row) for row in event_rows if isinstance(row, dict))
        if line
    ]
    total = len(rendered)
    return JobLogTailResponse(
        job_id=job_id,
        log_path=f"job://{job_id}/events",
        total_lines=total,
        lines=rendered[-tail:],
        truncated=total > tail,
    )


def _load_json(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _extract_actions_from_structured(structured_json: Optional[str]) -> List[Dict[str, Any]]:
    payload = _load_json(structured_json)
    if not isinstance(payload, dict):
        return []
    actions = payload.get("actions")
    if isinstance(actions, list):
        return [item for item in actions if isinstance(item, dict)]
    return []


def _classify_action_run(
    job_payload: Optional[Dict[str, Any]],
    actions: List[Dict[str, Any]],
) -> Optional[Literal["task_creation", "phagescope", "claude_code"]]:
    job_type = str((job_payload or {}).get("job_type") or "").strip().lower()
    if job_type == "phagescope_track":
        return "phagescope"
    if job_type == "plan_decompose":
        return "task_creation"
    if job_type == "plan_execute":
        return "claude_code"

    has_claude_code = False
    has_phagescope = False
    has_plan_creation = False
    for action in actions:
        kind = str(action.get("kind") or "").strip().lower()
        name = str(action.get("name") or "").strip().lower()
        if kind == "plan_operation" and name in ("create_plan", "optimize_plan"):
            has_plan_creation = True
        if kind != "tool_operation":
            continue
        if name == "claude_code":
            has_claude_code = True
        if name == "phagescope":
            has_phagescope = True

    if has_phagescope:
        return "phagescope"
    if has_claude_code:
        return "claude_code"
    if has_plan_creation:
        return "task_creation"
    return None


def _extract_first_action_label(actions: List[Dict[str, Any]]) -> Optional[str]:
    if not actions:
        return None
    first = actions[0]
    kind = str(first.get("kind") or "").strip().lower()
    name = str(first.get("name") or "").strip()
    if kind == "tool_operation" and name:
        return f"Invoke {name}"
    if name:
        return name
    return None


def _default_label(category: str) -> str:
    if category == "task_creation":
        return "Task Creation"
    if category == "phagescope":
        return "PhageScope Job"
    if category == "claude_code":
        return "Claude Code Execution"
    return "Background Job"


def _extract_phagescope_progress(job_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    stats = (job_payload or {}).get("stats")
    if not isinstance(stats, dict):
        return {}
    progress = stats.get("tool_progress")
    if not isinstance(progress, dict):
        return {}
    out: Dict[str, Any] = {}
    taskid = progress.get("taskid")
    if taskid is not None:
        out["taskid"] = str(taskid)
    remote_status = progress.get("status")
    if isinstance(remote_status, str) and remote_status.strip():
        out["remote_status"] = remote_status
    phase = progress.get("phase")
    if isinstance(phase, str) and phase.strip():
        out["phase"] = phase
    counts = progress.get("counts")
    if isinstance(counts, dict):
        done = counts.get("done")
        total = counts.get("total")
        if isinstance(done, int) and isinstance(total, int):
            out["counts"] = {"done": done, "total": total}

    if "taskid" not in out:
        result = (job_payload or {}).get("result")
        if isinstance(result, dict):
            # Try multiple locations where taskid might be stored
            for key in ("remote_taskid", "taskid"):
                val = result.get(key)
                if val is not None:
                    out["taskid"] = str(val)
                    break
            # Also check nested phagescope dict and completed_now list
            if "taskid" not in out:
                ps = result.get("phagescope")
                if isinstance(ps, dict) and ps.get("taskid"):
                    out["taskid"] = str(ps["taskid"])
            if "taskid" not in out:
                completed = result.get("completed_now")
                if isinstance(completed, list):
                    for item in completed:
                        if isinstance(item, dict) and item.get("taskid"):
                            out["taskid"] = str(item["taskid"])
                            break
            # Check steps for phagescope actions that returned taskid
            if "taskid" not in out:
                steps = result.get("steps")
                if isinstance(steps, list):
                    for step in steps:
                        if not isinstance(step, dict):
                            continue
                        details = step.get("details") or {}
                        if not isinstance(details, dict):
                            continue
                        step_result = details.get("result")
                        if isinstance(step_result, dict):
                            for key in ("taskid", "remote_taskid"):
                                val = step_result.get(key)
                                if val is not None:
                                    out["taskid"] = str(val)
                                    break
                        if "taskid" in out:
                            break
    return out


def _build_group(key: Literal["task_creation", "phagescope", "claude_code"], label: str) -> BackgroundTaskGroup:
    return BackgroundTaskGroup(
        key=key,
        label=label,
        total=0,
        running=0,
        queued=0,
        succeeded=0,
        failed=0,
        items=[],
    )


def _normalize_status(raw_status: Any) -> str:
    value = str(raw_status or "").strip().lower()
    if value in {"succeeded", "completed", "success", "done"}:
        return "succeeded"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"running", "in_progress"}:
        return "running"
    if value in {"queued", "pending", "created"}:
        return "queued"
    return value or "queued"


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        if not text:
            return None
        if "." in text:
            return int(float(text))
        return int(text)
    except Exception:
        return None


def _clamp_progress_percent(percent_raw: Optional[int], normalized_status: str) -> Optional[int]:
    if percent_raw is None:
        if normalized_status in {"succeeded", "failed"}:
            return 100
        return None

    percent = max(0, min(100, int(percent_raw)))
    if normalized_status in {"running", "queued"} and percent >= 100:
        percent = 99
    if normalized_status in {"succeeded", "failed"}:
        percent = 100
    return percent


def _extract_item_progress(
    *,
    category: str,
    status: Any,
    job_payload: Optional[Dict[str, Any]],
    phage_progress: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_status = _normalize_status(status)
    payload = job_payload if isinstance(job_payload, dict) else {}
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    job_type = str(payload.get("job_type") or "").strip().lower()

    percent_raw: Optional[int] = None
    progress_text: Optional[str] = None
    current_step: Optional[int] = None
    total_steps: Optional[int] = None
    done_steps: Optional[int] = None
    current_task_id: Optional[int] = None

    # Generic tool progress fallback (works for phagescope and future tools).
    tool_progress = stats.get("tool_progress") if isinstance(stats.get("tool_progress"), dict) else {}
    if tool_progress:
        percent_raw = _to_int(tool_progress.get("percent"))
        counts = tool_progress.get("counts")
        if isinstance(counts, dict):
            done = _to_int(counts.get("done"))
            total = _to_int(counts.get("total"))
            if done is not None and total is not None and total > 0:
                done_steps = done
                total_steps = total
                if percent_raw is None:
                    percent_raw = int(round((min(done, total) / total) * 100))
                progress_text = f"{done}/{total}"

    if job_type == "plan_execute":
        executed = max(0, _to_int(stats.get("executed")) or 0)
        failed = max(0, _to_int(stats.get("failed")) or 0)
        skipped = max(0, _to_int(stats.get("skipped")) or 0)
        done = executed + failed + skipped
        total = _to_int(stats.get("total_steps"))
        if total is None:
            total = _to_int(params.get("total_steps"))
        if total is None:
            total = _to_int(params.get("steps"))
        current_step = _to_int(stats.get("current_step"))
        current_task_id = _to_int(stats.get("current_task_id"))
        if total is not None and total > 0:
            total_steps = total
            done_steps = done
            if percent_raw is None:
                percent_raw = _to_int(stats.get("progress_percent"))
            if percent_raw is None:
                percent_raw = int(round((min(done, total) / total) * 100))
            progress_text = f"{done}/{total}"

    if job_type == "plan_decompose" or category == "task_creation":
        node_budget = _to_int(params.get("node_budget"))
        if node_budget is None:
            node_budget = _to_int(stats.get("node_budget"))
        consumed_budget = _to_int(stats.get("consumed_budget"))
        queue_remaining = _to_int(stats.get("queue_remaining"))
        if node_budget is not None and node_budget > 0 and consumed_budget is not None:
            if percent_raw is None:
                percent_raw = int(round((min(consumed_budget, node_budget) / node_budget) * 100))
            progress_text = f"{consumed_budget}/{node_budget}"
        elif queue_remaining is not None and normalized_status in {"running", "queued"}:
            progress_text = f"queue {queue_remaining}"

    if category == "phagescope":
        phage = phage_progress if isinstance(phage_progress, dict) else {}
        counts = phage.get("counts")
        if isinstance(counts, dict):
            done = _to_int(counts.get("done"))
            total = _to_int(counts.get("total"))
            if done is not None and total is not None and total > 0:
                done_steps = done
                total_steps = total
                if percent_raw is None:
                    percent_raw = int(round((min(done, total) / total) * 100))
                progress_text = f"{done}/{total}"
        if not progress_text:
            remote_status = phage.get("remote_status")
            if isinstance(remote_status, str) and remote_status.strip():
                progress_text = remote_status.strip()

    percent = _clamp_progress_percent(percent_raw, normalized_status)
    if percent is None and normalized_status in {"running", "queued", "succeeded", "failed"}:
        percent = 100 if normalized_status in {"succeeded", "failed"} else 0

    out: Dict[str, Any] = {
        "progress_percent": percent,
        "progress_status": normalized_status,
    }
    if progress_text:
        out["progress_text"] = progress_text
    if current_step is not None:
        out["current_step"] = current_step
    if total_steps is not None:
        out["total_steps"] = total_steps
    if done_steps is not None:
        out["done_steps"] = done_steps
    if current_task_id is not None:
        out["current_task_id"] = current_task_id
    return out


def _append_item(group: BackgroundTaskGroup, item: BackgroundTaskItem) -> None:
    group.items.append(item)
    group.total += 1
    status = _normalize_status(item.status)
    if status == "running":
        group.running += 1
    elif status == "queued":
        group.queued += 1
    elif status == "succeeded":
        group.succeeded += 1
    elif status == "failed":
        group.failed += 1


def _list_task_creation_job_ids(
    limit: int,
    *,
    job_type: str = "plan_decompose",
) -> List[str]:
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT job_id
                FROM plan_decomposition_job_index
                WHERE job_type=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (job_type, limit),
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "jobs.board: failed to query plan_decomposition_job_index job_type=%s: %s",
            job_type,
            exc,
        )
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


def _lookup_session_plan_id(
    session_id: Optional[str],
    *,
    owner_id: Optional[str] = None,
) -> Optional[int]:
    if not session_id:
        return None
    try:
        with get_db() as conn:
            if owner_id:
                row = conn.execute(
                    "SELECT plan_id FROM chat_sessions WHERE id=? AND owner_id=?",
                    (session_id, owner_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT plan_id FROM chat_sessions WHERE id=?",
                    (session_id,),
                ).fetchone()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("jobs.board: failed to lookup plan_id for session %s: %s", session_id, exc)
        return None
    if not row:
        return None
    value = row["plan_id"]
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _list_task_creation_job_ids_filtered(
    limit: int,
    *,
    owner_id: Optional[str] = None,
    plan_id: Optional[int] = None,
    job_type: str = "plan_decompose",
) -> List[str]:
    if plan_id is None:
        if not owner_id:
            return _list_task_creation_job_ids(limit, job_type=job_type)
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """
                    SELECT job_id
                    FROM plan_decomposition_job_index
                    WHERE job_type=? AND owner_id=?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (job_type, owner_id, limit),
                ).fetchall()
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "jobs.board: failed to query job_type=%s owner_id=%s: %s",
                job_type,
                owner_id,
                exc,
            )
            return []
        return [str(row["job_id"]) for row in rows if row and row["job_id"]]
    try:
        with get_db() as conn:
            if owner_id:
                rows = conn.execute(
                    """
                    SELECT job_id
                    FROM plan_decomposition_job_index
                    WHERE job_type=? AND plan_id=? AND owner_id=?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (job_type, plan_id, owner_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT job_id
                    FROM plan_decomposition_job_index
                    WHERE job_type=? AND plan_id=?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (job_type, plan_id, limit),
                ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "jobs.board: failed to query plan_decomposition_job_index for job_type=%s plan_id=%s: %s",
            job_type,
            plan_id,
            exc,
        )
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


def _list_task_creation_job_ids_by_plan_ids(
    limit: int,
    plan_ids: List[int],
    *,
    owner_id: Optional[str] = None,
    job_type: str = "plan_decompose",
) -> List[str]:
    normalized_ids: List[int] = []
    for pid in plan_ids:
        try:
            value = int(pid)
        except Exception:
            continue
        if value > 0 and value not in normalized_ids:
            normalized_ids.append(value)
    if not normalized_ids:
        return []

    placeholders = ",".join("?" for _ in normalized_ids)
    owner_sql = " AND owner_id = ?" if owner_id else ""
    sql = (
        f"""
        SELECT job_id
        FROM plan_decomposition_job_index
        WHERE job_type=? AND plan_id IN ({placeholders}){owner_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """
    )
    try:
        with get_db() as conn:
            params: List[Any] = [job_type, *normalized_ids]
            if owner_id:
                params.append(owner_id)
            params.append(limit)
            rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "jobs.board: failed to query plan_decomposition_job_index for job_type=%s plan_ids=%s: %s",
            job_type,
            normalized_ids,
            exc,
        )
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


@job_router.get(
    "/board",
    response_model=BackgroundTaskBoardResponse,
    summary="Background job board (Task Creation / PhageScope / Claude Code)",
)
def get_background_task_board(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    session_id: Optional[str] = Query(None),
    plan_id: Optional[int] = Query(None, ge=1),
    include_finished: bool = Query(True),
):
    owner_id = get_request_owner_id(request)
    groups: Dict[str, BackgroundTaskGroup] = {
        "task_creation": _build_group("task_creation", "Task Creation"),
        "phagescope": _build_group("phagescope", "PhageScope"),
        "claude_code": _build_group("claude_code", "Claude Code"),
    }
    seen: set[str] = set()

    # 1) Chat action runs -> classify into phagescope / claude_code
    params: List[Any] = []
    where_parts: List[str] = ["owner_id=?"]
    params.append(owner_id)
    if session_id:
        where_parts.append("session_id=?")
        params.append(session_id)
    if plan_id is not None:
        # Include rows with matching plan_id OR rows with plan_id IS NULL (e.g.
        # PhageScope jobs submitted before a plan was created in the session).
        where_parts.append("(plan_id=? OR plan_id IS NULL)")
        params.append(plan_id)
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    effective_plan_id = (
        plan_id if isinstance(plan_id, int) else _lookup_session_plan_id(session_id, owner_id=owner_id)
    )
    params.append(limit * 4)
    try:
        with get_db() as conn:
            rows = conn.execute(
                f"""
                SELECT id, session_id, user_message, plan_id, status, structured_json,
                       created_at, started_at, finished_at
                FROM chat_action_runs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("jobs.board: failed to query chat_action_runs: %s", exc)
        rows = []

    candidate_plan_ids: List[int] = []
    for row in rows:
        raw_plan_id = row["plan_id"]
        try:
            plan_value = int(raw_plan_id) if raw_plan_id is not None else None
        except Exception:
            plan_value = None
        if isinstance(plan_value, int) and plan_value > 0 and plan_value not in candidate_plan_ids:
            candidate_plan_ids.append(plan_value)

    for row in rows:
        job_id = str(row["id"])
        if job_id in seen:
            continue
        job_payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False) or {}
        actions = _extract_actions_from_structured(row["structured_json"])
        category = _classify_action_run(job_payload, actions)
        if category not in {"phagescope", "claude_code", "task_creation"}:
            continue

        status = str(job_payload.get("status") or row["status"] or "queued")
        if not include_finished and _normalize_status(status) in {"succeeded", "failed"}:
            continue

        label = _extract_first_action_label(actions) or _default_label(category)
        phage_progress: Dict[str, Any] = {}
        if category == "phagescope":
            phage_progress = _extract_phagescope_progress(job_payload)
        item_payload: Dict[str, Any] = {
            "category": category,
            "job_id": job_id,
            "job_type": str(job_payload.get("job_type") or "chat_action"),
            "status": status,
            "label": label,
            "session_id": row["session_id"],
            "plan_id": row["plan_id"],
            "created_at": row["created_at"],
            "started_at": row["started_at"] or job_payload.get("started_at"),
            "finished_at": row["finished_at"] or job_payload.get("finished_at"),
            "error": job_payload.get("error"),
        }
        if phage_progress:
            item_payload.update(phage_progress)
        item_payload.update(
            _extract_item_progress(
                category=category,
                status=status,
                job_payload=job_payload,
                phage_progress=phage_progress,
            )
        )

        _append_item(groups[category], BackgroundTaskItem(**item_payload))
        seen.add(job_id)

    # 2) Explicit task creation jobs from decomposition index
    task_creation_ids: List[str]
    if session_id and effective_plan_id is None:
        task_creation_ids = _list_task_creation_job_ids_by_plan_ids(
            limit * 4,
            candidate_plan_ids,
            owner_id=owner_id,
            job_type="plan_decompose",
        )
    else:
        task_creation_ids = _list_task_creation_job_ids_filtered(
            limit * 4,
            owner_id=owner_id,
            plan_id=effective_plan_id if (session_id or plan_id is not None) else None,
            job_type="plan_decompose",
        )
    for job_id in task_creation_ids:
        if job_id in seen:
            continue
        job_payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
        if not job_payload:
            continue
        if str(job_payload.get("owner_id") or "legacy-local") != owner_id:
            continue

        status = str(job_payload.get("status") or "queued")
        if not include_finished and _normalize_status(status) in {"succeeded", "failed"}:
            continue

        metadata = job_payload.get("metadata") if isinstance(job_payload.get("metadata"), dict) else {}
        label = (
            str(metadata.get("target_task_name") or "").strip()
            or "Task Creation/Decomposition"
        )
        item = BackgroundTaskItem(
            category="task_creation",
            job_id=job_id,
            job_type=str(job_payload.get("job_type") or "plan_decompose"),
            status=status,
            label=label,
            session_id=metadata.get("session_id") if isinstance(metadata.get("session_id"), str) else None,
            plan_id=job_payload.get("plan_id"),
            created_at=job_payload.get("created_at"),
            started_at=job_payload.get("started_at"),
            finished_at=job_payload.get("finished_at"),
            **_extract_item_progress(
                category="task_creation",
                status=status,
                job_payload=job_payload,
            ),
            error=job_payload.get("error"),
        )
        _append_item(groups["task_creation"], item)
        seen.add(job_id)

    # 3) Explicit plan execution jobs from decomposition index.
    plan_execute_ids: List[str]
    if session_id and effective_plan_id is None:
        plan_execute_ids = _list_task_creation_job_ids_by_plan_ids(
            limit * 4,
            candidate_plan_ids,
            owner_id=owner_id,
            job_type="plan_execute",
        )
    else:
        plan_execute_ids = _list_task_creation_job_ids_filtered(
            limit * 4,
            owner_id=owner_id,
            plan_id=effective_plan_id if (session_id or plan_id is not None) else None,
            job_type="plan_execute",
        )
    for job_id in plan_execute_ids:
        if job_id in seen:
            continue
        job_payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
        if not job_payload:
            continue
        if str(job_payload.get("owner_id") or "legacy-local") != owner_id:
            continue

        status = str(job_payload.get("status") or "queued")
        if not include_finished and _normalize_status(status) in {"succeeded", "failed"}:
            continue

        metadata = job_payload.get("metadata") if isinstance(job_payload.get("metadata"), dict) else {}
        target_task_name = str(metadata.get("target_task_name") or "").strip()
        target_task_id = metadata.get("target_task_id")
        label = target_task_name or (
            f"Execute task #{target_task_id}"
            if target_task_id is not None
            else "Plan Task Execution"
        )
        session_value = metadata.get("session_id")
        item = BackgroundTaskItem(
            category="claude_code",
            job_id=job_id,
            job_type=str(job_payload.get("job_type") or "plan_execute"),
            status=status,
            label=label,
            session_id=session_value if isinstance(session_value, str) and session_value.strip() else None,
            plan_id=job_payload.get("plan_id"),
            created_at=job_payload.get("created_at"),
            started_at=job_payload.get("started_at"),
            finished_at=job_payload.get("finished_at"),
            **_extract_item_progress(
                category="claude_code",
                status=status,
                job_payload=job_payload,
            ),
            error=job_payload.get("error"),
        )
        _append_item(groups["claude_code"], item)
        seen.add(job_id)

    # cap each group and total
    for key in ("task_creation", "phagescope", "claude_code"):
        group = groups[key]
        group.items = sorted(
            group.items,
            key=lambda item: item.created_at or "",
            reverse=True,
        )[:limit]
        group.total = len(group.items)
        group.running = sum(1 for item in group.items if _normalize_status(item.status) == "running")
        group.queued = sum(1 for item in group.items if _normalize_status(item.status) == "queued")
        group.succeeded = sum(1 for item in group.items if _normalize_status(item.status) == "succeeded")
        group.failed = sum(1 for item in group.items if _normalize_status(item.status) == "failed")

    total = sum(group.total for group in groups.values())
    generated_at = ""
    try:
        with get_db() as conn:
            now_row = conn.execute("SELECT CURRENT_TIMESTAMP AS ts").fetchone()
            generated_at = now_row["ts"] if now_row and now_row["ts"] else ""
    except Exception:  # pragma: no cover - defensive
        generated_at = ""

    return BackgroundTaskBoardResponse(
        generated_at=generated_at,
        total=total,
        groups=groups,
    )


@job_router.get(
    "/board/stream",
    summary="Stream background task board updates via SSE",
)
async def stream_background_task_board(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    session_id: Optional[str] = Query(None),
    plan_id: Optional[int] = Query(None, ge=1),
    include_finished: bool = Query(True),
):
    """SSE endpoint that pushes board snapshots whenever data changes.

    Sends an initial snapshot immediately, then re-queries every 3 seconds and
    pushes a new snapshot only when the fingerprint changes.  A heartbeat is
    emitted every 15 seconds of inactivity to keep the connection alive.
    """
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    def _build_snapshot() -> Dict[str, Any]:
        """Synchronous board snapshot builder – mirrors get_background_task_board."""
        from fastapi import Request as _Req  # noqa: F401 – not used, just isolation marker
        # Re-use the same sync logic by calling the board function directly.
        # We build the response dict to avoid Pydantic serialization overhead.
        snapshot = get_background_task_board(
            request=request,
            limit=limit,
            session_id=session_id,
            plan_id=plan_id,
            include_finished=include_finished,
        )
        return snapshot.model_dump() if hasattr(snapshot, "model_dump") else snapshot.dict()

    def _fingerprint(snap: Dict[str, Any]) -> str:
        groups = snap.get("groups") or {}
        parts = []
        for key in ("task_creation", "phagescope", "claude_code"):
            items = (groups.get(key) or {}).get("items") or []
            parts.append(f"{key}:{len(items)}:" + ",".join(
                f"{it.get('job_id','')}|{it.get('status','')}|{it.get('progress_percent','')}"
                for it in items
            ))
        return "|".join(parts)

    async def event_generator() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        last_fp = ""
        heartbeat_counter = 0

        try:
            while True:
                # Run sync board query in thread pool to avoid blocking event loop
                snap = await loop.run_in_executor(None, _build_snapshot)
                fp = _fingerprint(snap)

                if fp != last_fp:
                    last_fp = fp
                    heartbeat_counter = 0
                    yield _sse_message({"type": "snapshot", "board": snap})
                else:
                    heartbeat_counter += 1
                    if heartbeat_counter >= 5:  # 5 * 3s = 15s -> heartbeat
                        heartbeat_counter = 0
                        yield _sse_message({"type": "heartbeat"})

                await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@job_router.post(
    "/{job_id}/control",
    response_model=JobControlResponse,
    summary="Control running deep-think execution (pause/resume/skip)",
)
async def control_job_runtime(
    job_id: str,
    request: Request,
    control: JobControlRequest = Body(...),
):
    payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    ensure_owner_access(request, payload.get("owner_id"), detail="job owner mismatch")

    accepted = await route_control_message(
        "job",
        job_id,
        {"type": "job.control", "job_id": job_id, "action": control.action},
    )
    if not accepted:
        accepted = plan_decomposition_jobs.control_runtime(job_id, control.action)
    if not accepted:
        return JobControlResponse(
            success=False,
            job_id=job_id,
            action=control.action,
            status=payload.get("status"),
            message="Runtime control is unavailable for this job at current state.",
        )
    refreshed = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False) or payload
    return JobControlResponse(
        success=True,
        job_id=job_id,
        action=control.action,
        status=refreshed.get("status"),
        message=f"Action '{control.action}' accepted.",
    )


@job_router.get(
    "/{job_id}/stream",
    summary="Stream async job logs in real time",
)
async def stream_job(job_id: str, request: Request):
    snapshot = plan_decomposition_jobs.get_job_payload(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    ensure_owner_access(request, snapshot.get("owner_id"), detail="job owner mismatch")

    bus = await get_realtime_bus()
    subscription: Optional[EventSubscription] = await bus.subscribe_job_events(job_id)
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse_message({"type": "snapshot", "job": snapshot})
            if _normalize_status(snapshot.get("status")) in {"succeeded", "failed"}:
                return
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await subscription.get(timeout=15.0)
                except asyncio.TimeoutError:
                    heartbeat = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
                    if heartbeat is None:
                        break
                    if str(heartbeat.get("owner_id") or "legacy-local") != get_request_owner_id(request):
                        break
                    yield _sse_message({"type": "heartbeat", "job": heartbeat})
                    if _normalize_status(heartbeat.get("status")) in {"succeeded", "failed"}:
                        break
                    continue
                message.setdefault("type", "event")
                yield _sse_message(message)
                if _normalize_status(message.get("status")) in {"succeeded", "failed"}:
                    break
        except asyncio.CancelledError:  # pragma: no cover - defensive
            raise
        finally:
            await subscription.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@job_router.get(
    "/{job_id}",
    response_model=AsyncJobStatusResponse,
    summary="Get async job status",
)
def get_job_status(job_id: str, request: Request):
    payload = plan_decomposition_jobs.get_job_payload(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    ensure_owner_access(request, payload.get("owner_id"), detail="job owner mismatch")
    action_logs = payload.get("action_logs") or []
    action_cursor = payload.get("action_cursor")
    return AsyncJobStatusResponse(
        job_id=payload.get("job_id"),
        job_type=payload.get("job_type") or "plan_decompose",
        status=payload.get("status"),
        plan_id=payload.get("plan_id"),
        task_id=payload.get("task_id"),
        mode=payload.get("mode"),
        result=payload.get("result"),
        stats=payload.get("stats") or {},
        params=payload.get("params") or {},
        metadata=payload.get("metadata") or {},
        error=payload.get("error"),
        created_at=payload.get("created_at"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        logs=payload.get("logs", []),
        action_logs=action_logs,
        action_cursor=action_cursor,
    )


@job_router.get(
    "/{job_id}/logs",
    response_model=JobLogTailResponse,
    summary="Read job log tail",
)
def get_job_logs(job_id: str, tail: int = Query(200, ge=1, le=2000)):
    if not _is_safe_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")

    log_path = _CLAUDE_LOG_DIR / f"{job_id}.log"
    if not log_path.exists():
        fallback = _build_job_log_fallback(job_id, tail)
        if fallback is not None:
            return fallback
        raise HTTPException(status_code=404, detail="Job not found")

    lines, total = _tail_file_lines(log_path, tail)
    return JobLogTailResponse(
        job_id=job_id,
        log_path=str(log_path),
        total_lines=total,
        lines=lines,
        truncated=total > tail,
    )


register_router(
    namespace="jobs",
    version="v1",
    path="/jobs",
    router=job_router,
    tags=["jobs"],
    description="General async job query APIs",
)


__all__ = [
    "job_router",
    "AsyncJobStatusResponse",
]
