"""Persist chat run events and fan out to live SSE subscribers."""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.repository.chat_runs import append_chat_run_event
from app.services import chat_run_hub as hub

logger = logging.getLogger(__name__)


class ChatRunEmitter:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    async def emit(self, payload: Dict[str, Any]) -> None:
        try:
            seq = append_chat_run_event(self.run_id, payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("chat_run append_event failed run=%s: %s", self.run_id, exc)
            return
        await hub.publish_live_event(self.run_id, seq, payload)
