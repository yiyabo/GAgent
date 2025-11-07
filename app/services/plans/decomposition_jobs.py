from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

from ...repository.plan_storage import (
    append_decomposition_job_log,
    load_decomposition_job,
    lookup_decomposition_job_entry,
    record_decomposition_job,
    register_decomposition_job_index,
    update_decomposition_job_status,
    list_action_logs,
)
from .plan_decomposer import DecompositionResult

if TYPE_CHECKING:  # pragma: no cover
    from .plan_decomposer import PlanDecomposer

MAX_LOG_ENTRIES = 400
DEFAULT_TTL_SECONDS = 600

_job_context: ContextVar[Optional[str]] = ContextVar(
    "plan_decomposition_job_id", default=None
)


def _utc_now() -> datetime:
    return datetime.utcnow()


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.replace(microsecond=int(value.microsecond / 1000) * 1000).isoformat() + "Z"


def _normalize_metadata(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not data:
        return {}
    try:
        json.dumps(data)
        return dict(data)
    except TypeError:
        safe: Dict[str, Any] = {}
        for key, value in data.items():
            try:
                json.dumps({key: value})
            except TypeError:
                safe[key] = repr(value)
            else:
                safe[key] = value
        return safe


def _log_buffer() -> Deque["PlanDecompositionLogEvent"]:
    return deque(maxlen=MAX_LOG_ENTRIES)


def _coerce_result_payload(result: Any) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        try:
            payload = result.model_dump()
            if isinstance(payload, dict):
                return payload
            return dict(payload)
        except Exception:  # pragma: no cover - defensive
            return None
    if isinstance(result, dict):
        return dict(result)
    return None


def _coerce_stats_payload(stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not stats:
        return {}
    try:
        return dict(stats)
    except Exception:  # pragma: no cover - defensive
        return {}


@dataclass
class PlanDecompositionLogEvent:
    timestamp: datetime
    level: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "timestamp": _to_iso(self.timestamp),
            "level": self.level,
            "message": self.message,
            "metadata": dict(self.metadata),
        }


@dataclass
class PlanDecompositionSubscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


@dataclass
class PlanDecompositionJob:
    job_id: str
    plan_id: Optional[int]
    task_id: Optional[int]
    mode: str
    job_type: str = "plan_decompose"
    status: str = "queued"
    created_at: datetime = field(default_factory=_utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[Any] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    logs: Deque[PlanDecompositionLogEvent] = field(default_factory=_log_buffer)
    subscribers: List[PlanDecompositionSubscriber] = field(default_factory=list)
    last_activity_at: datetime = field(default_factory=_utc_now)
    persisted_log_count: int = 0

    def to_payload(self, *, include_logs: bool = True) -> Dict[str, Any]:
        result_payload: Any
        if self.result is None:
            result_payload = None
        else:
            try:
                result_payload = self.result.model_dump()
            except AttributeError:  # pragma: no cover - defensive
                if isinstance(self.result, dict):
                    result_payload = dict(self.result)
                else:
                    result_payload = self.result

        data = {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "mode": self.mode,
            "status": self.status,
            "error": self.error,
            "result": result_payload,
            "stats": dict(self.stats),
            "params": dict(self.params),
            "metadata": dict(self.metadata),
            "created_at": _to_iso(self.created_at),
            "started_at": _to_iso(self.started_at),
            "finished_at": _to_iso(self.finished_at),
        }
        if include_logs:
            data["logs"] = [event.to_payload() for event in self.logs]
        return data

    def append_log(
        self,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PlanDecompositionLogEvent:
        event = PlanDecompositionLogEvent(
            timestamp=_utc_now(),
            level=level,
            message=message,
            metadata=_normalize_metadata(metadata),
        )
        self.logs.append(event)
        self.last_activity_at = event.timestamp
        return event


class PlanDecompositionJobManager:
    """In-memory store tracking asynchronous plan decomposition jobs."""

    def __init__(self, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._jobs: Dict[str, PlanDecompositionJob] = {}
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds

    def create_job(
        self,
        *,
        plan_id: Optional[int],
        task_id: Optional[int],
        mode: str,
        job_type: str = "plan_decompose",
        params: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None,
    ) -> PlanDecompositionJob:
        job_id = job_id or uuid.uuid4().hex
        sanitized_metadata = _normalize_metadata(metadata or {})
        job = PlanDecompositionJob(
            job_id=job_id,
            job_type=job_type,
            plan_id=plan_id,
            task_id=task_id,
            mode=mode,
            params=params or {},
            metadata=sanitized_metadata,
        )
        with self._lock:
            self._cleanup_expired_locked()
            if job_id in self._jobs:
                raise ValueError(f"Job {job_id} already exists.")
            self._jobs[job_id] = job
        if plan_id is not None:
            register_decomposition_job_index(job_id, plan_id, job_type=job_type)
        record_decomposition_job(
            plan_id,
            job_id=job_id,
            job_type=job_type,
            mode=mode,
            target_task_id=task_id,
            status=job.status,
            params=params or {},
            metadata=sanitized_metadata,
        )
        return job

    def get_job(self, job_id: str) -> Optional[PlanDecompositionJob]:
        with self._lock:
            self._cleanup_expired_locked()
            job = self._jobs.get(job_id)
            return job

    def get_job_payload(self, job_id: str, *, include_logs: bool = True) -> Optional[Dict[str, Any]]:
        job = self.get_job(job_id)
        if job is not None:
            payload = job.to_payload(include_logs=include_logs)
        else:
            persisted = self._load_persisted_job(job_id)
            if persisted is None:
                return None
            if not include_logs:
                persisted = dict(persisted)
                persisted.pop("logs", None)
            payload = persisted

        try:
            action_logs, cursor = list_action_logs(
                payload.get("plan_id"),
                job_id=job_id,
                limit=200,
            )
            payload["action_logs"] = action_logs
            payload["action_cursor"] = cursor
        except Exception:  # pragma: no cover - defensive
            pass

        return payload

    def register_subscriber(
        self, job_id: str, loop: asyncio.AbstractEventLoop
    ) -> Optional[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.subscribers.append(PlanDecompositionSubscriber(loop=loop, queue=queue))
        return queue

    def unregister_subscriber(self, job_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.subscribers = [
                subscriber
                for subscriber in job.subscribers
                if subscriber.queue is not queue
            ]

    def mark_running(self, job_id: str) -> None:
        subscribers: List[PlanDecompositionSubscriber] = []
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = _utc_now()
            job.last_activity_at = job.started_at
            subscribers = list(job.subscribers)
        update_decomposition_job_status(
            job.plan_id,
            job_id=job_id,
            status="running",
            started_at=job.started_at,
        )
        self._notify_subscribers(
            subscribers,
            {
                "job_id": job_id,
                "job_type": job.job_type,
                "status": "running",
                "event": None,
                "stats": {},
                "metadata": dict(job.metadata),
            },
        )

    def mark_success(
        self,
        job_id: str,
        *,
        result: Any,
        stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        subscribers: List[PlanDecompositionSubscriber] = []
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "succeeded"
            job.finished_at = _utc_now()
            job.result = result
            if stats is not None:
                job.stats = _coerce_stats_payload(stats)
            job.last_activity_at = job.finished_at
            subscribers = list(job.subscribers)
        result_payload = _coerce_result_payload(job.result)
        stats_payload = _coerce_stats_payload(job.stats)
        update_decomposition_job_status(
            job.plan_id,
            job_id=job_id,
            status="succeeded",
            finished_at=job.finished_at,
            stats=stats_payload,
            result=result_payload,
        )
        payload = {
            "job_id": job_id,
            "job_type": job.job_type,
            "status": "succeeded",
            "event": None,
            "stats": stats_payload,
            "result": result_payload,
            "metadata": dict(job.metadata),
        }
        self._notify_subscribers(subscribers, payload)

    def mark_failure(
        self,
        job_id: str,
        error: str,
        *,
        result: Any = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        subscribers: List[PlanDecompositionSubscriber] = []
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.error = error
            job.finished_at = _utc_now()
            if result is not None:
                job.result = result
            if stats is not None:
                job.stats = _coerce_stats_payload(stats)
            job.last_activity_at = job.finished_at
            subscribers = list(job.subscribers)
        stats_payload = _coerce_stats_payload(job.stats)
        result_payload = _coerce_result_payload(job.result)
        update_decomposition_job_status(
            job.plan_id,
            job_id=job_id,
            status="failed",
            error=error,
            finished_at=job.finished_at,
            stats=stats_payload,
            result=result_payload,
        )
        payload = {
            "job_id": job_id,
            "job_type": job.job_type,
            "status": "failed",
            "event": None,
            "error": error,
            "stats": stats_payload,
            "result": result_payload,
            "metadata": dict(job.metadata),
        }
        self._notify_subscribers(subscribers, payload)

    def append_log(
        self,
        job_id: str,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        subscribers: List[PlanDecompositionSubscriber] = []
        payload: Dict[str, Any]
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            event = job.append_log(level=level, message=message, metadata=metadata)
            subscribers = list(job.subscribers)
            payload = {
                "job_id": job_id,
                "job_type": job.job_type,
                "status": job.status,
                "event": event.to_payload(),
                "stats": dict(job.stats),
                "metadata": dict(job.metadata),
            }
            append_decomposition_job_log(
                job.plan_id,
                job_id=job_id,
                timestamp=event.timestamp,
                level=level,
                message=message,
                metadata=metadata,
            )
            job.persisted_log_count += 1
        self._notify_subscribers(subscribers, payload)

    def log_from_context(
        self,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        job_id = _job_context.get()
        if not job_id:
            return
        self.append_log(job_id, level, message, metadata=metadata)

    def cleanup(self) -> None:
        with self._lock:
            self._cleanup_expired_locked()

    def attach_plan(self, job_id: str, plan_id: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.plan_id = plan_id
            register_decomposition_job_index(
                job_id, plan_id, job_type=job.job_type
            )
            record_decomposition_job(
                plan_id,
                job_id=job_id,
                job_type=job.job_type,
                mode=job.mode,
                target_task_id=job.task_id,
                status=job.status,
                params=job.params,
                metadata=job.metadata,
            )
            update_decomposition_job_status(
                plan_id,
                job_id=job_id,
                status=job.status,
                error=job.error,
                started_at=job.started_at,
                finished_at=job.finished_at,
                stats=_coerce_stats_payload(job.stats),
                result=_coerce_result_payload(job.result),
            )
            pending_logs = list(job.logs)[job.persisted_log_count :]
            for event in pending_logs:
                append_decomposition_job_log(
                    plan_id,
                    job_id=job_id,
                    timestamp=event.timestamp,
                    level=event.level,
                    message=event.message,
                    metadata=event.metadata,
                )
                job.persisted_log_count += 1

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify_subscribers(
        self,
        subscribers: List[PlanDecompositionSubscriber],
        payload: Dict[str, Any],
    ) -> None:
        if not subscribers:
            return
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(
                    subscriber.queue.put_nowait, payload
                )
            except RuntimeError:
                # loop closed; ignore
                continue

    def _cleanup_expired_locked(self) -> None:
        if not self._jobs:
            return
        now = _utc_now()
        threshold = timedelta(seconds=self._ttl_seconds)
        expired: List[str] = []
        for job_id, job in self._jobs.items():
            if job.finished_at and now - job.finished_at > threshold:
                expired.append(job_id)
        for job_id in expired:
            self._jobs.pop(job_id, None)

    def _load_persisted_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        entry = lookup_decomposition_job_entry(job_id)
        plan_id = entry.get("plan_id") if entry else None
        record = load_decomposition_job(plan_id, job_id)
        if record is None and plan_id is None:
            # planless job may reside in系统数据库
            record = load_decomposition_job(None, job_id)
        if record is None:
            return None
        return {
            "job_id": record.get("job_id"),
            "job_type": record.get("job_type") or "plan_decompose",
            "plan_id": record.get("plan_id"),
            "task_id": record.get("target_task_id"),
            "mode": record.get("mode"),
            "status": record.get("status"),
            "error": record.get("error"),
            "result": record.get("result"),
            "stats": record.get("stats") or {},
            "params": record.get("params") or {},
            "metadata": record.get("metadata") or {},
            "created_at": record.get("created_at"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "logs": record.get("logs", []),
        }


plan_decomposition_jobs = PlanDecompositionJobManager()


def set_current_job(job_id: Optional[str]) -> Any:
    return _job_context.set(job_id)


def reset_current_job(token: Any) -> None:
    try:
        _job_context.reset(token)
    except Exception:  # pragma: no cover - defensive
        pass


def get_current_job() -> Optional[str]:
    return _job_context.get()


def log_job_event(
    level: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    plan_decomposition_jobs.log_from_context(level, message, metadata=metadata)


def execute_decomposition_job(
    *,
    plan_decomposer: "PlanDecomposer",
    job_id: str,
    plan_id: int,
    mode: str,
    task_id: Optional[int] = None,
    max_depth: Optional[int] = None,
    expand_depth: Optional[int] = None,
    node_budget: Optional[int] = None,
    allow_existing_children: Optional[bool] = None,
) -> None:
    token = set_current_job(job_id)
    try:
        plan_decomposition_jobs.mark_running(job_id)
        if mode == "plan_bfs":
            log_job_event(
                "info",
                "开始整体计划分解",
                {
                    "plan_id": plan_id,
                    "max_depth": max_depth,
                    "node_budget": node_budget,
                },
            )
            result = plan_decomposer.run_plan(
                plan_id,
                max_depth=max_depth,
                node_budget=node_budget,
            )
        else:
            log_job_event(
                "info",
                "开始节点分解",
                {
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "expand_depth": expand_depth,
                    "node_budget": node_budget,
                    "allow_existing_children": allow_existing_children,
                },
            )
            if task_id is None:
                raise ValueError("task_id 必须在单节点分解模式下提供。")
            result = plan_decomposer.decompose_node(
                plan_id,
                task_id,
                expand_depth=expand_depth,
                node_budget=node_budget,
                allow_existing_children=allow_existing_children,
            )
        log_job_event(
            "info",
            "任务拆分完成",
            {
                "created_tasks": len(result.created_tasks),
                "stopped_reason": result.stopped_reason,
            },
        )
        plan_decomposition_jobs.mark_success(
            job_id,
            result=result,
            stats=result.stats,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log_job_event(
            "error",
            "任务拆分失败",
            {"error": str(exc)},
        )
        plan_decomposition_jobs.mark_failure(job_id, str(exc))
    finally:
        reset_current_job(token)


def start_decomposition_job_thread(
    plan_decomposer: "PlanDecomposer",
    *,
    plan_id: int,
    mode: str,
    task_id: Optional[int] = None,
    max_depth: Optional[int] = None,
    expand_depth: Optional[int] = None,
    node_budget: Optional[int] = None,
    allow_existing_children: Optional[bool] = None,
) -> PlanDecompositionJob:
    params = {
        "mode": mode,
        "plan_id": plan_id,
        "task_id": task_id,
        "max_depth": max_depth,
        "expand_depth": expand_depth,
        "node_budget": node_budget,
        "allow_existing_children": allow_existing_children,
    }
    job = plan_decomposition_jobs.create_job(
        plan_id=plan_id,
        task_id=task_id,
        mode=mode,
        params={k: v for k, v in params.items() if v is not None},
    )
    plan_decomposition_jobs.append_log(
        job.job_id,
        "info",
        "任务已加入后台队列",
        {
            "plan_id": plan_id,
            "task_id": task_id,
            "mode": mode,
        },
    )

    thread = threading.Thread(
        target=execute_decomposition_job,
        kwargs={
            "plan_decomposer": plan_decomposer,
            "job_id": job.job_id,
            "plan_id": plan_id,
            "mode": mode,
            "task_id": task_id,
            "max_depth": max_depth,
            "expand_depth": expand_depth,
            "node_budget": node_budget,
            "allow_existing_children": allow_existing_children,
        },
        daemon=True,
    )
    thread.start()
    return job


__all__ = [
    "PlanDecompositionJob",
    "PlanDecompositionJobManager",
    "PlanDecompositionLogEvent",
    "PlanDecompositionSubscriber",
    "plan_decomposition_jobs",
    "log_job_event",
    "execute_decomposition_job",
    "start_decomposition_job_thread",
    "set_current_job",
    "reset_current_job",
    "get_current_job",
]
