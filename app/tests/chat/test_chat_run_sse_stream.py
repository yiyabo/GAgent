"""Targeted tests for the chat-run SSE job event stream (W0 gap).

`iterate_chat_run_sse` is the durable SSE channel for chat runs: it replays
persisted events from SQLite, then tails the live fan-out queue, deduplicating
by ``seq`` and terminating on ``final``/``error``.  The only pre-existing SSE
job-stream test covers the plan decomposition endpoint
(``test_plan_job_streams.py``); the chat run channel itself had no direct
coverage of its replay/live-tail contract or its wire shape
(``id: <seq>\\ndata: <json>\\n\\n`` per ``chat_run_hub.format_sse_line``).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import app.routers.chat.run_routes as run_routes
from app.repository import chat_runs as cr
from app.routers.chat.run_routes import iterate_chat_run_sse

RUN_ID = "run_sse_gap"

_SCHEMA = """
CREATE TABLE chat_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    owner_id TEXT NOT NULL DEFAULT 'legacy-local',
    status TEXT NOT NULL DEFAULT 'queued',
    user_message_id INTEGER,
    assistant_message_id INTEGER,
    idempotency_key TEXT,
    error TEXT,
    request_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    last_event_seq INTEGER NOT NULL DEFAULT -1
);
CREATE TABLE chat_run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, seq)
);
"""


@pytest.fixture()
def run_db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO chat_runs (run_id, session_id) VALUES (?, ?)", (RUN_ID, "sess_sse"))
    conn.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    yield conn
    conn.close()


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


class _FakeSubscription:
    def __init__(self, items: List[Tuple[int, Dict[str, Any]]]) -> None:
        self._items = list(items)
        self.closed = False

    async def get(self, timeout: float) -> Tuple[int, Dict[str, Any]]:
        if not self._items:
            raise asyncio.TimeoutError
        return self._items.pop(0)

    async def close(self) -> None:
        self.closed = True


class _FakeBus:
    def __init__(self, subscription: _FakeSubscription) -> None:
        self._subscription = subscription
        self.subscribed_run_ids: List[str] = []

    async def subscribe_run_events(self, run_id: str) -> _FakeSubscription:
        self.subscribed_run_ids.append(run_id)
        return self._subscription


def _install_fake_bus(monkeypatch: pytest.MonkeyPatch, bus: _FakeBus) -> None:
    async def _get_bus() -> _FakeBus:
        return bus

    monkeypatch.setattr(run_routes, "get_realtime_bus", _get_bus)


def _parse_sse_line(line: str) -> Tuple[int, Dict[str, Any]]:
    assert line.endswith("\n\n"), f"SSE line must end with blank line: {line!r}"
    header, _, data = line.partition("\n")
    assert header.startswith("id: ")
    assert data.startswith("data: ")
    return int(header[len("id: "):]), json.loads(data[len("data: "):])


async def test_chat_run_sse_replays_job_lifecycle_and_stops_on_final(run_db, monkeypatch) -> None:
    _install_fake_bus(monkeypatch, _FakeBus(_FakeSubscription([])))
    cr.append_chat_run_event(RUN_ID, {"type": "start"})
    cr.append_chat_run_event(RUN_ID, {"type": "job_update", "payload": {"job_id": "j1", "status": "created"}})
    cr.append_chat_run_event(RUN_ID, {"type": "job_update", "payload": {"job_id": "j1", "status": "running"}})
    cr.append_chat_run_event(RUN_ID, {"type": "job_update", "payload": {"job_id": "j1", "status": "completed"}})
    cr.append_chat_run_event(RUN_ID, {"type": "final", "payload": {"response": "done", "metadata": {}}})

    lines = [line async for line in iterate_chat_run_sse(_FakeRequest(), RUN_ID)]

    assert len(lines) == 5
    parsed = [_parse_sse_line(line) for line in lines]
    assert [seq for seq, _ in parsed] == [0, 1, 2, 3, 4]
    types = [payload.get("type") for _, payload in parsed]
    assert types == ["start", "job_update", "job_update", "job_update", "final"]
    job_statuses = [payload["payload"]["status"] for _, payload in parsed if payload.get("type") == "job_update"]
    assert job_statuses == ["created", "running", "completed"]
    # Terminal event must be the last thing on the wire.
    assert parsed[-1][1]["payload"]["response"] == "done"


async def test_chat_run_sse_tails_live_queue_with_seq_dedupe(run_db, monkeypatch) -> None:
    cr.append_chat_run_event(RUN_ID, {"type": "start"})
    subscription = _FakeSubscription(
        [
            (0, {"type": "start"}),  # stale duplicate already replayed from SQLite
            (1, {"type": "job_update", "payload": {"job_id": "j1", "status": "running"}}),
            (2, {"type": "final", "payload": {"response": "ok", "metadata": {}}}),
        ]
    )
    bus = _FakeBus(subscription)
    _install_fake_bus(monkeypatch, bus)

    lines = [line async for line in iterate_chat_run_sse(_FakeRequest(), RUN_ID)]

    parsed = [_parse_sse_line(line) for line in lines]
    assert [seq for seq, _ in parsed] == [0, 1, 2]
    assert [payload.get("type") for _, payload in parsed] == ["start", "job_update", "final"]
    assert bus.subscribed_run_ids == [RUN_ID]
    assert subscription.closed is True


async def test_chat_run_sse_resumes_after_seq(run_db, monkeypatch) -> None:
    _install_fake_bus(monkeypatch, _FakeBus(_FakeSubscription([])))
    cr.append_chat_run_event(RUN_ID, {"type": "start"})
    cr.append_chat_run_event(RUN_ID, {"type": "job_update", "payload": {"job_id": "j1", "status": "running"}})
    cr.append_chat_run_event(RUN_ID, {"type": "final", "payload": {"response": "done", "metadata": {}}})

    lines = [line async for line in iterate_chat_run_sse(_FakeRequest(), RUN_ID, after_seq=0)]

    parsed = [_parse_sse_line(line) for line in lines]
    assert [seq for seq, _ in parsed] == [1, 2]
    assert parsed[-1][1]["type"] == "final"
