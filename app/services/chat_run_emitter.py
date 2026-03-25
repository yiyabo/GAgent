"""Persist chat run events and fan out to live SSE subscribers.

The emitter supports *micro-batching*: high-frequency token-level events
(``delta``, ``thinking_delta``, ``reasoning_delta``, ``tool_output``) are
buffered for a short window (default 80 ms) and flushed to SQLite in a
single transaction, reducing write overhead by 5-20×.

Critical lifecycle events (``start``, ``final``, ``error``,
``thinking_step``, ``control_ack``, ``steer_ack``, ``artifact``) are
persisted **immediately** so they are never delayed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.repository.chat_runs import append_chat_run_event, batch_append_chat_run_events
from app.services import chat_run_hub as hub
from app.services.realtime_bus import get_realtime_bus

logger = logging.getLogger(__name__)

# Event types that are flushed immediately (low-frequency, high-importance).
_IMMEDIATE_EVENT_TYPES = frozenset({
    "start",
    "final",
    "error",
    "thinking_step",
    "control_ack",
    "steer_ack",
    "artifact",
    "job_update",
})

# Default micro-batch flush interval in seconds.
_FLUSH_INTERVAL_S = 0.08  # 80 ms


class ChatRunEmitter:
    """Emit SSE events with micro-batching for high-frequency deltas."""

    def __init__(self, run_id: str, *, flush_interval: float = _FLUSH_INTERVAL_S) -> None:
        self.run_id = run_id
        self._flush_interval = flush_interval

        # Buffered payloads awaiting batch write.
        self._buffer: List[Dict[str, Any]] = []
        self._flush_task: Optional[asyncio.Task[None]] = None
        self._closed = False

    async def emit(self, payload: Dict[str, Any]) -> None:
        """Emit a single event.

        Immediate events are persisted and fan-out right away.
        High-frequency events are buffered and flushed periodically.
        """
        event_type = str(payload.get("type") or "unknown")

        if event_type in _IMMEDIATE_EVENT_TYPES:
            # Flush any pending buffer first so ordering is preserved.
            if self._buffer:
                await self._flush_buffer()
            try:
                seq = append_chat_run_event(self.run_id, payload)
            except Exception as exc:  # pragma: no cover
                logger.warning("chat_run append_event failed run=%s: %s", self.run_id, exc)
                return
            try:
                bus = await get_realtime_bus()
                await bus.publish_run_event(self.run_id, seq, payload)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("chat_run publish_run_event failed run=%s seq=%s: %s", self.run_id, seq, exc)
            return

        # Buffer high-frequency events.
        self._buffer.append(payload)
        self._schedule_flush()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _schedule_flush(self) -> None:
        """Ensure a delayed flush task is running."""
        if self._flush_task is not None and not self._flush_task.done():
            return  # already scheduled
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — fall back to synchronous flush.
            asyncio.ensure_future(self._flush_buffer())
            return
        self._flush_task = loop.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        """Wait for the flush interval then flush."""
        await asyncio.sleep(self._flush_interval)
        await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Persist all buffered events in a single transaction and fan-out."""
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()

        try:
            seqs = batch_append_chat_run_events(self.run_id, batch)
        except Exception as exc:
            # Restore the batch to the front of the buffer so events are not
            # permanently lost.  The next flush cycle (or an immediate event
            # via emit()) will retry them.
            self._buffer = batch + self._buffer
            logger.warning(
                "chat_run batch_append failed run=%s count=%d (events restored to buffer): %s",
                self.run_id, len(batch), exc,
            )
            return

        # Fan out each event to live SSE subscribers with correct seq.
        for seq, payload in zip(seqs, batch):
            try:
                bus = await get_realtime_bus()
                await bus.publish_run_event(self.run_id, seq, payload)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("chat_run publish_run_event failed run=%s seq=%s: %s", self.run_id, seq, exc)
