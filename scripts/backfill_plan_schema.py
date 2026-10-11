#!/usr/bin/env python3
"""One-time: bring every existing plan shard up to the current schema.

Shards written by older revisions can miss columns (owner_id, session_id,
metadata_json, ...) that current code reads unconditionally.
``plan_db_connection`` now ensures the schema on first open; this script does
the same for every shard up front, so a deployment can report what was
migrated instead of discovering it lazily under traffic.

Usage: python scripts/backfill_plan_schema.py [root]
Default root: data/databases/plans
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def iter_shard_files(root: Path):
    for path in sorted(root.rglob("plan_*.sqlite")):
        if path.is_file():
            yield path


def _table_columns(conn) -> dict:
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    result = {}
    for table in tables:
        result[table] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return result


def backfill(path: Path) -> tuple[bool, str]:
    from app.repository.plan_storage import ensure_plan_schema

    try:
        conn = sqlite3.connect(str(path))
        try:
            conn.row_factory = sqlite3.Row
            before = _table_columns(conn)
            ensure_plan_schema(conn)
            conn.commit()
            after = _table_columns(conn)
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)

    added = {
        table: sorted(after.get(table, set()) - before.get(table, set()))
        for table in after
    }
    added = {table: cols for table, cols in added.items() if cols}
    if added:
        detail = "; ".join(f"{table}: {','.join(cols)}" for table, cols in sorted(added.items()))
        return True, f"added columns: {detail}"
    return True, "already current"


def main(argv: list | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else REPO_ROOT / "data" / "databases" / "plans"
    if not root.is_dir():
        print(f"root not found: {root}")
        return 1

    total = ok = failed = changed = 0
    for path in iter_shard_files(root):
        total += 1
        success, detail = backfill(path)
        if not success:
            failed += 1
            print(f"FAIL\t{path}\t{detail}")
            continue
        ok += 1
        if detail != "already current":
            changed += 1
            print(f"OK\t{path}\t{detail}")
    print(f"done: {ok}/{total} ok ({changed} migrated), {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
