"""Age/size-based retention for runtime session dirs and SQLite log tables.

WHY THIS EXISTS
The production host accumulated 156 GB under the runtime tree with no reaper
(measured 2026-10-10): nothing ever deleted a session directory, and the main
DB's event/signal/usage tables plus every plan shard's action/job logs grow
without bound. This module is the reaper, run by the ``ops`` compose service
daily at 03:00 (UTC+8) through ``app.ops.cron``.

SAFETY MODEL
- Dry-run by default. ``python -m app.ops.retention`` only *reports*; deletion
  requires ``--apply`` (the cron passes it explicitly).
- Never delete a session with live work: a session is protected while it has a
  chat_run that is queued/running with a lease that has not expired (the exact
  inverse of ``app.repository.chat_runs.reap_expired_chat_runs``) or a
  decomposition job queued/running in any plan shard or in the system-jobs DB
  (a superset of the deploy gate's shard scan in ``deploy/gagentctl`` — the GC
  is deliberately more protective than the gate). If the active-check queries
  fail (corrupt/locked DB), the session GC aborts entirely rather than
  guessing; a missing table means "no such work" and is not a failure.
- Archive before delete: with ``RUNTIME_ARCHIVE_BEFORE_DELETE`` (default on)
  each session dir is tar.gz'd into ``<backup_dir>/archive/<ts>/`` first; if
  the archive fails, that directory is kept.
- Only ``session_*`` directories under the runtime root are candidates. The
  legacy ``data/information_sessions`` tree and hand-made directories are out
  of scope on purpose (they are a manual-archive concern, not a cron concern;
  see LOCAL_INFRA on the ``results/`` / ``phagescope/`` legacy trees).

TIMESTAMP CONVENTIONS (do not "normalize" these without changing the writers)
- ``chat_run_events`` / ``chat_run_signals``: SQLite ``CURRENT_TIMESTAMP`` =
  UTC, compared in SQL via ``datetime('now', '-N days')``.
- ``llm_usage_log.created_at``: written as ``datetime.now().isoformat()`` by
  ``app.repository.llm_usage`` (naive container-local; UTC in this stack), so
  the cutoff is computed the same way and compared lexicographically.
- ``chat_sessions`` / ``chat_runs``: SQLite UTC; parsed as UTC when read.
- Plan-shard logs: ``plan_action_logs.created_at`` and
  ``decomposition_job_logs.timestamp`` are both written UTC+8; their cutoffs
  live in ``app.repository.plan_storage`` (``cleanup_action_logs`` /
  ``cleanup_decomposition_job_logs``), which this module calls in apply mode.

Run: ``python -m app.ops.retention [--apply] [--vacuum]`` (dry-run otherwise).
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from app.config.database_config import get_database_config
from app.repository.chat_runs import DEFAULT_LEASE_TTL_SECONDS
from app.services.foundation.settings import get_settings
from app.services.foundation.sqlite_pragmas import apply_sqlite_pragmas
from app.services.session_paths import get_runtime_root

logger = logging.getLogger("app.ops.retention")

# Beijing time: no DST, so a fixed offset is exact (and avoids tzdata lookups
# that slim images may not ship). Matches the shard-log writers' convention.
_UTC8 = timezone(timedelta(hours=8))

# Consumed chat_run_signals are pure housekeeping once the run is terminal;
# the plan fixed the window at 7 days (not a settings knob on purpose).
SIGNAL_CONSUMED_RETENTION_DAYS = 7


@dataclass
class SessionRecord:
    """One ``session_*`` directory with its activity, size, and protection."""

    session_dir: Path
    size_bytes: int
    last_activity_epoch: float
    last_activity_source: str  # "dir-mtime" | "chat_sessions"
    active_reason: str = ""  # "" when not active

    @property
    def active(self) -> bool:
        return bool(self.active_reason)


def _dir_size_bytes(path: Path) -> int:
    """Total file size under *path* (symlinks not followed)."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(root) / name).stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _parse_db_timestamp(value: object) -> Optional[float]:
    """Parse the timestamp formats this codebase writes; naive means UTC."""
    text = str(value or "").strip()
    if not text:
        return None
    for parser in (
        lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S"),  # SQLite CURRENT_TIMESTAMP
        lambda v: datetime.fromisoformat(v),  # Python isoformat writers
    ):
        try:
            parsed = parser(text)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _table_missing(exc: sqlite3.Error) -> bool:
    return "no such table" in str(exc).lower()


