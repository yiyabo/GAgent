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


def test_live_claim_cannot_be_overwritten_even_by_same_worker(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    assert cr.claim_chat_run_lease("run_lease", "worker-a")
    assert not cr.claim_chat_run_lease("run_lease", "worker-b")
    assert not cr.claim_chat_run_lease("run_lease", "worker-a")
    assert cr.heartbeat_chat_run_lease("run_lease", "worker-a")
    assert not cr.heartbeat_chat_run_lease("run_lease", "worker-b")


def test_expired_claim_is_fenced_from_events_and_completion(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    assert cr.claim_chat_run_lease("run_lease", "attempt-a")
    assert cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    db.execute("UPDATE chat_runs SET lease_expires_at = datetime('now', '-1 seconds') WHERE run_id='run_lease'")
    db.commit()
    assert not cr.heartbeat_chat_run_lease("run_lease", "attempt-a")
    assert cr.claim_chat_run_lease("run_lease", "attempt-b")
    assert cr.append_chat_run_event("run_lease", {"type": "delta", "content": "stale"}, worker_id="attempt-a") is None
    assert cr.batch_append_chat_run_events("run_lease", [{"type": "delta", "content": "stale"}], worker_id="attempt-a") == []
    assert cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}}, worker_id="attempt-a") is None
    assert not cr.mark_chat_run_finished("run_lease", "failed", worker_id="attempt-a")
    assert cr.get_chat_run("run_lease")["status"] == "running"
    assert cr.fetch_events_after("run_lease", -1) == []
    assert cr.append_chat_run_event("run_lease", {"type": "delta", "content": "current"}, worker_id="attempt-b") == 0


def test_terminal_run_cannot_be_claimed(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    cr.mark_chat_run_finished("run_lease", "failed")
    assert not cr.claim_chat_run_lease("run_lease", "worker-a")


async def test_fast_steer_and_pump_apply_signal_once(db: sqlite3.Connection) -> None:
    from app.services.chat_run_signals import _apply_signals_once
    from app.services.realtime_bus import _handle_control_message

    _mk_run(db, "run_sig")
    hub.ensure_steer_queue("run_sig")
    sid = cr.insert_chat_run_signal("run_sig", "steer", {"message": "Use the updated requirement"})
    assert _handle_control_message({"type": "chat_run.steer", "run_id": "run_sig", "message": "Use the updated requirement", "signal_id": sid})
    assert hub.drain_steer_messages("run_sig") == ["Use the updated requirement"]
    await _apply_signals_once("run_sig")
    assert hub.drain_steer_messages("run_sig") == []
    assert cr.fetch_unconsumed_chat_run_signals("run_sig") == []
    # Identical content with a new ID is an intentional new user instruction.
    cr.insert_chat_run_signal("run_sig", "steer", {"message": "Use the updated requirement"})
    await _apply_signals_once("run_sig")
    assert hub.drain_steer_messages("run_sig") == ["Use the updated requirement"]


async def test_pump_does_not_ack_steer_without_an_accepting_run(db: sqlite3.Connection) -> None:
    from app.services.chat_run_signals import _apply_signals_once

    _mk_run(db, "run_sig")
    cr.insert_chat_run_signal("run_sig", "steer", {"message": "retry later"})
    await _apply_signals_once("run_sig")
    assert len(cr.fetch_unconsumed_chat_run_signals("run_sig")) == 1
    hub.ensure_steer_queue("run_sig")
    await _apply_signals_once("run_sig")
    assert hub.drain_steer_messages("run_sig") == ["retry later"]
    assert cr.fetch_unconsumed_chat_run_signals("run_sig") == []


async def test_fenced_pump_cannot_apply_or_consume_signals(db: sqlite3.Connection) -> None:
    from app.services.chat_run_signals import _apply_signals_once

    _mk_run(db, "run_sig")
    cr.claim_chat_run_lease("run_sig", "attempt-a")
    db.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id='run_sig'")
    db.commit()
    cr.claim_chat_run_lease("run_sig", "attempt-b")
    hub.ensure_steer_queue("run_sig")
    cr.insert_chat_run_signal("run_sig", "steer", {"message": "new owner only"})
    await _apply_signals_once("run_sig", worker_id="attempt-a")
    assert hub.drain_steer_messages("run_sig") == []
    assert len(cr.fetch_unconsumed_chat_run_signals("run_sig")) == 1
    await _apply_signals_once("run_sig", worker_id="attempt-b")
    assert hub.drain_steer_messages("run_sig") == ["new owner only"]
    assert cr.fetch_unconsumed_chat_run_signals("run_sig") == []


def test_terminal_event_and_state_roll_back_together(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _mk_run(db, "run_lease")
    cr.mark_chat_run_started("run_lease")

    def fail_append(*args):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(cr, "_append_event", fail_append)
    with pytest.raises(RuntimeError, match="event write failed"):
        cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}})
    assert cr.get_chat_run("run_lease")["status"] == "running"
    assert cr.fetch_events_after("run_lease", -1) == []


