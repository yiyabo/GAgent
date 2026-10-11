"""SQLite online backups (VACUUM INTO) + restore for the whole DB root.

WHY VACUUM INTO
The DBs are live WAL SQLite files the app writes to around the clock. Copying
them with ``cp`` can capture a torn state (or miss the WAL); ``VACUUM INTO``
produces a single consistent snapshot of the database as of a read
transaction, online, with no external tooling. This module backs up the main
registry DB, the system-jobs DB, and every plan shard into one timestamped
tree, plus a manifest.

LAYOUT
    <backup_dir>/sqlite/<ts>/main/plan_registry.db
    <backup_dir>/sqlite/<ts>/jobs/system_jobs.sqlite      (when it exists)
    <backup_dir>/sqlite/<ts>/plans/plan_<id>.sqlite       (every shard)
    <backup_dir>/sqlite/<ts>/manifest.json                (ts, files, bytes)

Retention: ``BACKUP_KEEP_DAILY`` most recent daily snapshots plus
``BACKUP_KEEP_WEEKLY`` most recent ISO-week snapshots are kept; the rest are
pruned by the same run that creates the new one. Session memory DBs under
``databases/sessions/`` are deliberately NOT backed up (disposable caches;
``DatabaseConfig.cleanup_old_sessions`` already treats them that way).

RESTORE
``--restore <ts>`` replaces the DB files wholesale with the snapshot (a
point-in-time restore: newer shards absent from the snapshot are removed, and
stale ``-wal``/``-shm`` files are swept first — a leftover WAL would corrupt
the restored file). It must NOT run while the app is writing: ``gagentctl
restore`` gates, stops the app, restores, and starts it again.

Run: ``python -m app.ops.backup [--list | --restore TS] [--backup-dir DIR]``
(no flags = take a backup now).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("app.ops.backup")

# Beijing time: no DST, so a fixed offset is exact; snapshot names sort
# chronologically and match the ops cron's scheduling clock.
_UTC8 = timezone(timedelta(hours=8))
_TS_FORMAT = "%Y%m%dT%H%M%S"
_STALE_SUFFIXES = ("-wal", "-shm", "-journal")


def _is_ts(name: str) -> bool:
    try:
        datetime.strptime(name, _TS_FORMAT)
        return True
    except ValueError:
        return False


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FORMAT).replace(tzinfo=_UTC8)


def _vacuum_into(conn: sqlite3.Connection, dest: Path) -> None:
    """``VACUUM INTO`` *dest*.

    Parameterized first (SQLite >= 3.27-era builds accept an expression, but
    older ones reject bound variables in VACUUM), with a literal fallback. The
    path is server-controlled (backup dir + timestamp), so the escaped literal
    carries no injection risk.
    """
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
        return
    except (sqlite3.OperationalError, sqlite3.ProgrammingError):
        pass
    conn.execute("VACUUM INTO '%s'" % str(dest).replace("'", "''"))


def _backup_one(src: Path, dest: Path) -> Optional[int]:
    """Snapshot one DB; returns bytes written, or None when *src* is absent."""
    if not src.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(src)
    try:
        _vacuum_into(conn, dest)
    finally:
        conn.close()
    return dest.stat().st_size


def _iter_db_sources(db_root: Path) -> List[Tuple[Path, Path]]:
    """(source, relative destination) pairs: main DB, system jobs, all shards."""
    pairs: List[Tuple[Path, Path]] = [
        (db_root / "main" / "plan_registry.db", Path("main") / "plan_registry.db"),
        (db_root / "jobs" / "system_jobs.sqlite", Path("jobs") / "system_jobs.sqlite"),
    ]
    plans = db_root / "plans"
    if plans.exists():
        for shard in sorted(plans.glob("*.sqlite")):
            pairs.append((shard, Path("plans") / shard.name))
    return pairs


def select_backups_to_prune(
    timestamps: List[str], *, keep_daily: int, keep_weekly: int
) -> List[str]:
    """Timestamps to delete: everything not in the keep set.

    Keep set = the newest snapshot of each of the ``keep_daily`` most recent
    distinct days, union the newest snapshot of each of the ``keep_weekly``
    most recent ISO weeks. Count-based on purpose: a gap in the schedule (host
    down for a week) must not silently widen the window.
    """
    parsed = sorted(((_parse_ts(t), t) for t in timestamps), reverse=True)
    kept: set = set()
    daily_days: List[object] = []
    for dt, ts in parsed:
        day = dt.date()
        if day not in daily_days:
            if len(daily_days) < max(0, int(keep_daily)):
                daily_days.append(day)
                kept.add(ts)
    weekly_keys: List[object] = []
    for dt, ts in parsed:
        key = dt.isocalendar()[:2]
        if key not in weekly_keys:
            if len(weekly_keys) < max(0, int(keep_weekly)):
                weekly_keys.append(key)
                kept.add(ts)
    return sorted(t for t in timestamps if t not in kept)


def prune_backups(
    backup_dir: Path, *, keep_daily: int, keep_weekly: int
) -> List[str]:
    """Apply the keep policy under ``<backup_dir>/sqlite``; returns pruned ts."""
    root = backup_dir / "sqlite"
    if not root.exists():
        return []
    ts_list = [p.name for p in root.iterdir() if p.is_dir() and _is_ts(p.name)]
    to_prune = select_backups_to_prune(ts_list, keep_daily=keep_daily, keep_weekly=keep_weekly)
    for ts in to_prune:
        shutil.rmtree(root / ts, ignore_errors=True)
        logger.info("backup: pruned %s", ts)
    return to_prune


def list_backups(backup_dir: Path) -> List[Dict[str, object]]:
    """Available snapshots, newest first (manifest metadata when readable)."""
    root = backup_dir / "sqlite"
    entries: List[Dict[str, object]] = []
    if not root.exists():
        return entries
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or not _is_ts(path.name):
            continue
        info: Dict[str, object] = {
            "ts": path.name,
            "dir": str(path),
            "files": 0,
            "bytes": 0,
            "created_at": None,
        }
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            info["files"] = len(manifest.get("files", []))
            info["bytes"] = manifest.get("total_bytes", 0)
            info["created_at"] = manifest.get("created_at")
        except (OSError, ValueError):
            pass
        entries.append(info)
    return entries


def run_backup(
    *,
    backup_dir: Path,
    db_root: Path,
    keep_daily: int = 14,
    keep_weekly: int = 4,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Take one snapshot of every DB and prune old ones. Returns the report."""
    now = now or datetime.now(_UTC8)
    ts = now.strftime(_TS_FORMAT)
    dest_root = backup_dir / "sqlite" / ts

    files: List[Dict[str, object]] = []
    total_bytes = 0
    for src, rel in _iter_db_sources(db_root):
        size = _backup_one(src, dest_root / rel)
        if size is None:
            logger.info("backup: skipping missing %s", src)
            continue
        files.append({"path": str(rel), "bytes": size})
        total_bytes += size
    if not files:
        raise RuntimeError(f"nothing to back up under {db_root}")

    manifest = {
        "ts": ts,
        "created_at": now.isoformat(),
        "db_root": str(db_root),
        "files": files,
        "total_bytes": total_bytes,
    }
    (dest_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    pruned = prune_backups(backup_dir, keep_daily=keep_daily, keep_weekly=keep_weekly)
    logger.info(
        "backup: %s files=%d bytes=%d pruned=%d", ts, len(files), total_bytes, len(pruned)
    )
    return {
        "ts": ts,
        "dir": str(dest_root),
        "files": len(files),
        "bytes": total_bytes,
        "pruned": len(pruned),
        "pruned_ts": pruned,
    }


def restore_backup(ts: str, *, backup_dir: Path, db_root: Path) -> Dict[str, object]:
    """Replace the live DB files with snapshot *ts* (app must be stopped)."""
    src_root = backup_dir / "sqlite" / ts
    if not src_root.is_dir():
        raise FileNotFoundError(f"backup {ts} not found under {backup_dir}/sqlite")
    if not (src_root / "manifest.json").exists():
        raise FileNotFoundError(f"backup {ts} has no manifest.json; refusing to restore")

    restored: List[str] = []
    # Wholesale replacement per DB family: a point-in-time restore must not
    # leave behind newer shard files that the snapshot does not contain. A
    # family absent from the snapshot (e.g. jobs/ never existed) is left alone.
    for family in ("main", "jobs", "plans"):
        live_dir = db_root / family
        snap_dir = src_root / family
        if not snap_dir.is_dir():
            continue
        live_dir.mkdir(parents=True, exist_ok=True)
        for live in sorted(live_dir.iterdir()):
            if live.is_dir():
                continue
            if live.name.endswith(_STALE_SUFFIXES) or not (snap_dir / live.name).exists():
                live.unlink()
        for snap in sorted(snap_dir.iterdir()):
            if not snap.is_file():
                continue
            dest = live_dir / snap.name
            dest.unlink(missing_ok=True)
            shutil.copy2(snap, dest)
            restored.append(str(dest))

    logger.info("backup: restored %s (%d files) into %s", ts, len(restored), db_root)
    return {"ts": ts, "restored": len(restored), "files": restored}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.backup",
        description="SQLite online backups (VACUUM INTO) and point-in-time restore.",
    )
    parser.add_argument("--list", action="store_true", help="list snapshots and exit")
    parser.add_argument(
        "--restore", metavar="TS", default=None,
        help="restore snapshot TS (the app must be stopped first)",
    )
    parser.add_argument("--backup-dir", default=None, help="override BACKUP_DIR")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from app.config.database_config import get_database_config
    from app.services.foundation.settings import get_settings

    settings = get_settings()
    db = get_database_config()
    backup_dir = Path(args.backup_dir) if args.backup_dir else Path(settings.backup_dir)

    if args.list:
        entries = list_backups(backup_dir)
        if not entries:
            print(f"no backups under {backup_dir}/sqlite")
        for entry in entries:
            print(
                f"{entry['ts']}  files={entry['files']}  "
                f"bytes={entry['bytes']}  created_at={entry['created_at']}"
            )
        return 0

    if args.restore:
        result = restore_backup(args.restore, backup_dir=backup_dir, db_root=Path(db.db_root))
        print(f"restored {result['restored']} files from snapshot {result['ts']}")
        return 0

    result = run_backup(
        backup_dir=backup_dir,
        db_root=Path(db.db_root),
        keep_daily=settings.backup_keep_daily,
        keep_weekly=settings.backup_keep_weekly,
    )
    print(
        f"backup {result['ts']}: files={result['files']} bytes={result['bytes']} "
        f"pruned={result['pruned']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - service entrypoint
    sys.exit(main())
