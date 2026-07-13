from __future__ import annotations

from app.services.deliverables import platform_archiver


def test_archive_dirname_is_stable_and_prevents_title_collisions() -> None:
    first = platform_archiver._archive_dirname("session-a", "Same title")
    second = platform_archiver._archive_dirname("session-b", "Same title")

    assert first.startswith("Same title-")
    assert first != second
    assert platform_archiver._archive_dirname("session-a", "Same title") == first


def test_archive_dirname_sanitizes_title() -> None:
    result = platform_archiver._archive_dirname("session-a", "../Unsafe/Title")

    assert "/" not in result
    assert ".." not in result


def test_platform_archiver_never_falls_back_to_agent_host_filesystem(monkeypatch) -> None:
    called = False

    def _legacy(*args, **kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(platform_archiver, "_legacy_archive_session_to_platform", _legacy)

    assert platform_archiver.archive_session_to_platform("session-a") is False
    assert called is False
