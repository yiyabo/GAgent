"""In-process fan-out and cancel coordination for chat runs (single-worker MVP)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_subscribers: Dict[str, List[asyncio.Queue[Optional[Tuple[int, Dict[str, Any]]]]]] = {}
_cancel_events: Dict[str, asyncio.Event] = {}
_tasks: Dict[str, asyncio.Task[None]] = {}
_steer_queues: Dict[str, asyncio.Queue[str]] = {}


def ensure_cancel_event(run_id: str) -> asyncio.Event:
    if run_id not in _cancel_events:
        _cancel_events[run_id] = asyncio.Event()
    return _cancel_events[run_id]


def register_worker_task(run_id: str, task: asyncio.Task[None]) -> None:
    _tasks[run_id] = task


def forget_worker_task(run_id: str) -> None:
    _tasks.pop(run_id, None)


def request_cancel(run_id: str) -> None:
    ensure_cancel_event(run_id).set()


async def register_subscriber(
    run_id: str,
) -> asyncio.Queue[Optional[Tuple[int, Dict[str, Any]]]]:
    q: asyncio.Queue[Optional[Tuple[int, Dict[str, Any]]]] = asyncio.Queue()
    async with _lock:
        _subscribers.setdefault(run_id, []).append(q)
    return q


async def unregister_subscriber(
    run_id: str,
    q: asyncio.Queue[Optional[Tuple[int, Dict[str, Any]]]],
) -> None:
    async with _lock:
        subs = _subscribers.get(run_id)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            return
        if not subs:
            _subscribers.pop(run_id, None)


async def publish_live_event(run_id: str, seq: int, payload: Dict[str, Any]) -> None:
    async with _lock:
        subs = list(_subscribers.get(run_id, []))
    for q in subs:
        try:
            await q.put((seq, payload))
        except Exception as exc:  # pragma: no cover
            logger.debug("chat_run publish failed: %s", exc)


async def close_live_subscribers(run_id: str) -> None:
    async with _lock:
        subs = _subscribers.pop(run_id, [])
    for q in subs:
        try:
            await q.put(None)
        except Exception:
            pass


def cleanup_run_signals(run_id: str) -> None:
    _cancel_events.pop(run_id, None)
    _steer_queues.pop(run_id, None)


# ---- Steer (mid-run user guidance) ----------------------------------------

def ensure_steer_queue(run_id: str) -> asyncio.Queue[str]:
    if run_id not in _steer_queues:
        _steer_queues[run_id] = asyncio.Queue()
    return _steer_queues[run_id]


def push_steer_message(run_id: str, message: str) -> bool:
    q = _steer_queues.get(run_id)
    if q is None:
        return False
    q.put_nowait(message)
    return True


def drain_steer_messages(run_id: str) -> List[str]:
    q = _steer_queues.get(run_id)
    if q is None:
        return []
    msgs: List[str] = []
    while not q.empty():
        try:
            msgs.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return msgs


def format_sse_line(seq: int, payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False)
    return f"id: {seq}\ndata: {body}\n\n"
