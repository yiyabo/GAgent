"""Retention GC contracts: dry-run safety, live-work protection, DB windows.

``app/ops/retention.py`` deletes production data on a cron. These tests pin
the safety model so a refactor cannot quietly turn the reaper into a
shredder: dry-run is the default, sessions with live work are never selected,
an archive failure keeps the directory, and each table's age window uses its
writer's own timestamp convention (SQLite-UTC for events/signals,
naive-local isoformat for llm_usage_log, UTC+8 for the shard log tables).
"""

from __future__ import annotations

import os
import sqlite3
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ops.retention import (
    SessionRecord,
    run_retention,
    select_expired,
    select_for_size_cap,
)

_UTC8 = timezone(timedelta(hours=8))


def _rec(name: str, *, size: int = 1024, idle_days: float, active: str = "", now_epoch: float):
    return SessionRecord(
        session_dir=Path("/tmp") / name,
        size_bytes=size,
        last_activity_epoch=now_epoch - idle_days * 86400,
        last_activity_source="dir-mtime",
        active_reason=active,
    )


def test_select_expired_respects_window_and_active_protection() -> None:
    now = time.time()
    old = _rec("session_old", idle_days=90, now_epoch=now)
    fresh = _rec("session_fresh", idle_days=1, now_epoch=now)
    live_old = _rec("session_live", idle_days=365, active="active-run-or-job", now_epoch=now)

    picked = select_expired([old, fresh, live_old], retention_days=60, now_epoch=now)

    assert [r.session_dir.name for r in picked] == ["session_old"]


def test_select_for_size_cap_deletes_oldest_first_never_active() -> None:
    now = time.time()
    gib = 1024**3
    records = [
        _rec("session_a", size=5 * gib, idle_days=10, now_epoch=now),
        _rec("session_b", size=5 * gib, idle_days=20, now_epoch=now),
        _rec("session_c", size=5 * gib, idle_days=30, active="active-run-or-job", now_epoch=now),
    ]

    # 15 GiB on disk, cap 8 GiB: drop the oldest NON-active first (b), then a.
    # c is the oldest overall but untouchable.
    extra = select_for_size_cap(records, already_selected=[], max_bytes=8 * gib, now_epoch=now)
    assert [r.session_dir.name for r in extra] == ["session_b", "session_a"]

    # Bytes already selected by the age pass count toward the projected total.
    extra2 = select_for_size_cap(
        records, already_selected=[records[0]], max_bytes=8 * gib, now_epoch=now
    )
    assert [r.session_dir.name for r in extra2] == ["session_b"]


def _make_session_dir(root: Path, name: str, *, idle_days: float, size_bytes: int = 2048) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "payload.bin").write_bytes(b"x" * size_bytes)
    stamp = time.time() - idle_days * 86400
    os.utime(session_dir, (stamp, stamp))
    return session_dir


def _retention_kwargs(isolated_app_env: dict, backup_dir: Path) -> dict:
    from app.config.database_config import get_database_config

    db = get_database_config()
    return {
        "runtime_root": isolated_app_env["runtime_root"],
        "backup_dir": backup_dir,
        "main_db": Path(db.get_main_db_path()),
        "plan_store_dir": db.get_plan_store_dir(),
        "system_db": Path(db.get_system_jobs_db_path()),
    }


def test_run_retention_dry_run_then_apply(isolated_app_env) -> None:
    from app.database import init_db
    from app.database_pool import get_db

    init_db()
    runtime_root = isolated_app_env["runtime_root"]
    backup_dir = isolated_app_env["db_root"] / "ops_backups"

    old_dir = _make_session_dir(runtime_root, "session_gc_old", idle_days=120)
    fresh_dir = _make_session_dir(runtime_root, "session_gc_fresh", idle_days=1)
    live_dir = _make_session_dir(runtime_root, "session_gc_live", idle_days=200)

    lease = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (id, owner_id, created_at, updated_at) "
            "VALUES ('gc_old', 'u', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
        )
        conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('gc_fresh', 'u')")
        conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('gc_live', 'u')")
        conn.execute(
            "INSERT INTO chat_runs (run_id, session_id, status, lease_expires_at) "
            "VALUES ('run_live', 'gc_live', 'running', ?)",
            (lease,),
        )
        conn.commit()

    kwargs = _retention_kwargs(isolated_app_env, backup_dir)

    report = run_retention(apply=False, retention_days=60, **kwargs)
    assert report["sessions_total"] == 3
    assert report["sessions_active"] == 1
    assert report["sessions_selected_by_age"] == 1
    assert report["sessions_deleted"] == 0
    assert old_dir.exists() and fresh_dir.exists() and live_dir.exists()

    report = run_retention(apply=True, retention_days=60, **kwargs)
    assert report["sessions_deleted"] == 1
    assert not old_dir.exists()
    assert fresh_dir.exists() and live_dir.exists()
    archives = list((backup_dir / "archive").glob("*/session_gc_old.tar.gz"))
    assert archives, "archive-before-delete must leave a tarball"
    assert tarfile.is_tarfile(archives[0])


