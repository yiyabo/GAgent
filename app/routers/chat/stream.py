"""Streaming chat endpoint implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.services.moderation import scan_user_input
from app.services.platform_access import bind_chat_request_to_principal
from app.services.request_principal import get_request_owner_id, get_request_principal

from .background import _sse_message
from .models import ChatRequest
from .run_routes import iterate_chat_run_sse, start_background_chat_run
from .stream_context import build_agent_for_chat_request

logger = logging.getLogger(__name__)


async def _sse_with_keepalive(
    source: AsyncIterator[str], idle_seconds: float = 5.0
) -> AsyncIterator[str]:
    """Yield SSE comment lines while the source is idle.

    Pre-stream work (routing, title generation, uploads) can be slow; without
    traffic, intermediate gateways cut idle connections before the first real
    event, which surfaced as disconnect-and-sync messages in the UI.
    """
    queue: asyncio.Queue = asyncio.Queue()
    done_sentinel: object = object()

    async def _pump() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(done_sentinel)

    pump_task = asyncio.create_task(_pump())
    try:
        while True:
            getter = asyncio.create_task(queue.get())
            sleeper = asyncio.create_task(asyncio.sleep(idle_seconds))
            done, _ = await asyncio.wait(
                {getter, sleeper}, return_when=asyncio.FIRST_COMPLETED
            )
            sleeper.cancel()
            if getter in done:
                item = getter.result()
                if item is done_sentinel:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
            else:
                getter.cancel()
                yield ": keepalive\n\n"
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except BaseException:  # pragma: no cover - best-effort cleanup
            pass


async def chat_stream(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
):
    _ = background_tasks

    logger.info(
        "[CHAT][STREAM] POST received session=%s msg_len=%d",
        request.session_id,
        len(request.message or ""),
    )

    # Log-only moderation audit of the raw user message (never blocks)
    scan_user_input(request.message, session_id=request.session_id)

    if request.session_id:
        request = bind_chat_request_to_principal(raw_request, request)
        owner_id = get_request_owner_id(raw_request)
        run_id = start_background_chat_run(
            request,
            session_id=request.session_id,
            owner_id=owner_id,
        )
        http = raw_request

        async def event_generator() -> AsyncIterator[str]:
            async for line in _sse_with_keepalive(
                iterate_chat_run_sse(http, run_id, after_seq=-1)
            ):
                yield line

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
        return StreamingResponse(
            event_generator(), media_type="text/event-stream", headers=headers
        )

    principal = get_request_principal(raw_request)
    if principal.is_authenticated and principal.is_platform_access:
        raise HTTPException(status_code=403, detail="Platform SSO sessions require a chat session id")

    async def event_generator_legacy() -> AsyncIterator[str]:
        try:
            agent, message_to_send = await build_agent_for_chat_request(
                request, save_user_message=True
            )
            yield _sse_message({"type": "start"})
            agent._current_user_message = message_to_send
            log_ctx = "plan-bound" if agent.plan_session.plan_id is not None else "no-plan"
            logger.info("[CHAT] Unified agent stream (%s, legacy no-session)", log_ctx)
            async for chunk in _sse_with_keepalive(
                agent.process_unified_stream(message_to_send)
            ):
                yield chunk
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Chat streaming failed: %s", exc)
            yield _sse_message(
                {
                    "type": "error",
                    "message": "⚠️ Streaming request failed. Please try again.",
                    "error_type": type(exc).__name__,
                }
            )

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        event_generator_legacy(), media_type="text/event-stream", headers=headers
    )
