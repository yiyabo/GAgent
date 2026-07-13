from __future__ import annotations

from app.services.deliverables import platform_archiver


def test_archive_session_to_platform_is_safe_noop() -> None:
    assert platform_archiver.archive_session_to_platform("session-a") is False


def test_archive_session_to_platform_handles_empty_session_id() -> None:
    assert platform_archiver.archive_session_to_platform("") is False