def collect_active_session_ids(main_db: Path, plan_store_dir: Path, system_db: Path) -> Set[str]:
    """Session ids with live work. Raises ``sqlite3.Error`` on query failure.

    Active chat runs mirror ``reap_expired_chat_runs`` exactly (a row is alive
    while its lease has not expired, or it never got a lease and is younger
    than the TTL grace window). Decomposition jobs use the same queued/running
    criterion as the deploy gate, scanned across every plan shard and the
    system-jobs DB. A missing table is treated as "no such work".
    """
    active: Set[str] = set()

    if main_db.exists():
        conn = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
        try:
            ttl = max(5, int(DEFAULT_LEASE_TTL_SECONDS))
            rows = conn.execute(
                """
                SELECT DISTINCT session_id FROM chat_runs
                WHERE status IN ('queued', 'running')
                  AND (
                        (lease_expires_at IS NOT NULL AND lease_expires_at > datetime('now'))
                     OR (lease_expires_at IS NULL
                         AND created_at >= datetime('now', ?))
                  )
                """,
                (f"-{ttl} seconds",),
            ).fetchall()
            active.update(str(row[0]) for row in rows if row[0])
        except sqlite3.Error as exc:
            if not _table_missing(exc):
                raise
        finally:
            conn.close()

    shard_paths: List[Path] = []
    if plan_store_dir.exists():
        shard_paths.extend(sorted(plan_store_dir.glob("plan_*.sqlite")))
    if system_db.exists():
        shard_paths.append(system_db)
    for shard in shard_paths:
        conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT session_id FROM decomposition_jobs "
                "WHERE status IN ('queued', 'running')"
            ).fetchall()
            active.update(str(row[0]) for row in rows if row[0])
        except sqlite3.Error as exc:
            if not _table_missing(exc):
                raise
        finally:
            conn.close()

    return active


def scan_sessions(
    runtime_root: Path,
    main_db: Path,
    plan_store_dir: Path,
    system_db: Path,
) -> "tuple[List[SessionRecord], Optional[str]]":
    """Enumerate session dirs with size, last activity, and protection.

    Returns ``(records, abort_reason)``. A non-None abort_reason means the
    active-check queries failed and the caller must not delete anything.
    """
    try:
        active_ids = collect_active_session_ids(main_db, plan_store_dir, system_db)
    except sqlite3.Error as exc:
        return [], f"active-check query failed: {exc}"

    records: List[SessionRecord] = []
    if not runtime_root.exists():
        return records, None

    for entry in sorted(runtime_root.glob("session_*")):
        if entry.is_symlink() or not entry.is_dir():
            continue
        base = entry.name[len("session_"):]
        if not base:
            continue

        db_epoch: Optional[float] = None
        if main_db.exists():
            conn = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT last_message_at, created_at, updated_at FROM chat_sessions "
                    "WHERE id IN (?, ?, ?)",
                    (base, f"session_{base}", f"session-{base}"),
                ).fetchall()
                for row in rows:
                    for value in row:
                        parsed = _parse_db_timestamp(value)
                        if parsed is not None and (db_epoch is None or parsed > db_epoch):
                            db_epoch = parsed
            except sqlite3.Error as exc:
                if not _table_missing(exc):
                    return [], f"chat_sessions query failed: {exc}"
            finally:
                conn.close()

        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if db_epoch is not None and db_epoch > mtime:
            last_activity, source = db_epoch, "chat_sessions"
        else:
            last_activity, source = mtime, "dir-mtime"

        candidates = {base, f"session_{base}", f"session-{base}"}
        reason = "active-run-or-job" if candidates & active_ids else ""

        # Walk sizes for every record: the size-cap pass needs the true total
        # on disk, active sessions included, or the cap math understates usage.
        records.append(
            SessionRecord(
                session_dir=entry,
                size_bytes=_dir_size_bytes(entry),
                last_activity_epoch=last_activity,
                last_activity_source=source,
                active_reason=reason,
            )
        )
    return records, None


