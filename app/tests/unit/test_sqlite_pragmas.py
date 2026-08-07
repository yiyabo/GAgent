from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.foundation.sqlite_pragmas import apply_sqlite_pragmas


def test_apply_pragmas_sets_wal_busy_timeout(tmp_path) -> None:
    db = tmp_path / "t.db"
    conn = apply_sqlite_pragmas(sqlite3.connect(str(db)))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    finally:
        conn.close()


def test_wal_mode_persists_for_later_raw_connections(tmp_path) -> None:
    db = tmp_path / "t.db"
    conn = apply_sqlite_pragmas(sqlite3.connect(str(db)))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    raw = sqlite3.connect(str(db))
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        raw.close()


def test_plan_db_connection_applies_wal(tmp_path) -> None:
    from app.database import plan_db_connection

    with plan_db_connection(tmp_path / "plan_999.sqlite") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_enable_wal_script_covers_existing_files(tmp_path) -> None:
    from scripts.enable_wal_mode import enable_wal, iter_db_files, main

    plain = tmp_path / "plain.db"
    conn = sqlite3.connect(str(plain))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()
    assert sqlite3.connect(str(plain)).execute("PRAGMA journal_mode").fetchone()[0] != "wal"

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = backup_dir / "old.db"
    conn = sqlite3.connect(str(backup))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    found = list(iter_db_files(tmp_path))
    assert plain in found
    assert backup not in found
    assert backup in list(iter_db_files(tmp_path, include_backups=True))

    ok, mode = enable_wal(plain)
    assert ok and mode == "wal"
    assert sqlite3.connect(str(plain)).execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    assert main([str(tmp_path)]) == 0
