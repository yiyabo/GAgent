"""Streaming chat endpoint implementation."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import BackgroundTasks, Request
from fastapi.responses import StreamingResponse

from .background import _sse_message
from .models import ChatRequest
from .run_routes import iterate_chat_run_sse, start_background_chat_run
from .stream_context import build_agent_for_chat_request

logger = logging.getLogger(__name__)


async def chat_stream(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
):
    _ = background_tasks

    if request.session_id:
        run_id = start_background_chat_run(request, session_id=request.session_id)
        http = raw_request

        async def event_generator() -> AsyncIterator[str]:
            async for line in iterate_chat_run_sse(http, run_id, after_seq=-1):
                yield line

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
        return StreamingResponse(
            event_generator(), media_type="text/event-stream", headers=headers
        )

    async def event_generator_legacy() -> AsyncIterator[str]:
        try:
            agent, message_to_send = build_agent_for_chat_request(
                request, save_user_message=True
            )
            yield _sse_message({"type": "start"})
            agent._current_user_message = message_to_send
            log_ctx = "plan-bound" if agent.plan_session.plan_id is not None else "no-plan"
            logger.info("[CHAT] Unified agent stream (%s, legacy no-session)", log_ctx)
            async for chunk in agent.process_unified_stream(message_to_send):
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