def select_expired(
    records: Iterable[SessionRecord], *, retention_days: int, now_epoch: float
) -> List[SessionRecord]:
    """Non-active records idle for more than *retention_days*."""
    cutoff = now_epoch - max(0, int(retention_days)) * 86400
    return [r for r in records if not r.active and r.last_activity_epoch < cutoff]


def select_for_size_cap(
    records: Iterable[SessionRecord],
    *,
    already_selected: Iterable[SessionRecord],
    max_bytes: int,
    now_epoch: float,
) -> List[SessionRecord]:
    """Oldest-first extra deletions until the tree fits under *max_bytes*.

    Runs after the age pass: if the tree is still over the cap, sessions are
    removed by age regardless of the retention window (never active ones).
    """
    _ = now_epoch
    records = list(records)
    selected_dirs = {r.session_dir for r in already_selected}
    total = sum(r.size_bytes for r in records)
    projected = total - sum(r.size_bytes for r in records if r.session_dir in selected_dirs)
    if projected <= max_bytes:
        return []
    extra: List[SessionRecord] = []
    remaining = sorted(
        (r for r in records if not r.active and r.session_dir not in selected_dirs),
        key=lambda r: r.last_activity_epoch,
    )
    for record in remaining:
        if projected <= max_bytes:
            break
        extra.append(record)
        projected -= record.size_bytes
    return extra


