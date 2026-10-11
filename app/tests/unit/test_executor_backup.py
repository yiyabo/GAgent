"""Backup/restore contracts for app/ops/backup.py.

These snapshots are the last line of defence for the production SQLite data,
so the tests pin: VACUUM INTO output is a real database, restore is
point-in-time (files added after the snapshot are gone), stale WAL/SHM files
are swept before overwriting (a leftover WAL would corrupt the restored
file), and the keep policy is count-based on distinct days/ISO weeks.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ops.backup import (
    list_backups,
    restore_backup,
    run_backup,
    select_backups_to_prune,
)

_UTC8 = timezone(timedelta(hours=8))


def test_run_backup_snapshots_all_dbs_and_manifest(isolated_app_env) -> None:
    from app.database import init_db
    from app.database_pool import get_db
    from app.repository.plan_storage import (
        append_decomposition_job_log,
        record_decomposition_job,
    )

    init_db()
    # Shard connections enforce FKs (plan_db_connection), so the log's parent
    # job row must exist first — same order production uses.
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
    append_decomposition_job_log(
        7, job_id="j1", timestamp=datetime.now(_UTC8), level="info", message="keep me"
    )
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('s1', 'u')")
        conn.commit()

    db_root = Path(isolated_app_env["db_root"])
    backup_dir = db_root / "ops_backups"
    now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=_UTC8)

    report = run_backup(backup_dir=backup_dir, db_root=db_root, now=now)

    snap = backup_dir / "sqlite" / report["ts"]
    snap_main = snap / "main" / "plan_registry.db"
    snap_shard = snap / "plans" / "plan_7.sqlite"
    assert snap_main.exists() and snap_shard.exists()
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ts"] == report["ts"]
    assert {entry["path"] for entry in manifest["files"]} >= {
        "main/plan_registry.db",
        "plans/plan_7.sqlite",
    }

    # The snapshot is a real, readable database with the row in it.
    conn = sqlite3.connect(snap_main)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0] == 1
    finally:
        conn.close()
    shard_conn = sqlite3.connect(snap_shard)
    try:
        assert shard_conn.execute("SELECT message FROM decomposition_job_logs").fetchall() == [
            ("keep me",)
        ]
    finally:
        shard_conn.close()


def test_restore_is_point_in_time_and_sweeps_stale_wal(isolated_app_env) -> None:
    from app.database import init_db
    from app.database_pool import get_db

    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('s1', 'u')")
        conn.commit()

    db_root = Path(isolated_app_env["db_root"])
    backup_dir = db_root / "ops_backups"
    report = run_backup(
        backup_dir=backup_dir, db_root=db_root, now=datetime(2026, 10, 1, 12, 0, 0, tzinfo=_UTC8)
    )

    # Data that arrived after the snapshot, plus a stale WAL squatting on the
    # live file — both must be gone after the restore.
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('s2', 'u')")
        conn.commit()
    stale_wal = db_root / "main" / "plan_registry.db-wal"
    stale_wal.write_bytes(b"stale")

    result = restore_backup(report["ts"], backup_dir=backup_dir, db_root=db_root)

    assert result["restored"] >= 1
    assert not stale_wal.exists()
    conn = sqlite3.connect(db_root / "main" / "plan_registry.db")
    try:
        ids = {row[0] for row in conn.execute("SELECT id FROM chat_sessions")}
    finally:
        conn.close()
    assert ids == {"s1"}


def test_restore_refuses_unknown_or_manifestless_snapshot(isolated_app_env) -> None:
    db_root = Path(isolated_app_env["db_root"])
    backup_dir = db_root / "ops_backups"
    missing_ts = "20990101T000000"
    try:
        restore_backup(missing_ts, backup_dir=backup_dir, db_root=db_root)
        raise AssertionError("restore of a missing snapshot must raise")
    except FileNotFoundError:
        pass

    orphan = backup_dir / "sqlite" / missing_ts
    orphan.mkdir(parents=True)
    (orphan / "main").mkdir()
    (orphan / "main" / "plan_registry.db").write_bytes(b"junk")
    try:
        restore_backup(missing_ts, backup_dir=backup_dir, db_root=db_root)
        raise AssertionError("restore without a manifest must raise")
    except FileNotFoundError:
        pass


def test_select_backups_to_prune_keeps_daily_and_weekly() -> None:
    stamps = [
        "20261001T030000",
        "20261002T030000",
        "20261002T150000",  # second snapshot the same day: not that day's keeper
        "20261003T030000",
        "20261004T030000",
        "20261005T030000",
        "20261006T030000",
        "20261007T030000",
        "20261008T030000",
        "20261009T030000",
        "20261010T030000",
        "20261011T030000",
    ]
    pruned = select_backups_to_prune(stamps, keep_daily=3, keep_weekly=1)
    kept = set(stamps) - set(pruned)

    # Daily keeps the newest snapshot of each of the 3 newest days; weekly
    # keeps the newest snapshot of the newest ISO week (which is the 11th).
    assert {"20261011T030000", "20261010T030000", "20261009T030000"} <= kept
    assert "20261002T150000" in pruned
    assert "20261001T030000" in pruned
    assert "20261005T030000" in pruned


def test_run_backup_prunes_by_keep_policy(isolated_app_env) -> None:
    from app.database import init_db

    init_db()
    db_root = Path(isolated_app_env["db_root"])
    backup_dir = db_root / "ops_backups"
    base = datetime(2026, 10, 1, 3, 0, 0, tzinfo=_UTC8)

    for day in range(4):
        run_backup(
            backup_dir=backup_dir,
            db_root=db_root,
            keep_daily=2,
            keep_weekly=0,
            now=base + timedelta(days=day),
        )

    remaining = [entry["ts"] for entry in list_backups(backup_dir)]
    assert remaining == [
        (base + timedelta(days=3)).strftime("%Y%m%dT%H%M%S"),
        (base + timedelta(days=2)).strftime("%Y%m%dT%H%M%S"),
    ]
