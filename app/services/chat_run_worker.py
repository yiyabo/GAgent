"""Background execution of a chat run (decoupled from HTTP)."""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.repository.chat_runs import get_chat_run, mark_chat_run_finished, mark_chat_run_started
from app.routers.chat.models import ChatRequest
from app.routers.chat.stream_context import build_agent_for_chat_request
from app.services.chat_run_emitter import ChatRunEmitter
from app.services import chat_run_hub as hub

logger = logging.getLogger(__name__)


async def execute_chat_run(run_id: str) -> None:
    cancel_ev = hub.ensure_cancel_event(run_id)
    hub.ensure_steer_queue(run_id)
    emitter = ChatRunEmitter(run_id)
    try:
        row = get_chat_run(run_id)
        if not row:
            logger.warning("chat_run missing run_id=%s", run_id)
            return
        raw = row.get("request_json")
        if not raw:
            mark_chat_run_finished(run_id, "failed", error="missing request_json")
            return
        data = json.loads(raw)
        request = ChatRequest.model_validate(data)

        mark_chat_run_started(run_id)
        await emitter.emit({"type": "start", "run_id": run_id})

        agent, message_to_send = build_agent_for_chat_request(
            request, save_user_message=False
        )
        agent._current_user_message = message_to_send

        async for _chunk in agent.process_unified_stream(
            message_to_send,
            run_id=run_id,
            cancel_event=cancel_ev,
            event_sink=emitter.emit,
            steer_drain=lambda: hub.drain_steer_messages(run_id),
        ):
            pass

        if cancel_ev.is_set():
            mark_chat_run_finished(run_id, "cancelled", error="cancelled")
        else:
            mark_chat_run_finished(run_id, "succeeded")
    except Exception as exc:
        logger.exception("chat_run worker failed run_id=%s", run_id)
        try:
            await emitter.emit(
                {
                    "type": "error",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
        except Exception:
            pass
        mark_chat_run_finished(run_id, "failed", error=str(exc))
    finally:
        try:
            await hub.close_live_subscribers(run_id)
        except Exception:
            pass
        hub.forget_worker_task(run_id)
        hub.cleanup_run_signals(run_id)
