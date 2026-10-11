"""The ops cron's schedule math and run order.

``app.ops.cron`` is the compose service that decides WHEN production data is
reaped and backed up. The sleep math must survive DST-less UTC+8 arithmetic
(no tzdata on slim images), wrap past midnight, and the run order is
load-bearing: the backup must be taken BEFORE retention deletes anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ops.cron import run_once, seconds_until_next_run

_UTC8 = timezone(timedelta(hours=8))


def test_seconds_until_next_run_wraps_midnight() -> None:
    before = datetime(2026, 10, 11, 1, 30, tzinfo=_UTC8)
    assert seconds_until_next_run(before) == pytest.approx(1.5 * 3600)

    after = datetime(2026, 10, 11, 4, 0, tzinfo=_UTC8)
    assert seconds_until_next_run(after) == pytest.approx(23 * 3600)

    # Exactly at the run hour: schedule tomorrow, do not re-fire immediately.
    exactly = datetime(2026, 10, 11, 3, 0, tzinfo=_UTC8)
    assert seconds_until_next_run(exactly) == pytest.approx(24 * 3600)

    # A UTC-typed "now" is converted first (03:30 UTC+8 -> 23.5 h to next 03:00).
    utc_now = datetime(2026, 10, 10, 19, 30, tzinfo=timezone.utc)
    assert seconds_until_next_run(utc_now) == pytest.approx(23.5 * 3600)


def test_run_once_backs_up_before_retention_and_vacuums_on_sunday(
    isolated_app_env, monkeypatch
) -> None:
    import app.ops.backup as backup_module
    import app.ops.retention as retention_module
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("BACKUP_DIR", str(isolated_app_env["db_root"] / "bk"))
    get_settings.cache_clear()

    calls: list = []

    def fake_backup(**kwargs):
        calls.append(("backup", kwargs))
        return {"ts": "t", "files": 1, "bytes": 1, "pruned": 0}

    def fake_retention(**kwargs):
        calls.append(("retention", kwargs))
        return {"sessions_deleted": 0, "bytes_freed": 0, "usage_rows": 0}

    monkeypatch.setattr(backup_module, "run_backup", fake_backup)
    monkeypatch.setattr(retention_module, "run_retention", fake_retention)

    anchor = datetime(2026, 10, 11, 3, 0, tzinfo=_UTC8)
    sunday = anchor + timedelta(days=(6 - anchor.weekday()) % 7)
    assert sunday.weekday() == 6

    run_once(apply=True, vacuum=None, now=sunday)
    assert [name for name, _ in calls] == ["backup", "retention"]
    assert calls[1][1]["vacuum"] is True
    assert calls[1][1]["apply"] is True

    calls.clear()
    run_once(apply=True, vacuum=None, now=sunday + timedelta(days=1))
    assert calls[1][1]["vacuum"] is False

    # Explicit override wins over the weekday default.
    calls.clear()
    run_once(apply=True, vacuum=True, now=sunday + timedelta(days=2))
    assert calls[1][1]["vacuum"] is True
