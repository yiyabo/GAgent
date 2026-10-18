"""The ``ops`` compose service: daily backup + retention on a sleep loop.

WHY THIS EXISTS
``app.ops.retention`` and ``app.ops.backup`` are one-shot commands; something
has to run them on a schedule. A host cron entry would work, but the stack's
contract is "compose up is the whole install", so the schedule lives inside
the stack as this tiny service (same image as the app, entrypoint swapped).
It sleeps until the next 03:00 Beijing time, then runs:

    1. ``backup.run_backup``     — VACUUM INTO snapshots + prune old ones
    2. ``retention.run_retention --apply`` — session GC + DB log retention
    3. on Sundays, the same retention pass also VACUUMs the DBs

Failure policy: a failed run is logged with its traceback and the loop simply
waits for the next 03:00. No partial-state repair here — each sub-step is
already fail-safe on its own (see the modules' docstrings).

TIMEZONE
Beijing time via a fixed UTC+8 offset: no DST, exact, and no tzdata lookup
that a slim image might not ship. The container needs no TZ env var.

Run: ``python -m app.ops.cron`` (service entrypoint; loops forever).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("app.ops.cron")

# Daily run hour in Beijing time. 03:00 keeps the (briefly locking) SQLite
# maintenance and the session-tree walk far away from working hours.
_RUN_HOUR = 3
_UTC8 = timezone(timedelta(hours=8))


def seconds_until_next_run(now: datetime, hour: int = _RUN_HOUR) -> float:
    """Seconds from *now* (tz-aware) to the next ``hour``:00:00 in UTC+8."""
    local = now.astimezone(_UTC8)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return (target - local).total_seconds()


def run_once(
    *,
    apply: bool = True,
    vacuum: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """One ops pass: backup first, then retention. *vacuum* defaults to Sunday."""
    from app.config.database_config import get_database_config
    from app.ops import backup as backup_module
    from app.ops import retention as retention_module
    from app.services.foundation.settings import get_settings
    from app.services.session_paths import get_runtime_root

    settings = get_settings()
    db = get_database_config()
    now = now or datetime.now(_UTC8)
    if vacuum is None:
        vacuum = now.astimezone(_UTC8).weekday() == 6  # Sunday

    backup_dir = Path(settings.backup_dir)
    backup_report = backup_module.run_backup(
        backup_dir=backup_dir,
        db_root=Path(db.db_root),
        keep_daily=settings.backup_keep_daily,
        keep_weekly=settings.backup_keep_weekly,
        now=now,
    )
    retention_report = retention_module.run_retention(
        runtime_root=get_runtime_root(),
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
        apply=apply,
        vacuum=vacuum,
        now=now,
    )
    return {"backup": backup_report, "retention": retention_report}


def main(argv: Optional[list] = None) -> int:
    _ = argv
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("ops cron starting; daily run at %02d:00 UTC+8 (Sundays add VACUUM)", _RUN_HOUR)
    while True:
        wait_seconds = seconds_until_next_run(datetime.now(_UTC8))
        logger.info("ops cron: next run in %.0f s", wait_seconds)
        time.sleep(wait_seconds)
        try:
            report = run_once(apply=True)
            backup_report = report["backup"]  # type: ignore[index]
            retention_report = report["retention"]  # type: ignore[index]
            logger.info(
                "ops cron: run complete (backup ts=%s files=%s pruned=%s)",
                backup_report.get("ts"),  # type: ignore[union-attr]
                backup_report.get("files"),  # type: ignore[union-attr]
                backup_report.get("pruned"),  # type: ignore[union-attr]
            )
            logger.info(
                "ops cron: retention disk_free=%s%% level=%s cap_effective=%s GiB "
                "archives_deleted=%s archives_freed=%s B",
                retention_report.get("disk_free_pct"),  # type: ignore[union-attr]
                retention_report.get("disk_level"),  # type: ignore[union-attr]
                retention_report.get("cap_gb_effective"),  # type: ignore[union-attr]
                retention_report.get("archives_deleted"),  # type: ignore[union-attr]
                retention_report.get("archive_bytes_freed"),  # type: ignore[union-attr]
            )
            logger.info(
                "ops cron: retention sessions_deleted=%s bytes_freed=%s usage_rows=%s",
                retention_report.get("sessions_deleted"),  # type: ignore[union-attr]
                retention_report.get("bytes_freed"),  # type: ignore[union-attr]
                retention_report.get("usage_rows"),  # type: ignore[union-attr]
            )
        except Exception:
            logger.exception("ops cron: run failed; retrying at the next scheduled time")
    return 0  # pragma: no cover - the loop never exits


if __name__ == "__main__":  # pragma: no cover - service entrypoint
    sys.exit(main())
