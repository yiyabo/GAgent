"""Durable run signals + worker lease: repository, pump delivery, reaper, resume guard."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from typing import Iterator, List

import pytest

import app.database as app_database
from app.repository import chat_runs as cr
from app.routers.chat import run_routes
from app.routers.chat.models import ChatRequest
from app.services import chat_run_hub as hub
from app.services.chat_run_signals import run_signal_pump

_SCHEMA = """
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'legacy-local',
    name TEXT,
    name_source TEXT,
    is_user_named INTEGER DEFAULT 0,
    metadata TEXT,
    plan_id INTEGER,
    plan_title TEXT,
    project_id INTEGER,
    last_message_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    metadata TEXT
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
    last_event_seq INTEGER NOT NULL DEFAULT -1,
    worker_id TEXT,
    heartbeat_at TIMESTAMP,
    lease_expires_at TIMESTAMP
);
CREATE UNIQUE INDEX idx_chat_runs_idempotency
ON chat_runs(session_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;
CREATE TABLE chat_run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE chat_run_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMP
);
INSERT INTO chat_sessions (id, owner_id) VALUES ('sess_unit', 'legacy-local');
"""


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    # check_same_thread=False: the signal pump reads via asyncio.to_thread.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    monkeypatch.setattr(run_routes, "get_db", fake_get_db)
    monkeypatch.setattr(app_database, "get_db", fake_get_db)
    return conn


@pytest.fixture(autouse=True)
def _clean_hub() -> Iterator[None]:
    yield
    hub.cleanup_run_signals("run_sig")
    hub.cleanup_run_signals("run_lease")
    hub.cleanup_run_signals("run_resume")
    hub.forget_worker_task("run_resume")


def _mk_run(db: sqlite3.Connection, run_id: str, *, key: str | None = None) -> None:
    db.execute(
        "INSERT INTO chat_runs (run_id, session_id, request_json, idempotency_key) VALUES (?, 'sess_unit', '{}', ?)",
        (run_id, key),
    )
    db.commit()


# ---- signal table -----------------------------------------------------------


def test_signal_roundtrip(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_sig")
    sid = cr.insert_chat_run_signal("run_sig", "steer", {"message": "换个角度"})
    assert sid >= 1
    rows = cr.fetch_unconsumed_chat_run_signals("run_sig")
    assert len(rows) == 1 and rows[0]["kind"] == "steer"
    assert rows[0]["payload"]["message"] == "换个角度"
    cr.mark_chat_run_signals_consumed([rows[0]["id"]])
    assert cr.fetch_unconsumed_chat_run_signals("run_sig") == []


async def test_pump_applies_cancel_and_steer(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_sig")
    cancel_ev = hub.ensure_cancel_event("run_sig")
    hub.ensure_steer_queue("run_sig")
    cr.insert_chat_run_signal("run_sig", "steer", {"message": "先别画图"})
    cr.insert_chat_run_signal("run_sig", "cancel")

    stop = asyncio.Event()
    task = asyncio.create_task(run_signal_pump("run_sig", stop))
    for _ in range(50):
        if cancel_ev.is_set() and hub.drain_steer_messages("run_sig"):
            break
        await asyncio.sleep(0.05)
    stop.set()
    await task

    assert cancel_ev.is_set()
    assert cr.fetch_unconsumed_chat_run_signals("run_sig") == []


async def test_pump_cancel_also_sets_the_thread_safe_delegation_token(
    db: sqlite3.Connection,
) -> None:
    """The durable fallback must reach the worker's CLI watchdog too.

    A delegation watches the CLI subprocess from a worker thread, where only a
    ``threading`` primitive can be observed — so the pump's cancel has to flip
    the run's cancel token, not just the loop-side event.
    """
    _mk_run(db, "run_sig")
    cancel_ev = hub.ensure_cancel_event("run_sig")
    token = hub.ensure_cancel_token("run_sig")
    assert token.cancelled is False
    cr.insert_chat_run_signal("run_sig", "cancel")

    stop = asyncio.Event()
    task = asyncio.create_task(run_signal_pump("run_sig", stop))
    for _ in range(50):
        if token.cancelled:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await task

    assert cancel_ev.is_set()
    assert token.cancelled is True
    assert token.reason == "chat_run_cancelled"


async def test_pump_survives_db_errors(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _mk_run(db, "run_sig")
    calls = {"n": 0}
    real_fetch = cr.fetch_unconsumed_chat_run_signals

    def flaky(run_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db locked")
        return real_fetch(run_id)

    import app.services.chat_run_signals as sig_mod

    monkeypatch.setattr(sig_mod, "fetch_unconsumed_chat_run_signals", flaky)
    hub.ensure_cancel_event("run_sig")
    cr.insert_chat_run_signal("run_sig", "cancel")

    stop = asyncio.Event()
    task = asyncio.create_task(run_signal_pump("run_sig", stop))
    for _ in range(50):
        if hub.ensure_cancel_event("run_sig").is_set():
            break
        await asyncio.sleep(0.05)
    stop.set()
    await task
    assert hub.ensure_cancel_event("run_sig").is_set()


# ---- lease ------------------------------------------------------------------


def test_lease_claim_heartbeat_release(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    assert not cr.is_chat_run_lease_live("run_lease")
    cr.claim_chat_run_lease("run_lease", "worker-a", ttl_seconds=30)
    assert cr.is_chat_run_lease_live("run_lease")
    assert cr.heartbeat_chat_run_lease("run_lease", "worker-a", ttl_seconds=30)
    # another worker cannot heartbeat our lease
    assert not cr.heartbeat_chat_run_lease("run_lease", "worker-b", ttl_seconds=30)
    cr.release_chat_run_lease("run_lease", "worker-a")
    assert not cr.is_chat_run_lease_live("run_lease")
    # worker_id is kept for forensics
    row = db.execute("SELECT worker_id FROM chat_runs WHERE run_id='run_lease'").fetchone()
    assert row["worker_id"] == "worker-a"


def test_reap_expired_lease_marks_failed(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    cr.mark_chat_run_started("run_lease")
    cr.claim_chat_run_lease("run_lease", "worker-dead", ttl_seconds=30)
    db.execute(
        "UPDATE chat_runs SET lease_expires_at = datetime('now', '-1 seconds') WHERE run_id='run_lease'"
    )
    cr.insert_chat_run_signal("run_lease", "cancel")
    db.commit()

    assert cr.reap_expired_chat_runs() == 1
    row = cr.get_chat_run("run_lease")
    assert row["status"] == "failed"
    assert "interrupted" in (row["error"] or "")
    events = cr.fetch_events_after("run_lease", -1)
    assert any(e[1].get("run_interrupted") for e in events)
    # terminal-run signals are consumed as housekeeping
    assert cr.fetch_unconsumed_chat_run_signals("run_lease") == []


def test_reap_spares_fresh_null_lease_and_live_lease(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_fresh")
    _mk_run(db, "run_live")
    cr.claim_chat_run_lease("run_live", "worker-a", ttl_seconds=30)
    assert cr.reap_expired_chat_runs() == 0
    assert cr.get_chat_run("run_fresh")["status"] == "queued"
    assert cr.get_chat_run("run_live")["status"] == "queued"


def test_reap_old_null_lease_row(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_old")
    db.execute(
        "UPDATE chat_runs SET created_at = datetime('now', '-120 seconds') WHERE run_id='run_old'"
    )
    db.commit()
    assert cr.reap_expired_chat_runs(ttl_seconds=30) == 1
    assert cr.get_chat_run("run_old")["status"] == "failed"


# ---- idempotent resume lease guard ------------------------------------------


@pytest.fixture()
async def worker_gate(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    gate = asyncio.Event()
    spawns: List[str] = []

    async def _fake_worker(run_id: str) -> None:
        await gate.wait()

    monkeypatch.setattr(run_routes, "execute_chat_run", _fake_worker)
    monkeypatch.setattr(run_routes, "_schedule_quality_follow_up", lambda **kwargs: None)
    yield spawns
    gate.set()
    await asyncio.sleep(0)


def _req(cmid: str) -> ChatRequest:
    return ChatRequest(message="继续分析", session_id="sess_unit", client_message_id=cmid)


async def test_resume_skips_redispatch_when_lease_live(
    db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    _mk_run(db, "run_resume", key="ck_lease")
    cr.claim_chat_run_lease("run_resume", "worker-other", ttl_seconds=30)
    run_id = run_routes.start_background_chat_run(
        _req("ck_lease"), session_id="sess_unit", owner_id="legacy-local"
    )
    assert run_id == "run_resume"
    assert hub.has_live_worker_task("run_resume") is False
    # lease held elsewhere -> no local redispatch
    assert "run_resume" not in [t.get_name() for t in asyncio.all_tasks()]


async def test_resume_redispatches_when_lease_expired(
    db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    _mk_run(db, "run_resume", key="ck_lease2")
    cr.claim_chat_run_lease("run_resume", "worker-dead", ttl_seconds=30)
    db.execute(
        "UPDATE chat_runs SET lease_expires_at = datetime('now', '-1 seconds') WHERE run_id='run_resume'"
    )
    db.commit()
    run_id = run_routes.start_background_chat_run(
        _req("ck_lease2"), session_id="sess_unit", owner_id="legacy-local"
    )
    assert run_id == "run_resume"
    assert hub.has_live_worker_task("run_resume")