def test_terminal_event_is_first_winner_only(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    cr.mark_chat_run_started("run_lease")
    final = {"type": "final", "payload": {"response": "done"}}
    assert cr.finish_chat_run_with_event("run_lease", "succeeded", final) == 0
    assert cr.finish_chat_run_with_event("run_lease", "succeeded", final) is None
    assert cr.finish_chat_run_with_event("run_lease", "failed", {"type": "error", "message": "late"}) is None
    assert cr.get_chat_run("run_lease")["status"] == "succeeded"
    assert cr.fetch_events_after("run_lease", -1) == [(0, final)]


def test_same_terminal_owner_can_attach_message_id(db: sqlite3.Connection) -> None:
    _mk_run(db, "run_lease")
    assert cr.claim_chat_run_lease("run_lease", "worker-a")
    cr.mark_chat_run_started("run_lease", worker_id="worker-a")
    cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}}, worker_id="worker-a")
    assert cr.mark_chat_run_finished("run_lease", "succeeded", assistant_message_id=42, worker_id="worker-a")
    assert cr.get_chat_run("run_lease")["assistant_message_id"] == 42
    assert not cr.mark_chat_run_finished("run_lease", "succeeded", assistant_message_id=99, worker_id="worker-b")
    assert cr.get_chat_run("run_lease")["assistant_message_id"] == 42