def test_run_retention_db_windows_and_usage_export(isolated_app_env) -> None:
    from app.database import init_db
    from app.database_pool import get_db
    from app.repository.llm_usage import init_llm_usage_table

    init_db()
    init_llm_usage_table()
    backup_dir = isolated_app_env["db_root"] / "ops_backups"

    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('s1', 'u')")
        conn.execute(
            "INSERT INTO chat_runs (run_id, session_id, status) VALUES ('r_old', 's1', 'succeeded')"
        )
        conn.execute(
            "INSERT INTO chat_run_events (run_id, seq, event_type, payload_json, created_at) "
            "VALUES ('r_old', 0, 'x', '{}', '2020-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO chat_run_events (run_id, seq, event_type, payload_json) "
            "VALUES ('r_old', 1, 'x', '{}')"
        )
        conn.execute(
            "INSERT INTO chat_run_signals (run_id, kind, payload_json, created_at, consumed_at) "
            "VALUES ('r_old', 'k', '{}', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO chat_run_signals (run_id, kind, payload_json, created_at) "
            "VALUES ('r_old', 'k2', '{}', '2020-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO llm_usage_log (provider, model, prompt_tokens, completion_tokens, "
            "total_tokens, created_at) VALUES ('qwen', 'm', 1, 1, 2, '2020-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO llm_usage_log (provider, model, prompt_tokens, completion_tokens, "
            "total_tokens, created_at) VALUES ('qwen', 'm', 1, 1, 2, ?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()

    kwargs = _retention_kwargs(isolated_app_env, backup_dir)
    report = run_retention(apply=True, **kwargs)

    assert report["events_rows"] == 1
    assert report["signals_rows"] == 1  # the consumed one only
    assert report["usage_rows"] == 1
    assert report["usage_csv"] and Path(report["usage_csv"]).exists()
    csv_text = Path(report["usage_csv"]).read_text(encoding="utf-8")
    assert "created_at" in csv_text.splitlines()[0]
    assert "2020-01-01T00:00:00" in csv_text

    with get_db() as conn:
        events = conn.execute("SELECT seq FROM chat_run_events").fetchall()
        signals = conn.execute("SELECT kind FROM chat_run_signals").fetchall()
        usage = conn.execute("SELECT created_at FROM llm_usage_log").fetchall()
    assert [row[0] for row in events] == [1]
    assert [row[0] for row in signals] == ["k2"]  # unconsumed row is kept
    assert len(usage) == 1


def test_run_retention_prunes_shard_job_logs(isolated_app_env) -> None:
    from app.repository.plan_storage import (
        append_decomposition_job_log,
        cleanup_decomposition_job_logs,
        get_plan_db_path,
        plan_db_connection,
        record_decomposition_job,
    )

    old = datetime(2020, 1, 1, tzinfo=_UTC8)
    recent = datetime.now(_UTC8)
    # FK enforcement is ON for shard connections (plan_db_connection), and the
    # log rows reference the job — record the parent first, like production.
    record_decomposition_job(
        7,
        job_id="j1",
        job_type="decompose",
        mode="async",
        target_task_id=None,
        owner_id=None,
        session_id=None,
        status="succeeded",
    )
    append_decomposition_job_log(7, job_id="j1", timestamp=old, level="info", message="old")
    append_decomposition_job_log(7, job_id="j1", timestamp=recent, level="info", message="new")

    # Direct contract of the sibling cleaner: age cutoff is UTC+8-aware.
    cleanup_decomposition_job_logs(7, older_than_days=30)
    with plan_db_connection(get_plan_db_path(7)) as conn:
        rows = conn.execute("SELECT message FROM decomposition_job_logs").fetchall()
    assert [row["message"] for row in rows] == ["new"]

    # And through the retention pass: dry-run counts, apply deletes.
    append_decomposition_job_log(7, job_id="j1", timestamp=old, level="info", message="old2")
    kwargs = _retention_kwargs(isolated_app_env, isolated_app_env["db_root"] / "ops_backups")

    dry = run_retention(apply=False, job_log_retention_days=30, **kwargs)
    assert dry["shards_scanned"] >= 1
    assert dry["shard_log_rows"] == 1
    assert dry["shard_failures"] == 0
    with plan_db_connection(get_plan_db_path(7)) as conn:
        rows = conn.execute("SELECT message FROM decomposition_job_logs").fetchall()
    assert len(rows) == 2  # dry-run must not delete

    applied = run_retention(apply=True, job_log_retention_days=30, **kwargs)
    assert applied["shard_log_rows"] == 1
    assert applied["shard_failures"] == 0
    with plan_db_connection(get_plan_db_path(7)) as conn:
        rows = conn.execute("SELECT message FROM decomposition_job_logs").fetchall()
    assert [row["message"] for row in rows] == ["new"]


def test_scan_aborts_when_active_check_db_is_unreadable(isolated_app_env) -> None:
    """A corrupt main DB must abort the session GC, not guess."""
    runtime_root = isolated_app_env["runtime_root"]
    _make_session_dir(runtime_root, "session_x", idle_days=400)
    main_db = isolated_app_env["db_root"] / "main" / "plan_registry.db"
    main_db.parent.mkdir(parents=True, exist_ok=True)
    main_db.write_bytes(b"this is not a sqlite database")

    kwargs = _retention_kwargs(isolated_app_env, isolated_app_env["db_root"] / "ops_backups")
    report = run_retention(apply=True, **kwargs)

    assert report["abort_reason"], "corrupt DB must produce an abort reason"
    assert report["sessions_deleted"] == 0
    assert (runtime_root / "session_x").exists()


def test_open_sqlite_roundtrip_guard() -> None:
    """Guard the sqlite3 features retention depends on (TRUNCATE checkpoint)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        assert path.exists()
