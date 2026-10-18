"""Retention GC contracts: dry-run safety, live-work protection, DB windows.

``app/ops/retention.py`` deletes production data on a cron. These tests pin
the safety model so a refactor cannot quietly turn the reaper into a
shredder: dry-run is the default, sessions with live work are never selected,
an archive failure keeps the directory, and each table's age window uses its
writer's own timestamp convention (SQLite-UTC for events/signals,
naive-local isoformat for llm_usage_log, UTC+8 for the shard log tables).
"""

from __future__ import annotations

from collections import namedtuple

import os
import sqlite3
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ops.retention import (
    SessionRecord,
    disk_usage_report,
    effective_cap_gb,
    prune_session_archives,
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


# --- archive batches, free-space floor, disk levels ------------------------

def _archive_batch(root: Path, name: str, *, files: int = 1) -> Path:
    batch = root / name
    batch.mkdir(parents=True, exist_ok=True)
    for index in range(files):
        (batch / f"session_x{index}.tar.gz").write_bytes(b"x" * 1024)
    return batch


def test_prune_session_archives_keeps_window_newest_and_unknown(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    now = datetime.now(_UTC8)
    old = _archive_batch(archive_root, (now - timedelta(days=40)).strftime("%Y%m%dT%H%M%S"))
    recent = _archive_batch(archive_root, (now - timedelta(days=5)).strftime("%Y%m%dT%H%M%S"))
    today = _archive_batch(archive_root, now.strftime("%Y%m%dT%H%M%S"))
    unknown = _archive_batch(archive_root, "manual-2026-09-01")

    report = prune_session_archives(archive_root, keep_days=30, keep_min=2, now=now, apply=False)
    assert report["archives_total"] == 3  # the unrecognized name is never a candidate
    assert report["archives_selected"] == 1
    assert report["archives_deleted"] == 0  # dry-run deletes nothing
    assert old.exists()

    report = prune_session_archives(archive_root, keep_days=30, keep_min=2, now=now, apply=True)
    assert report["archives_deleted"] == 1
    assert report["archive_bytes_freed"] > 0
    assert not old.exists()
    assert recent.exists() and today.exists() and unknown.exists()


def test_prune_session_archives_always_keeps_the_newest_batches(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    now = datetime.now(_UTC8)
    first = _archive_batch(archive_root, (now - timedelta(days=90)).strftime("%Y%m%dT%H%M%S"))
    second = _archive_batch(archive_root, (now - timedelta(days=80)).strftime("%Y%m%dT%H%M%S"))
    report = prune_session_archives(archive_root, keep_days=1, keep_min=2, now=now, apply=True)
    assert report["archives_deleted"] == 0
    assert first.exists() and second.exists()


def test_prune_session_archives_missing_root_is_a_noop(tmp_path: Path) -> None:
    report = prune_session_archives(
        tmp_path / "absent", keep_days=30, now=datetime.now(_UTC8), apply=True
    )
    assert report["archives_total"] == 0 and report["archives_deleted"] == 0


def test_effective_cap_prefers_the_free_floor_when_headroom_shrinks() -> None:
    gib = 1024**3
    # Roomy disk: the static cap stands.
    assert effective_cap_gb(
        runtime_bytes=127 * gib, free_bytes=185 * gib, max_gb=200, free_floor_gb=40
    ) == 200
    # Tight disk: the floor binds (127 + 20 - 40 = 107).
    assert effective_cap_gb(
        runtime_bytes=127 * gib, free_bytes=20 * gib, max_gb=200, free_floor_gb=40
    ) == 107
    # No floor configured: unchanged static cap.
    assert effective_cap_gb(
        runtime_bytes=10 * gib, free_bytes=1 * gib, max_gb=200, free_floor_gb=0
    ) == 200
    # Never negative, even with nothing free.
    assert effective_cap_gb(runtime_bytes=0, free_bytes=0, max_gb=200, free_floor_gb=40) == 0


def test_disk_usage_report_levels(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "app.ops.retention.shutil.disk_usage",
        lambda _path: usage(total=100 * 1024**3, used=90 * 1024**3, free=10 * 1024**3),
    )
    report = disk_usage_report(tmp_path, warn_pct=20, error_pct=10)
    assert report["disk_level"] == "warn"
    assert report["disk_free_pct"] == 10.0

    monkeypatch.setattr(
        "app.ops.retention.shutil.disk_usage",
        lambda _path: usage(total=100 * 1024**3, used=95 * 1024**3, free=5 * 1024**3),
    )
    assert disk_usage_report(tmp_path, warn_pct=20, error_pct=10)["disk_level"] == "error"


def test_run_retention_prunes_expired_archives_only(isolated_app_env) -> None:
    from app.database import init_db

    init_db()
    backup_dir = isolated_app_env["db_root"] / "ops_backups"
    archive_root = backup_dir / "archive"
    now = datetime.now(_UTC8)
    stale = _archive_batch(archive_root, (now - timedelta(days=45)).strftime("%Y%m%dT%H%M%S"))
    middle = _archive_batch(archive_root, (now - timedelta(days=20)).strftime("%Y%m%dT%H%M%S"))
    recent = _archive_batch(archive_root, (now - timedelta(days=3)).strftime("%Y%m%dT%H%M%S"))

    report = run_retention(
        apply=True, archive_keep_days=30, **_retention_kwargs(isolated_app_env, backup_dir)
    )

    assert report["archives_deleted"] == 1
    assert not stale.exists() and middle.exists() and recent.exists()
    assert "disk_free_pct" in report and "cap_gb_effective" in report