async def test_emitter_publishes_only_after_terminal_commit(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chat_run_emitter as module

    _mk_run(db, "run_lease")
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    seen = []

    class Bus:
        async def publish_run_event(self, run_id, seq, payload):
            seen.append((cr.get_chat_run(run_id)["status"], cr.fetch_events_after(run_id, -1)))

    async def get_bus():
        return Bus()

    monkeypatch.setattr(module, "get_realtime_bus", get_bus)
    emitter = module.ChatRunEmitter("run_lease")
    emitter.worker_id = "attempt-a"
    final = {"type": "final", "payload": {"response": "done"}}
    assert await emitter.emit(final)
    assert seen == [("succeeded", [(0, {**final, "terminal_claim_id": "attempt-a"})])]
    assert not await emitter.emit({"type": "error", "message": "late"})
    assert len(seen) == 1


async def test_delayed_terminal_publication_keeps_winner_message_authority(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from app.services import chat_run_emitter as module, chat_run_worker as worker
    from app.routers.chat.session_helpers import _save_chat_message
    from app.services.chat_run_state import chat_run_claim
    import app.services.memory.chat_memory_middleware as memory

    async def noop(**kwargs):
        pass

    monkeypatch.setattr(memory, "get_chat_memory_middleware", lambda: SimpleNamespace(process_message=noop))
    _mk_run(db, "run_lease")
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    emitter = module.ChatRunEmitter("run_lease")
    emitter.worker_id = "attempt-a"
    lease_lost = asyncio.Event()

    class Bus:
        async def publish_run_event(self, run_id, seq, payload):
            assert emitter.terminal_committed.is_set()
            # Deterministically simulate publication outlasting the remaining TTL.
            db.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id=?", (run_id,))
            db.commit()
            assert not cr.heartbeat_chat_run_lease(run_id, "attempt-a")
            await worker._run_lease_heartbeat(
                run_id, "attempt-a", asyncio.Event(), owner_task=asyncio.current_task(),
                lease_lost=lease_lost, terminal_committed=emitter.terminal_committed,
            )
            assert not lease_lost.is_set()
            await asyncio.sleep(0)

    async def get_bus():
        return Bus()

    monkeypatch.setattr(module, "get_realtime_bus", get_bus)
    handle = chat_run_claim.set(("run_lease", "attempt-a"))
    try:
        assert await emitter.emit({"type": "final", "payload": {"response": "done"}})
        message_id = _save_chat_message("sess_unit", "assistant", "done")
        assert isinstance(message_id, int)
        assert cr.get_chat_run("run_lease")["assistant_message_id"] == message_id
        cr.release_chat_run_lease("run_lease", "attempt-a")
        assert _save_chat_message("sess_unit", "assistant", "duplicate") == message_id
    finally:
        chat_run_claim.reset(handle)
    await asyncio.sleep(0)


def test_reaper_interrupted_owner_cannot_save_after_terminal(db: sqlite3.Connection) -> None:
    from app.routers.chat.session_helpers import _save_chat_message
    from app.services.chat_run_state import chat_run_claim

    _mk_run(db, "run_lease")
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    db.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id='run_lease'")
    db.commit()
    assert cr.reap_expired_chat_runs() == 1
    assert db.execute("SELECT worker_id FROM chat_runs WHERE run_id='run_lease'").fetchone()[0] == "attempt-a"
    assert "terminal_claim_id" not in cr.fetch_events_after("run_lease", -1)[0][1]
    handle = chat_run_claim.set(("run_lease", "attempt-a"))
    try:
        assert _save_chat_message("sess_unit", "assistant", "late success") is None
        assert db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        assert not cr.mark_chat_run_finished("run_lease", "failed", assistant_message_id=99, worker_id="attempt-a")
    finally:
        chat_run_claim.reset(handle)


def test_closed_producer_cannot_save_even_for_terminal_winner(db: sqlite3.Connection) -> None:
    from app.routers.chat.session_helpers import _save_chat_message
    from app.services.chat_run_state import chat_run_claim
    from app.services.cancellation import CancelToken, set_cancel_token, reset_cancel_token

    _mk_run(db, "run_lease")
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}}, worker_id="attempt-a")
    handle = chat_run_claim.set(("run_lease", "attempt-a"))
    token = CancelToken()
    token.close()
    token_handle = set_cancel_token(token)
    try:
        assert _save_chat_message("sess_unit", "assistant", "late zombie output") is None
        assert db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
    finally:
        reset_cancel_token(token_handle)
        chat_run_claim.reset(handle)


async def test_rejected_terminal_event_does_not_signal_committed(db: sqlite3.Connection) -> None:
    from app.services.chat_run_emitter import ChatRunEmitter

    _mk_run(db, "run_lease")
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    db.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id='run_lease'")
    db.commit()
    cr.claim_chat_run_lease("run_lease", "attempt-b")
    emitter = ChatRunEmitter("run_lease")
    emitter.worker_id = "attempt-a"
    assert not await emitter.emit({"type": "final", "payload": {}})
    assert not emitter.terminal_committed.is_set()
    assert cr.fetch_events_after("run_lease", -1) == []


