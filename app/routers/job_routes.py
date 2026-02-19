from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.database import get_db
from app.services.plans.decomposition_jobs import plan_decomposition_jobs
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
        return f"调用 {name}"
    if name:
        return name
    return None


def _default_label(category: str) -> str:
    if category == "task_creation":
        return "任务创建"
    if category == "phagescope":
        return "PhageScope 任务"
    if category == "claude_code":
        return "Claude Code 执行"
    return "后台任务"


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


def _list_task_creation_job_ids(limit: int) -> List[str]:
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT job_id
                FROM plan_decomposition_job_index
                WHERE job_type='plan_decompose'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("jobs.board: failed to query plan_decomposition_job_index: %s", exc)
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


def _lookup_session_plan_id(session_id: Optional[str]) -> Optional[int]:
    if not session_id:
        return None
    try:
        with get_db() as conn:
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


def _list_task_creation_job_ids_filtered(limit: int, *, plan_id: Optional[int] = None) -> List[str]:
    if plan_id is None:
        return _list_task_creation_job_ids(limit)
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT job_id
                FROM plan_decomposition_job_index
                WHERE job_type='plan_decompose' AND plan_id=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (plan_id, limit),
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "jobs.board: failed to query plan_decomposition_job_index for plan_id=%s: %s",
            plan_id,
            exc,
        )
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


def _list_task_creation_job_ids_by_plan_ids(limit: int, plan_ids: List[int]) -> List[str]:
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
    sql = (
        f"""
        SELECT job_id
        FROM plan_decomposition_job_index
        WHERE job_type='plan_decompose' AND plan_id IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT ?
        """
    )
    try:
        with get_db() as conn:
            rows = conn.execute(sql, (*normalized_ids, limit)).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "jobs.board: failed to query plan_decomposition_job_index for plan_ids=%s: %s",
            normalized_ids,
            exc,
        )
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


@job_router.get(
    "/board",
    response_model=BackgroundTaskBoardResponse,
    summary="后台任务看板（任务创建 / PhageScope / Claude Code）",
)
def get_background_task_board(
    limit: int = Query(50, ge=1, le=500),
    session_id: Optional[str] = Query(None),
    plan_id: Optional[int] = Query(None, ge=1),
    include_finished: bool = Query(True),
):
    groups: Dict[str, BackgroundTaskGroup] = {
        "task_creation": _build_group("task_creation", "任务创建"),
        "phagescope": _build_group("phagescope", "PhageScope"),
        "claude_code": _build_group("claude_code", "Claude Code"),
    }
    seen: set[str] = set()

    # 1) Chat action runs -> classify into phagescope / claude_code
    params: List[Any] = []
    where_parts: List[str] = []
    if session_id:
        where_parts.append("session_id=?")
        params.append(session_id)
    if plan_id is not None:
        # Include rows with matching plan_id OR rows with plan_id IS NULL (e.g.
        # PhageScope jobs submitted before a plan was created in the session).
        where_parts.append("(plan_id=? OR plan_id IS NULL)")
        params.append(plan_id)
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    effective_plan_id = plan_id if isinstance(plan_id, int) else _lookup_session_plan_id(session_id)
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
        if category == "phagescope":
            item_payload.update(_extract_phagescope_progress(job_payload))

        _append_item(groups[category], BackgroundTaskItem(**item_payload))
        seen.add(job_id)

    # 2) Explicit task creation jobs from decomposition index
    task_creation_ids: List[str]
    if session_id and effective_plan_id is None:
        task_creation_ids = _list_task_creation_job_ids_by_plan_ids(
            limit * 4,
            candidate_plan_ids,
        )
    else:
        task_creation_ids = _list_task_creation_job_ids_filtered(
            limit * 4,
            plan_id=effective_plan_id if (session_id or plan_id is not None) else None,
        )
    for job_id in task_creation_ids:
        if job_id in seen:
            continue
        job_payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
        if not job_payload:
            continue

        status = str(job_payload.get("status") or "queued")
        if not include_finished and _normalize_status(status) in {"succeeded", "failed"}:
            continue

        metadata = job_payload.get("metadata") if isinstance(job_payload.get("metadata"), dict) else {}
        label = (
            str(metadata.get("target_task_name") or "").strip()
            or "任务创建/拆分"
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
            error=job_payload.get("error"),
        )
        _append_item(groups["task_creation"], item)
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
    "/{job_id}/stream",
    summary="实时订阅异步 Job 日志",
)
async def stream_job(job_id: str):
    snapshot = plan_decomposition_jobs.get_job_payload(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="未找到对应的 Job。")

    loop = asyncio.get_running_loop()
    queue = plan_decomposition_jobs.register_subscriber(job_id, loop)
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    if queue is None:
        async def snapshot_only() -> AsyncIterator[str]:
            yield _sse_message({"type": "snapshot", "job": snapshot})

        return StreamingResponse(snapshot_only(), media_type="text/event-stream", headers=headers)

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse_message({"type": "snapshot", "job": snapshot})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    heartbeat = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
                    if heartbeat is None:
                        break
                    yield _sse_message({"type": "heartbeat", "job": heartbeat})
                    continue
                message.setdefault("type", "event")
                yield _sse_message(message)
                if message.get("status") in {"succeeded", "failed"}:
                    break
        except asyncio.CancelledError:  # pragma: no cover - defensive
            raise
        finally:
            plan_decomposition_jobs.unregister_subscriber(job_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@job_router.get(
    "/{job_id}",
    response_model=AsyncJobStatusResponse,
    summary="查询异步 Job 状态",
)
def get_job_status(job_id: str):
    payload = plan_decomposition_jobs.get_job_payload(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="未找到对应的 Job。")
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
    summary="读取 Claude Code 日志尾部",
)
def get_job_logs(job_id: str, tail: int = Query(200, ge=1, le=2000)):
    if not _is_safe_job_id(job_id):
        raise HTTPException(status_code=400, detail="非法的 job_id")

    log_path = _CLAUDE_LOG_DIR / f"{job_id}.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="日志不存在或尚未生成")

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
    description="通用异步 Job 查询接口",
)


__all__ = [
    "job_router",
    "AsyncJobStatusResponse",
]
