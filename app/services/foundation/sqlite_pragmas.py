"""Standard SQLite pragmas for non-pooled connections (cache/plan/job DBs)."""
from __future__ import annotations

import sqlite3


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Enable WAL + busy timeout; idempotent, degrades silently on exotic filesystems."""
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn
