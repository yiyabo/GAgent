"""Chat run API: durable SSE event stream with replay and resume."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.repository.chat_runs import (
    create_chat_run,
    fetch_events_after,
    get_chat_run,
    list_session_runs,
)
from app.database import get_db
from app.routers.chat.background import _sse_message
from app.routers.chat.models import ChatRequest
from app.routers.chat.session_helpers import _ensure_session_exists, _save_chat_message
from app.services.chat_run_worker import execute_chat_run
from app.services import chat_run_hub as hub
from app.services.realtime_bus import EventSubscription, get_realtime_bus, route_control_message
from app.services.request_principal import ensure_owner_access, get_request_owner_id

logger = logging.getLogger(__name__)


def new_chat_run_id() -> str:
    return f"dt_{uuid4().hex}"


def _plan_id_from_request(request: ChatRequest) -> Optional[int]:
    ctx = request.context or {}
    raw = ctx.get("plan_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def start_background_chat_run(
    request: ChatRequest,
    *,
    session_id: str,
    owner_id: str,
) -> str:
    """Create DB row, save user message, spawn worker."""
    # chat_runs.session_id FK references chat_sessions; frontend-only sessions must be materialized first.
    plan_id = _plan_id_from_request(request)
    with get_db() as conn:
        _ensure_session_exists(session_id, conn, plan_id, owner_id=owner_id)

    run_id = new_chat_run_id()
    request_json = request.model_dump_json()
    create_chat_run(run_id, session_id, request_json, owner_id=owner_id)
    hub.ensure_cancel_event(run_id)
    hub.ensure_steer_queue(run_id)
    _save_chat_message(session_id, "user", request.message, owner_id=owner_id)
    loop = asyncio.get_running_loop()
    task = loop.create_task(execute_chat_run(run_id))
    hub.register_worker_task(run_id, task)
    return run_id


async def iterate_chat_run_sse(
    request: Request,
    run_id: str,
    *,
    after_seq: int = -1,
) -> AsyncIterator[str]:
    """Replay SQLite, then tail the live fan-out queue, without losing events.

    A naive "replay then subscribe" window can miss events that were persisted
    while the client was between the two steps (those rows never hit the queue
    for this subscriber). We therefore **re-scan the DB whenever it has new
    rows**, and **deduplicate** queue items with ``seq <= last_yielded_seq``.
    """
    last_yielded_seq = after_seq
    q: Optional[EventSubscription] = None
    try:
        while True:
            progressed = False
            for seq, payload in fetch_events_after(run_id, last_yielded_seq):
                progressed = True
                if await request.is_disconnected():
                    return
                last_yielded_seq = seq
                yield hub.format_sse_line(seq, payload)
                if payload.get("type") in ("final", "error"):
                    return

            if q is None:
                bus = await get_realtime_bus()
                q = await bus.subscribe_run_events(run_id)
            if progressed:
                continue

            if await request.is_disconnected():
                return
            try:
                item = await q.get(timeout=1.0)
            except asyncio.TimeoutError:
                continue
            seq, payload = item
            if seq <= last_yielded_seq:
                continue
            if await request.is_disconnected():
                return
            last_yielded_seq = seq
            yield hub.format_sse_line(seq, payload)
            if payload.get("type") in ("final", "error"):
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("run events stream failed run_id=%s", run_id)
        yield _sse_message({"type": "error", "message": str(exc)})
    finally:
        if q is not None:
            await q.close()

async def create_run(request: ChatRequest, raw_request: Request) -> Dict[str, str]:
    if not request.session_id:
        raise HTTPException(status_code=400, detail="session_id is required for chat runs")
    owner_id = get_request_owner_id(raw_request)
    run_id = start_background_chat_run(request, session_id=request.session_id, owner_id=owner_id)
    return {
        "run_id": run_id,
        "events_stream_url": f"/chat/runs/{run_id}/events",
        "session_id": request.session_id,
    }


async def list_runs_for_session(
    session_id: str,
    request: Request,
    status: Optional[str] = Query(None, description="Filter by run status"),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    owner_id = get_request_owner_id(request)
    rows = list_session_runs(session_id, owner_id=owner_id, status=status, limit=limit)
    return {"runs": rows}

async def cancel_run(run_id: str, request: Request) -> Dict[str, str]:
    row = get_chat_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    ensure_owner_access(request, row.get("owner_id"), detail="run owner mismatch")
    accepted = await route_control_message(
        "run",
        run_id,
        {"type": "chat_run.cancel", "run_id": run_id},
    )
    if not accepted:
        hub.request_cancel(run_id)
    return {"run_id": run_id, "status": "cancel_requested"}


async def stream_run_events(
    run_id: str,
    request: Request,
    session_id: str = Query(..., description="Must match the run's session"),
    after_seq: int = Query(-1, description="Replay events with seq greater than this value"),
) -> StreamingResponse:
    row = get_chat_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    ensure_owner_access(request, row.get("owner_id"), detail="run owner mismatch")
    if row["session_id"] != session_id:
        raise HTTPException(status_code=403, detail="session mismatch")

    last_event_header = request.headers.get("last-event-id")
    if last_event_header is not None and last_event_header.strip().isdigit():
        after_seq = max(after_seq, int(last_event_header.strip()))

    async def gen() -> AsyncIterator[str]:
        async for line in iterate_chat_run_sse(request, run_id, after_seq=after_seq):
            yield line

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


def mount_run_routes(router: APIRouter) -> None:
    """Attach run routes on the same ``/chat`` prefix router."""
    router.add_api_route("/runs", create_run, methods=["POST"])
    router.add_api_route(
        "/sessions/{session_id}/runs",
        list_runs_for_session,
        methods=["GET"],
    )
    router.add_api_route(
        "/runs/{run_id}/cancel",
        cancel_run,
        methods=["POST"],
    )
    router.add_api_route(
        "/runs/{run_id}/events",
        stream_run_events,
        methods=["GET"],
    )
    router.add_api_route(
        "/runs/{run_id}/steer",
        steer_run,
        methods=["POST"],
    )


async def steer_run(run_id: str, request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, str]:
    """Push a mid-run user guidance message into a running chat run."""
    row = get_chat_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    ensure_owner_access(request, row.get("owner_id"), detail="run owner mismatch")
    if row["status"] not in ("queued", "running"):
        raise HTTPException(status_code=409, detail="run is not active")

    caller_session = (body.get("session_id") or "").strip()
    if not caller_session:
        raise HTTPException(status_code=400, detail="session_id is required")
    if caller_session != row.get("session_id"):
        raise HTTPException(status_code=403, detail="session mismatch")

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    accepted = await route_control_message(
        "run",
        run_id,
        {"type": "chat_run.steer", "run_id": run_id, "message": message},
    )
    if not accepted:
        accepted = hub.push_steer_message(run_id, message)
    if not accepted:
        raise HTTPException(status_code=409, detail="run has no steer queue (may have ended)")
    return {"run_id": run_id, "status": "accepted"}