def archive_session_dir(session_dir: Path, archive_dir: Path) -> Path:
    """tar.gz one session directory into *archive_dir*; returns the archive path."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{session_dir.name}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        tar.add(session_dir, arcname=session_dir.name)
    return target


def _parse_archive_ts(name: str) -> Optional[float]:
    """Epoch seconds for a ``<backup>/archive/<ts>`` batch name, or None."""
    try:
        return datetime.strptime(name, "%Y%m%dT%H%M%S").replace(tzinfo=_UTC8).timestamp()
    except ValueError:
        return None


def prune_session_archives(
    archive_root: Path,
    *,
    keep_days: int,
    keep_min: int = 2,
    now: Optional[datetime] = None,
    apply: bool = False,
) -> Dict[str, object]:
    """Delete session-archive batches older than *keep_days*; newest *keep_min* stay.

    ``run_retention`` archives every expired session directory into
    ``<backup_dir>/archive/<ts>/`` before removing the original, and nothing
    ever removed those batches afterwards: retention moved the bytes off the
    runtime tree but never freed them. Batch names are ``%Y%m%dT%H%M%S`` in
    UTC+8 (the cron clock). Names that do not parse are never deleted, and the
    newest batches are always kept so the pass that just wrote one cannot race
    itself. Dry-run unless *apply*.
    """
    now = now or datetime.now(_UTC8)
    report: Dict[str, object] = {
        "archives_total": 0,
        "archives_selected": 0,
        "archives_deleted": 0,
        "archive_bytes_freed": 0,
    }
    if not archive_root.is_dir():
        return report

    batches: List[tuple[float, Path]] = []
    for entry in sorted(archive_root.iterdir()):
        if not entry.is_dir():
            continue
        stamp = _parse_archive_ts(entry.name)
        if stamp is None:
            logger.warning("retention: unrecognized archive batch name %s; keeping", entry.name)
            continue
        batches.append((stamp, entry))
    report["archives_total"] = len(batches)
    if not batches:
        return report

    batches.sort(key=lambda item: item[0])
    protected = {entry for _stamp, entry in batches[-max(0, int(keep_min)):]} if keep_min else set()
    cutoff = now.timestamp() - max(0, int(keep_days)) * 86400
    selected = [entry for stamp, entry in batches if stamp < cutoff and entry not in protected]
    report["archives_selected"] = len(selected)

    for entry in selected:
        size = _dir_size_bytes(entry)
        if not apply:
            logger.info(
                "retention: would prune archive batch %s (%.1f MiB)", entry.name, size / 1024**2
            )
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            logger.error("retention: archive prune failed for %s: %s", entry.name, exc)
            continue
        report["archives_deleted"] = int(report["archives_deleted"]) + 1
        report["archive_bytes_freed"] = int(report["archive_bytes_freed"]) + size
        logger.info("retention: pruned archive batch %s (%.1f MiB)", entry.name, size / 1024**2)
    return report


def effective_cap_gb(
    *,
    runtime_bytes: int,
    free_bytes: int,
    max_gb: float,
    free_floor_gb: float,
) -> float:
    """The size cap the pass should use: ``RUNTIME_MAX_GB`` bounded by free space.

    A static cap cannot protect a disk it was not calibrated for. On .8 the
    runtime tree shares a 500 GiB filesystem with the course platform and the
    Docker root, so ``RUNTIME_MAX_GB=300`` can only fire after the disk is
    nearly full. The effective cap is therefore also bounded by what the tree
    may grow into while still leaving *free_floor_gb* of headroom.
    """
    if free_floor_gb <= 0:
        return max(0.0, float(max_gb))
    headroom_gb = max(0.0, (int(runtime_bytes) + int(free_bytes)) / 1024**3 - float(free_floor_gb))
    return max(0.0, min(float(max_gb), headroom_gb))


def disk_usage_report(
    runtime_root: Path,
    *,
    warn_pct: float = 20.0,
    error_pct: float = 10.0,
) -> Dict[str, object]:
    """Free-space facts for the filesystem holding the runtime tree.

    Retention is the only component that ever looks at this disk, so it is also
    the natural place to say something before the disk fills.
    """
    try:
        usage = shutil.disk_usage(runtime_root)
    except OSError as exc:
        return {"disk_error": str(exc)}
    free_pct = (usage.free / usage.total * 100.0) if usage.total else 0.0
    if free_pct < float(error_pct):
        level = "error"
    elif free_pct < float(warn_pct):
        level = "warn"
    else:
        level = "ok"
    return {
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "disk_free_pct": round(free_pct, 1),
        "disk_level": level,
    }


def _open_maintenance_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    apply_sqlite_pragmas(conn)
    return conn


def _count_and_maybe_delete(
    conn: sqlite3.Connection,
    *,
    table: str,
    where_sql: str,
    params: tuple,
    apply: bool,
) -> int:
    """Count matching rows; delete them when *apply*. Missing table -> 0."""
    try:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where_sql}", params
        ).fetchone()[0]
    except sqlite3.Error as exc:
        if _table_missing(exc):
            return 0
        raise
    if apply and count:
        conn.execute(f"DELETE FROM {table} WHERE {where_sql}", params)
        conn.commit()
    return int(count)


def _retain_main_db(
    main_db: Path,
    *,
    backup_dir: Optional[Path],
    event_retention_days: int,
    usage_retention_days: int,
    ts: str,
    apply: bool,
) -> Dict[str, object]:
    """Event/signal/usage retention on the main DB. Returns report fields."""
    result: Dict[str, object] = {
        "events_rows": 0,
        "signals_rows": 0,
        "usage_rows": 0,
        "usage_csv": None,
    }
    if not main_db.exists():
        return result

    conn = _open_maintenance_connection(main_db)
    try:
        # Events + signals are SQLite-UTC (CURRENT_TIMESTAMP); compare in SQL.
        result["events_rows"] = _count_and_maybe_delete(
            conn,
            table="chat_run_events",
            where_sql="created_at < datetime('now', ?)",
            params=(f"-{max(0, int(event_retention_days))} days",),
            apply=apply,
        )
        result["signals_rows"] = _count_and_maybe_delete(
            conn,
            table="chat_run_signals",
            where_sql="consumed_at IS NOT NULL AND consumed_at < datetime('now', ?)",
            params=(f"-{SIGNAL_CONSUMED_RETENTION_DAYS} days",),
            apply=apply,
        )

        # llm_usage_log.created_at is naive-local isoformat; cut off in Python
        # with the same call so the string formats compare lexicographically.
        usage_cutoff = (datetime.now() - timedelta(days=max(0, int(usage_retention_days)))).isoformat()
        try:
            usage_rows = conn.execute(
                "SELECT COUNT(*) FROM llm_usage_log WHERE created_at < ?",
                (usage_cutoff,),
            ).fetchone()[0]
        except sqlite3.Error as exc:
            if not _table_missing(exc):
                raise
            usage_rows = 0
        result["usage_rows"] = int(usage_rows)
        if apply and usage_rows:
            if backup_dir is None:
                logger.warning(
                    "retention: %d llm_usage_log rows are past the window but no "
                    "backup dir is configured; keeping them (export first)",
                    usage_rows,
                )
            else:
                usage_dir = backup_dir / "usage"
                usage_dir.mkdir(parents=True, exist_ok=True)
                csv_path = usage_dir / f"llm_usage_{ts}.csv"
                columns = [
                    row[1] for row in conn.execute("PRAGMA table_info(llm_usage_log)").fetchall()
                ]
                rows = conn.execute(
                    "SELECT * FROM llm_usage_log WHERE created_at < ? ORDER BY id",
                    (usage_cutoff,),
                ).fetchall()
                with open(csv_path, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(columns)
                    writer.writerows(rows)
                conn.execute("DELETE FROM llm_usage_log WHERE created_at < ?", (usage_cutoff,))
                conn.commit()
                result["usage_csv"] = str(csv_path)
    finally:
        conn.close()
    return result


def _shard_paths(plan_store_dir: Path, system_db: Path) -> List[Path]:
    shards: List[Path] = []
    if plan_store_dir.exists():
        shards.extend(sorted(plan_store_dir.glob("plan_*.sqlite")))
    if system_db.exists():
        shards.append(system_db)
    return shards


def _plan_id_for_shard(shard: Path, system_db: Path) -> Optional[int]:
    if shard == system_db:
        return None
    stem = shard.stem  # plan_<id>
    try:
        return int(stem.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _retain_shard_logs(
    plan_store_dir: Path,
    system_db: Path,
    *,
    job_log_retention_days: int,
    job_log_max_rows: int,
    apply: bool,
) -> Dict[str, object]:
    """Age/cap cleanup of plan_action_logs + decomposition_job_logs per shard.

    Both tables are written with UTC+8 timestamps (see the module docstring),
    so the dry-run counts use the same cutoff convention as the cleaners.
    Apply mode delegates the deletes to ``app.repository.plan_storage``.
    """
    from app.repository.plan_storage import (
        cleanup_action_logs,
        cleanup_decomposition_job_logs,
    )

    result: Dict[str, object] = {"shards_scanned": 0, "shard_failures": 0, "shard_log_rows": 0}
    cutoff = (
        datetime.now(timezone(timedelta(hours=8)))
        - timedelta(days=max(0, int(job_log_retention_days)))
    ).isoformat()
    for shard in _shard_paths(plan_store_dir, system_db):
        result["shards_scanned"] = int(result["shards_scanned"]) + 1
        try:
            conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
            try:
                for table, column in (
                    ("plan_action_logs", "created_at"),
                    ("decomposition_job_logs", "timestamp"),
                ):
                    try:
                        rows = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {column} < ?", (cutoff,)
                        ).fetchone()[0]
                    except sqlite3.Error as exc:
                        if not _table_missing(exc):
                            raise
                        rows = 0
                    result["shard_log_rows"] = int(result["shard_log_rows"]) + int(rows)
            finally:
                conn.close()
            if apply:
                plan_id = _plan_id_for_shard(shard, system_db)
                if plan_id is None and shard != system_db:
                    # A plan_*.sqlite whose name does not parse would otherwise
                    # be cleaned as plan_id=None — i.e. against the system-jobs
                    # DB. Skip it instead.
                    logger.warning(
                        "retention: unrecognized shard name %s; skipping cleanup", shard.name
                    )
                    continue
                cleanup_action_logs(
                    plan_id,
                    older_than_days=job_log_retention_days,
                    max_rows=job_log_max_rows,
                )
                cleanup_decomposition_job_logs(
                    plan_id,
                    older_than_days=job_log_retention_days,
                    max_rows=job_log_max_rows,
                )
        except Exception as exc:  # keep one bad shard from sinking the run
            result["shard_failures"] = int(result["shard_failures"]) + 1
            logger.warning("retention: shard cleanup failed for %s: %s", shard.name, exc)
    return result


def _checkpoint(path: Path) -> bool:
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error as exc:
        logger.warning("retention: wal_checkpoint failed for %s: %s", path.name, exc)
        return False


def _vacuum(path: Path) -> bool:
    try:
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
        return True
    except sqlite3.Error as exc:
        logger.warning("retention: VACUUM failed for %s: %s", path.name, exc)
        return False


def run_retention(
    *,
    runtime_root: Path,
    backup_dir: Optional[Path],
    main_db: Path,
    plan_store_dir: Path,
    system_db: Path,
    retention_days: int = 60,
    max_gb: float = 300.0,
    archive_before_delete: bool = True,
    archive_keep_days: int = 90,
    archive_keep_min: int = 2,
    free_floor_gb: float = 40.0,
    disk_warn_pct: float = 20.0,
    disk_error_pct: float = 10.0,
    event_retention_days: int = 30,
    usage_retention_days: int = 365,
    job_log_retention_days: int = 30,
    job_log_max_rows: int = 10000,
    apply: bool = False,
    vacuum: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """One retention pass; dry-run unless *apply*. Returns the report dict."""
    now = now or datetime.now(_UTC8)
    now_epoch = now.timestamp()
    ts = now.strftime("%Y%m%dT%H%M%S")
    report: Dict[str, object] = {
        "dry_run": not apply,
        "runtime_root": str(runtime_root),
        "backup_dir": str(backup_dir) if backup_dir else None,
        "sessions_total": 0,
        "sessions_active": 0,
        "sessions_selected_by_age": 0,
        "sessions_selected_by_cap": 0,
        "sessions_deleted": 0,
        "sessions_kept_by_archive_failure": 0,
        "bytes_selected": 0,
        "bytes_freed": 0,
        "runtime_bytes": 0,
        "cap_gb_effective": 0.0,
        "archives_total": 0,
        "archives_selected": 0,
        "archives_deleted": 0,
        "archive_bytes_freed": 0,
        "events_rows": 0,
        "signals_rows": 0,
        "usage_rows": 0,
        "usage_csv": None,
        "shards_scanned": 0,
        "shard_failures": 0,
        "shard_log_rows": 0,
        "checkpointed": 0,
        "vacuumed": 0,
        "abort_reason": None,
        "main_db_error": None,
    }

    # ---- 1. session directories -------------------------------------------
    records, abort_reason = scan_sessions(runtime_root, main_db, plan_store_dir, system_db)
    report["sessions_total"] = len(records)
    report["sessions_active"] = sum(1 for r in records if r.active)
    if abort_reason is not None:
        report["abort_reason"] = abort_reason
        logger.warning("retention: session GC aborted (%s)", abort_reason)
    else:
        runtime_bytes = sum(r.size_bytes for r in records)
        free_bytes = 0
        try:
            free_bytes = shutil.disk_usage(runtime_root).free
        except OSError as exc:
            logger.warning("retention: disk usage unavailable for %s: %s", runtime_root, exc)
        cap_gb = effective_cap_gb(
            runtime_bytes=runtime_bytes,
            free_bytes=free_bytes,
            max_gb=max_gb,
            free_floor_gb=free_floor_gb,
        )
        report["runtime_bytes"] = runtime_bytes
        report["cap_gb_effective"] = round(cap_gb, 1)
        by_age = select_expired(records, retention_days=retention_days, now_epoch=now_epoch)
        by_cap = select_for_size_cap(
            records,
            already_selected=by_age,
            max_bytes=int(cap_gb * 1024**3),
            now_epoch=now_epoch,
        )
        selected = sorted(
            by_age + by_cap, key=lambda r: r.last_activity_epoch
        )  # oldest first
        report["sessions_selected_by_age"] = len(by_age)
        report["sessions_selected_by_cap"] = len(by_cap)
        report["bytes_selected"] = sum(r.size_bytes for r in selected)

        archive_dir = (backup_dir / "archive" / ts) if backup_dir is not None else None
        for record in selected:
            idle_days = (now_epoch - record.last_activity_epoch) / 86400.0
            logger.info(
                "retention: %s session %s (%.1f MiB, idle %.1f d, via %s)",
                "would delete" if not apply else "deleting",
                record.session_dir.name,
                record.size_bytes / 1024**2,
                idle_days,
                record.last_activity_source,
            )
            if not apply:
                continue
            if archive_before_delete:
                if archive_dir is None:
                    report["sessions_kept_by_archive_failure"] = (
                        int(report["sessions_kept_by_archive_failure"]) + 1
                    )
                    logger.error(
                        "retention: no backup dir configured; keeping %s",
                        record.session_dir.name,
                    )
                    continue
                try:
                    archive_session_dir(record.session_dir, archive_dir)
                except Exception as exc:
                    report["sessions_kept_by_archive_failure"] = (
                        int(report["sessions_kept_by_archive_failure"]) + 1
                    )
                    logger.error(
                        "retention: archive failed for %s (%s); keeping directory",
                        record.session_dir.name,
                        exc,
                    )
                    continue
            try:
                shutil.rmtree(record.session_dir)
            except OSError as exc:
                logger.error("retention: rmtree failed for %s: %s", record.session_dir, exc)
                continue
            report["sessions_deleted"] = int(report["sessions_deleted"]) + 1
            report["bytes_freed"] = int(report["bytes_freed"]) + record.size_bytes

    # ---- 2. expired session archives --------------------------------------
    # Sessions deleted by earlier passes were archived and never pruned, so the
    # bytes only moved off the runtime tree. Apply the same window to them.
    if backup_dir is not None:
        report.update(
            prune_session_archives(
                backup_dir / "archive",
                keep_days=archive_keep_days,
                keep_min=archive_keep_min,
                now=now,
                apply=apply,
            )
        )

    # ---- 3. main DB log tables --------------------------------------------
    # A corrupt/locked main DB must not take the shard cleanup (or the report)
    # down with it; the failure is surfaced in the report and logs instead.
    try:
        report.update(
            _retain_main_db(
                main_db,
                backup_dir=backup_dir,
                event_retention_days=event_retention_days,
                usage_retention_days=usage_retention_days,
                ts=ts,
                apply=apply,
            )
        )
    except sqlite3.Error as exc:
        logger.error("retention: main DB retention failed: %s", exc)
        report["main_db_error"] = str(exc)

    # ---- 4. plan shard logs ------------------------------------------------
    report.update(
        _retain_shard_logs(
            plan_store_dir,
            system_db,
            job_log_retention_days=job_log_retention_days,
            job_log_max_rows=job_log_max_rows,
            apply=apply,
        )
    )

    # ---- 5. WAL checkpoint + weekly VACUUM (apply only) --------------------
    if apply:
        checkpointed = 0
        vacuumed = 0
        targets = [main_db] if main_db.exists() else []
        targets.extend(_shard_paths(plan_store_dir, system_db))
        for target in targets:
            if _checkpoint(target):
                checkpointed += 1
            if vacuum and _vacuum(target):
                vacuumed += 1
        report["checkpointed"] = checkpointed
        report["vacuumed"] = vacuumed
    elif vacuum:
        logger.info("retention: --vacuum ignored in dry-run (needs --apply)")

    report.update(
        disk_usage_report(runtime_root, warn_pct=disk_warn_pct, error_pct=disk_error_pct)
    )
    free_gib = float(report.get("disk_free_bytes") or 0) / 1024**3
    free_pct = float(report.get("disk_free_pct") or 0.0)
    if report.get("disk_level") == "error":
        logger.error(
            "retention: data disk free %.1f%% (%.1f GiB) is below the error threshold",
            free_pct,
            free_gib,
        )
    elif report.get("disk_level") == "warn":
        logger.warning(
            "retention: data disk free %.1f%% (%.1f GiB) is below the warning threshold",
            free_pct,
            free_gib,
        )

    mode = "apply" if apply else "DRY-RUN"
    logger.info(
        "retention[%s]: sessions total=%s active=%s selected(age=%s cap=%s) "
        "deleted=%s kept(archive-fail)=%s freed=%.1f MiB",
        mode,
        report["sessions_total"],
        report["sessions_active"],
        report["sessions_selected_by_age"],
        report["sessions_selected_by_cap"],
        report["sessions_deleted"],
        report["sessions_kept_by_archive_failure"],
        int(report["bytes_freed"]) / 1024**2,
    )
    logger.info(
        "retention[%s]: runtime=%.1f GiB cap_effective=%.1f GiB archives "
        "total=%s selected=%s deleted=%s freed=%.1f MiB disk_free=%.1f%%",
        mode,
        float(report.get("runtime_bytes") or 0) / 1024**3,
        float(report.get("cap_gb_effective") or 0.0),
        report.get("archives_total"),
        report.get("archives_selected"),
        report.get("archives_deleted"),
        float(report.get("archive_bytes_freed") or 0) / 1024**2,
        free_pct,
    )
    logger.info(
        "retention[%s]: db events=%s signals=%s usage=%s shard_log_rows=%s "
        "(shards=%s failures=%s) checkpointed=%s vacuumed=%s",
        mode,
        report["events_rows"],
        report["signals_rows"],
        report["usage_rows"],
        report["shard_log_rows"],
        report["shards_scanned"],
        report["shard_failures"],
        report["checkpointed"],
        report["vacuumed"],
    )
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.retention",
        description="Runtime session + DB log retention (dry-run unless --apply).",
    )
    parser.add_argument(
        "--apply", action="store_true", help="execute deletions (default: report only)"
    )
    parser.add_argument(
        "--vacuum", action="store_true", help="also VACUUM main DB + shards (weekly in cron)"
    )
    parser.add_argument("--runtime-root", default=None, help="override APP_RUNTIME_ROOT")
    parser.add_argument("--backup-dir", default=None, help="override BACKUP_DIR")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    db = get_database_config()
    runtime_root = Path(args.runtime_root) if args.runtime_root else get_runtime_root()
    backup_dir = Path(args.backup_dir) if args.backup_dir else Path(settings.backup_dir)
    run_retention(
        runtime_root=runtime_root,
        backup_dir=backup_dir,
        main_db=Path(db.get_main_db_path()),
        plan_store_dir=db.get_plan_store_dir(),
        system_db=Path(db.get_system_jobs_db_path()),
        retention_days=settings.runtime_retention_days,
        max_gb=settings.runtime_max_gb,
        archive_before_delete=settings.runtime_archive_before_delete,
        archive_keep_days=settings.runtime_archive_keep_days,
        free_floor_gb=settings.runtime_free_floor_gb,
        disk_warn_pct=settings.runtime_disk_warn_pct,
        disk_error_pct=settings.runtime_disk_error_pct,
        event_retention_days=settings.event_retention_days,
        usage_retention_days=settings.usage_retention_days,
        job_log_retention_days=settings.job_log_retention_days,
        job_log_max_rows=settings.job_log_max_rows,
        apply=args.apply,
        vacuum=args.vacuum,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - service entrypoint
    sys.exit(main())