async def test_lost_lease_during_terminal_flush_cancels_worker(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from app.services import chat_run_emitter as module, chat_run_worker as worker
    from app.services.cancellation import current_cancel_token
    from app.services.chat_run_state import chat_run_claim

    _mk_run(db, "run_lease")
    db.execute("UPDATE chat_runs SET request_json=? WHERE run_id='run_lease'", (ChatRequest(message="execute", session_id="sess_unit").model_dump_json(),))
    db.commit()
    seen = {}
    flush_entered = asyncio.Event()

    async def stream(*args, event_sink, **kwargs):
        seen["token"] = current_cancel_token()
        await event_sink({"type": "delta", "content": "pending"})
        # This final must flush the earlier event before its DB transaction.
        await event_sink({"type": "final", "payload": {"response": "done"}})
        yield ""

    async def build(*args, **kwargs):
        return SimpleNamespace(extra_context={}, process_unified_stream=stream), "execute"

    async def pump(*args, **kwargs):
        pass

    def emitter_factory(run_id):
        emitter = module.ChatRunEmitter(run_id)
        emitter._schedule_flush = lambda: None
        seen["emitter"] = emitter
        return emitter

    class Bus:
        async def publish_run_event(self, run_id, seq, payload):
            if payload["type"] == "delta":
                db.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id=?", (run_id,))
                db.commit()
                assert cr.claim_chat_run_lease(run_id, "replacement")
                flush_entered.set()
                await asyncio.Event().wait()

    async def get_bus():
        return Bus()

    monkeypatch.setattr(worker, "build_agent_for_chat_request", build)
    monkeypatch.setattr(worker, "ChatRunEmitter", emitter_factory)
    monkeypatch.setattr(worker, "run_signal_pump", pump)
    monkeypatch.setattr(worker, "start_owner_lease", lambda *args: None)
    monkeypatch.setattr(worker, "stop_owner_lease", lambda *args: None)
    monkeypatch.setattr(worker, "_capture_quality_snapshot", lambda *args: None)
    monkeypatch.setattr(module, "get_realtime_bus", get_bus)
    task = asyncio.create_task(worker.execute_chat_run("run_lease"))
    await asyncio.wait_for(flush_entered.wait(), timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert seen["token"].reason == "chat_run_lease_lost"
    assert not seen["emitter"].terminal_committed.is_set()
    assert cr.get_chat_run("run_lease")["status"] == "running"
    assert db.execute("SELECT worker_id FROM chat_runs WHERE run_id='run_lease'").fetchone()[0] == "replacement"
    assert [event["type"] for _, event in cr.fetch_events_after("run_lease", -1)] == ["start", "delta"]
    assert chat_run_claim.get() is None


async def test_terminal_emit_failure_leaves_run_active(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.chat_run_emitter import ChatRunEmitter

    _mk_run(db, "run_lease")
    cr.mark_chat_run_started("run_lease")
    monkeypatch.setattr(cr, "_append_event", lambda *args: (_ for _ in ()).throw(RuntimeError("event write failed")))
    with pytest.raises(RuntimeError, match="event write failed"):
        await ChatRunEmitter("run_lease").emit({"type": "final", "payload": {}})
    assert cr.get_chat_run("run_lease")["status"] == "running"
    assert cr.fetch_events_after("run_lease", -1) == []


async def test_assistant_save_attaches_id_after_owned_terminal_event(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from app.routers.chat.session_helpers import _save_chat_message
    from app.services.chat_run_state import chat_run_claim
    import app.services.memory.chat_memory_middleware as memory

    async def noop(**kwargs):
        pass

    monkeypatch.setattr(memory, "get_chat_memory_middleware", lambda: SimpleNamespace(process_message=noop))
    _mk_run(db, "run_lease")
    assert cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}}, worker_id="attempt-a")
    handle = chat_run_claim.set(("run_lease", "attempt-a"))
    try:
        message_id = _save_chat_message("sess_unit", "assistant", "done")
        assert isinstance(message_id, int)
        assert cr.get_chat_run("run_lease")["assistant_message_id"] == message_id
        assert _save_chat_message("sess_unit", "assistant", "duplicate") == message_id
        assert db.execute("SELECT COUNT(*) FROM chat_messages WHERE role='assistant'").fetchone()[0] == 1
    finally:
        chat_run_claim.reset(handle)
    await asyncio.sleep(0)


async def test_scoped_assistant_dedup_never_reuses_same_turn_user_id(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from app.routers.chat.session_helpers import _save_chat_message
    from app.services.chat_run_state import chat_run_claim
    import app.services.memory.chat_memory_middleware as memory

    async def noop(**kwargs):
        pass

    monkeypatch.setattr(memory, "get_chat_memory_middleware", lambda: SimpleNamespace(process_message=noop))
    metadata = {"client_message_id": "same-turn"}
    user_id = _save_chat_message("sess_unit", "user", "question", metadata)
    assert isinstance(user_id, int)
    _mk_run(db, "run_lease")
    cr.set_chat_run_user_message_id("run_lease", user_id)
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    cr.mark_chat_run_started("run_lease", worker_id="attempt-a")
    cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}}, worker_id="attempt-a")
    handle = chat_run_claim.set(("run_lease", "attempt-a"))
    try:
        assistant_id = _save_chat_message("sess_unit", "assistant", "answer", metadata)
        assert isinstance(assistant_id, int) and assistant_id != user_id
        assert cr.get_chat_run("run_lease")["assistant_message_id"] == assistant_id
        # Heal a missing pointer by finding the matching assistant, not the user.
        db.execute("UPDATE chat_runs SET assistant_message_id=NULL WHERE run_id='run_lease'")
        db.commit()
        assert _save_chat_message("sess_unit", "assistant", "answer", metadata) == assistant_id
        assert cr.get_chat_run("run_lease")["assistant_message_id"] == assistant_id
        assert db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 2
    finally:
        chat_run_claim.reset(handle)
    await asyncio.sleep(0)


