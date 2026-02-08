import asyncio
from pathlib import Path

from fastapi import HTTPException, status

from app.routers import artifact_routes


def test_resolve_session_dir_prefers_expected_root_by_purpose(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    runtime_session = runtime_root / "session_abc123"
    info_session = info_root / "session-session_abc123"

    (runtime_session / "deliverables").mkdir(parents=True, exist_ok=True)
    (runtime_session / "deliverables" / "manifest_latest.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (info_session / "tool_outputs").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)

    raw_dir = artifact_routes._resolve_session_dir("session_abc123", purpose="raw")
    deliverable_dir = artifact_routes._resolve_session_dir(
        "session_abc123", purpose="deliverables"
    )

    assert raw_dir == info_session.resolve()
    assert deliverable_dir == runtime_session.resolve()


def test_resolve_session_dir_handles_repeated_prefixes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    legacy_runtime_session = runtime_root / "session_session_xyz"
    legacy_runtime_session.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)

    resolved = artifact_routes._resolve_session_dir(
        "session-session_xyz", purpose="generic"
    )
    assert resolved == legacy_runtime_session.resolve()


def test_list_deliverables_returns_empty_when_session_missing(monkeypatch) -> None:
    def _raise_not_found(*args, **kwargs):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="missing")

    monkeypatch.setattr(artifact_routes, "_resolve_session_dir", _raise_not_found)

    response = asyncio.run(artifact_routes.list_session_deliverables("session_missing"))
    assert response.count == 0
    assert response.items == []
    assert response.modules == {}


def test_manifest_returns_empty_when_session_missing(monkeypatch) -> None:
    def _raise_not_found(*args, **kwargs):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="missing")

    monkeypatch.setattr(artifact_routes, "_resolve_session_dir", _raise_not_found)

    response = asyncio.run(
        artifact_routes.get_session_deliverables_manifest("session_missing")
    )
    assert response.manifest == {}
    assert response.manifest_path is None
