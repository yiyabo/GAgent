"""Chat run API: durable SSE event stream with replay and resume."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.repository.chat_runs import (
    create_chat_run,
    fetch_events_after,
    get_chat_run,
    list_session_runs,
)
from app.routers.chat.background import _sse_message
from app.routers.chat.models import ChatRequest
from app.routers.chat.session_helpers import _save_chat_message
from app.services.chat_run_worker import execute_chat_run
from app.services import chat_run_hub as hub

logger = logging.getLogger(__name__)


def new_chat_run_id() -> str:
    return f"dt_{uuid4().hex}"


def start_background_chat_run(request: ChatRequest, *, session_id: str) -> str:
    """Create DB row, save user message, spawn worker."""
    run_id = new_chat_run_id()
    request_json = request.model_dump_json()
    create_chat_run(run_id, session_id, request_json)
    hub.ensure_cancel_event(run_id)
    _save_chat_message(session_id, "user", request.message)
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
    q: Optional[asyncio.Queue] = None
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
                q = await hub.register_subscriber(run_id)
            if progressed:
                continue

            if await request.is_disconnected():
                return
            item = await q.get()
            if item is None:
                return
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
            await hub.unregister_subscriber(run_id, q)


async def create_run(request: ChatRequest) -> Dict[str, str]:
    if not request.session_id:
        raise HTTPException(status_code=400, detail="session_id is required for chat runs")
    run_id = start_background_chat_run(request, session_id=request.session_id)
    return {
        "run_id": run_id,
        "events_stream_url": f"/chat/runs/{run_id}/events",
        "session_id": request.session_id,
    }


async def list_runs_for_session(
    session_id: str,
    status: Optional[str] = Query(None, description="Filter by run status"),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    rows = list_session_runs(session_id, status=status, limit=limit)
    return {"runs": rows}


async def cancel_run(run_id: str) -> Dict[str, str]:
    row = get_chat_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
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
