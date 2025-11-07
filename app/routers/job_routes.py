from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.plans.decomposition_jobs import plan_decomposition_jobs
from . import register_router

job_router = APIRouter(prefix="/jobs", tags=["jobs"])


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