def test_fenced_assistant_save_does_not_write_a_message(db: sqlite3.Connection) -> None:
    from app.routers.chat.session_helpers import _save_chat_message
    from app.services.chat_run_state import chat_run_claim

    _mk_run(db, "run_lease")
    cr.claim_chat_run_lease("run_lease", "attempt-a")
    db.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id='run_lease'")
    db.commit()
    cr.claim_chat_run_lease("run_lease", "attempt-b")
    handle = chat_run_claim.set(("run_lease", "attempt-a"))
    try:
        assert _save_chat_message("sess_unit", "assistant", "stale") is None
        assert db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        assert cr.get_chat_run("run_lease")["assistant_message_id"] is None
    finally:
        chat_run_claim.reset(handle)


def test_reaper_rolls_back_state_if_interruption_event_fails(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _mk_run(db, "run_lease")
    cr.mark_chat_run_started("run_lease")
    db.execute("UPDATE chat_runs SET lease_expires_at = datetime('now', '-1 seconds') WHERE run_id='run_lease'")
    db.commit()
    monkeypatch.setattr(cr, "_append_event", lambda *args: (_ for _ in ()).throw(RuntimeError("event write failed")))
    with pytest.raises(RuntimeError, match="event write failed"):
        cr.reap_expired_chat_runs()
    assert cr.get_chat_run("run_lease")["status"] == "running"
    assert cr.fetch_events_after("run_lease", -1) == []


def test_autocommit_concurrent_claim_and_terminal_race(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use real independent autocommit connections, as the application pool does."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "runs.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        _mk_run(conn, "run_lease")

    @contextmanager
    def pooled_db():
        conn = sqlite3.connect(path, isolation_level=None, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(cr, "get_db", pooled_db)
    barrier = Barrier(2)

    def claim(worker_id):
        barrier.wait()
        return cr.claim_chat_run_lease("run_lease", worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(claim, "worker-a"), pool.submit(claim, "worker-b")
        assert sorted([a.result(), b.result()]) == [False, True]
    cr.mark_chat_run_started("run_lease")
    with pooled_db() as conn:
        conn.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE run_id='run_lease'")

    barrier = Barrier(2)

    def finish():
        barrier.wait()
        return cr.finish_chat_run_with_event("run_lease", "succeeded", {"type": "final", "payload": {}})

    def reap():
        barrier.wait()
        return cr.reap_expired_chat_runs()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(finish), pool.submit(reap)
        a.result(), b.result()
    events = cr.fetch_events_after("run_lease", -1)
    assert len(events) == 1
    status = cr.get_chat_run("run_lease")["status"]
    assert (status, events[0][1]["type"]) in {("succeeded", "final"), ("failed", "error")}


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
