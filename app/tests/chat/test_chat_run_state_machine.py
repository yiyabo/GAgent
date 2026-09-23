"""Chat-run lifecycle state machine: legal/illegal transitions, idempotent self-loops."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

import pytest

from app.repository import chat_runs as cr
from app.services.chat_run_state import (
    IllegalChatRunTransition,
    is_transition_allowed,
    transition_chat_run_status,
)

_SCHEMA = """
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'legacy-local'
);
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
INSERT INTO chat_sessions (id, owner_id) VALUES ('sess_unit', 'legacy-local');
"""


@pytest.fixture()
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    c.execute(
        "INSERT INTO chat_runs (run_id, session_id, request_json) VALUES ('r1', 'sess_unit', '{}')"
    )
    c.commit()
    yield c
    c.close()


def _status(c: sqlite3.Connection, run_id: str = "r1") -> str:
    row = c.execute("SELECT status FROM chat_runs WHERE run_id = ?", (run_id,)).fetchone()
    return str(row["status"])


def test_transition_table_declared() -> None:
    assert is_transition_allowed("queued", "running")
    assert is_transition_allowed("queued", "failed")
    assert is_transition_allowed("running", "succeeded")
    assert is_transition_allowed("running", "cancelled")
    assert not is_transition_allowed("queued", "succeeded")
    assert not is_transition_allowed("running", "queued")
    assert not is_transition_allowed("failed", "running")
    assert not is_transition_allowed("succeeded", "failed")
    assert not is_transition_allowed(None, "running")
    # self-loops are idempotent no-ops
    assert is_transition_allowed("running", "running")
    assert is_transition_allowed("failed", "failed")


def test_legal_happy_path(conn: sqlite3.Connection) -> None:
    assert transition_chat_run_status(conn, "r1", "running", started=True)
    row = conn.execute("SELECT status, started_at FROM chat_runs WHERE run_id='r1'").fetchone()
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert transition_chat_run_status(conn, "r1", "succeeded", assistant_message_id=42)
    row = conn.execute(
        "SELECT status, finished_at, assistant_message_id FROM chat_runs WHERE run_id='r1'"
    ).fetchone()
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None
    assert row["assistant_message_id"] == 42


def test_queued_terminal_transitions(conn: sqlite3.Connection) -> None:
    assert transition_chat_run_status(conn, "r1", "failed", error="boom")
    assert _status(conn) == "failed"
    conn.execute("INSERT INTO chat_runs (run_id, session_id, request_json) VALUES ('r2','sess_unit','{}')")
    assert transition_chat_run_status(conn, "r2", "cancelled", error="cancelled")
    assert _status(conn, "r2") == "cancelled"


def test_illegal_transition_skipped_and_warned(conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert transition_chat_run_status(conn, "r1", "succeeded") is False
    assert _status(conn) == "queued"
    assert "illegal chat_run transition" in caplog.text
    # terminal states have no outbound edges
    assert transition_chat_run_status(conn, "r1", "failed", error="x")
    with caplog.at_level("WARNING"):
        assert transition_chat_run_status(conn, "r1", "running", started=True) is False
    assert _status(conn) == "failed"


def test_illegal_transition_raises_in_strict_mode(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHAT_RUN_STATE_STRICT", "1")
    with pytest.raises(IllegalChatRunTransition):
        transition_chat_run_status(conn, "r1", "succeeded")
    # legal transitions still pass under strict mode
    assert transition_chat_run_status(conn, "r1", "running", started=True)


def test_terminal_self_loop_is_noop(conn: sqlite3.Connection) -> None:
    assert transition_chat_run_status(conn, "r1", "failed", error="first")
    first = conn.execute("SELECT finished_at, error FROM chat_runs WHERE run_id='r1'").fetchone()
    assert transition_chat_run_status(conn, "r1", "failed", error="second") is True
    row = conn.execute("SELECT finished_at, error FROM chat_runs WHERE run_id='r1'").fetchone()
    assert row["finished_at"] == first["finished_at"]
    assert row["error"] == "first"


def test_missing_run_returns_false(conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert transition_chat_run_status(conn, "nope", "running", started=True) is False
    assert "target missing" in caplog.text


def test_repository_writes_go_through_choke(monkeypatch: pytest.MonkeyPatch) -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield c

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    cr.create_chat_run("ra", "sess_unit", "{}")
    cr.mark_chat_run_started("ra")
    assert cr.get_chat_run("ra")["status"] == "running"
    cr.mark_chat_run_finished("ra", "succeeded")
    assert cr.get_chat_run("ra")["status"] == "succeeded"
    # second terminal write to a different state is rejected by the machine
    cr.mark_chat_run_finished("ra", "failed", error="late failure")
    assert cr.get_chat_run("ra")["status"] == "succeeded"
    # started_at is not restamped by repeated starts
    first_started = cr.get_chat_run("ra")["started_at"]
    cr.mark_chat_run_started("ra")
    assert cr.get_chat_run("ra")["started_at"] == first_started
