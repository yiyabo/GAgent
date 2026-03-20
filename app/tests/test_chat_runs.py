"""Tests for chat run persistence, replay indices, and cancel signalling."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

import pytest

from app.repository import chat_runs as cr
from app.services import chat_run_hub as hub


_SCHEMA = """
CREATE TABLE chat_sessions (id TEXT PRIMARY KEY);
CREATE TABLE chat_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    user_message_id INTEGER,
    assistant_message_id INTEGER,
    idempotency_key TEXT,
    error TEXT,
    request_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    last_event_seq INTEGER NOT NULL DEFAULT -1,
    FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
);
CREATE TABLE chat_run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES chat_runs (run_id) ON DELETE CASCADE
);
INSERT INTO chat_sessions (id) VALUES ('sess_unit');
"""


@pytest.fixture()
def memory_chat_run_db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        try:
            yield conn
        finally:
            pass

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    return conn


def test_append_chat_run_event_monotonic_seq(memory_chat_run_db: sqlite3.Connection) -> None:
    cr.create_chat_run("run_a", "sess_unit", '{"message":"m"}')
    s0 = cr.append_chat_run_event("run_a", {"type": "start"})
    s1 = cr.append_chat_run_event("run_a", {"type": "delta", "content": "x"})
    s2 = cr.append_chat_run_event("run_a", {"type": "final", "payload": {}})
    assert s0 == 0 and s1 == 1 and s2 == 2
    row = cr.get_chat_run("run_a")
    assert row is not None
    assert row["last_event_seq"] == 2


def test_fetch_events_after_seq(memory_chat_run_db: sqlite3.Connection) -> None:
    cr.create_chat_run("run_b", "sess_unit", "{}")
    cr.append_chat_run_event("run_b", {"type": "a"})
    cr.append_chat_run_event("run_b", {"type": "b"})
    cr.append_chat_run_event("run_b", {"type": "c"})
    rows = cr.fetch_events_after("run_b", 0)
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1]["type"] == "b"


def test_list_session_runs_filter(memory_chat_run_db: sqlite3.Connection) -> None:
    cr.create_chat_run("r1", "sess_unit", "{}")
    cr.mark_chat_run_started("r1")
    cr.mark_chat_run_finished("r1", "succeeded")
    cr.create_chat_run("r2", "sess_unit", "{}")
    cr.mark_chat_run_started("r2")
    running = cr.list_session_runs("sess_unit", status="running", limit=5)
    ids = {r["run_id"] for r in running}
    assert "r2" in ids and "r1" not in ids


def test_hub_cancel_sets_event() -> None:
    hub.cleanup_run_signals("tmp_cancel")
    ev = hub.ensure_cancel_event("tmp_cancel")
    assert not ev.is_set()
    hub.request_cancel("tmp_cancel")
    assert ev.is_set()
    hub.cleanup_run_signals("tmp_cancel")


def test_format_sse_includes_id_line() -> None:
    line = hub.format_sse_line(3, {"type": "delta", "content": "z"})
    assert "id: 3" in line
    assert "data:" in line
    payload = json.loads(line.split("data: ", 1)[1].split("\n", 1)[0])
    assert payload["type"] == "delta"
