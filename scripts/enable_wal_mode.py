#!/usr/bin/env python3
"""One-time: enable WAL mode on all existing SQLite DB files under a root.

journal_mode is persisted in the DB file header, so running this once covers
databases created before apply_sqlite_pragmas existed. New databases get WAL
at birth via apply_sqlite_pragmas.

Usage: python scripts/enable_wal_mode.py [root] [--include-backups]
Default root: data/databases. Backup dirs are skipped unless --include-backups.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Iterator, Tuple

_DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def iter_db_files(root: Path, *, include_backups: bool = False) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _DB_SUFFIXES:
            continue
        if not include_backups and "backups" in path.parts:
            continue
        yield path


def enable_wal(path: Path) -> Tuple[bool, str]:
    try:
        conn = sqlite3.connect(str(path))
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
        finally:
            conn.close()
        return (str(mode).lower() == "wal"), str(mode)
    except Exception as exc:
        return False, str(exc)


def main(argv: list | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    include_backups = "--include-backups" in args
    positional = [a for a in args if not a.startswith("--")]
    root = Path(positional[0] if positional else "data/databases")
    if not root.is_dir():
        print(f"root not found: {root}")
        return 1

    total, ok, failed = 0, 0, 0
    for path in iter_db_files(root, include_backups=include_backups):
        total += 1
        success, detail = enable_wal(path)
        if success:
            ok += 1
        else:
            failed += 1
            print(f"FAIL\t{path}\t{detail}")
    print(f"done: {ok}/{total} in WAL mode, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
