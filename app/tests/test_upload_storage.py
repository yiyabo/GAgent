from pathlib import Path

from app.services import upload_storage


def test_ensure_session_dir_uses_runtime_session_dir(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime" / "session_abc"

    def _fake_get_runtime_session_dir(session_id: str, *, create: bool = False) -> Path:
        assert session_id == "abc"
        if create:
            runtime_dir.mkdir(parents=True, exist_ok=True)
        return runtime_dir

    monkeypatch.setattr(upload_storage, "get_runtime_session_dir", _fake_get_runtime_session_dir)

    session_dir = upload_storage.ensure_session_dir("abc")
    assert session_dir == runtime_dir
    assert session_dir.exists()


def test_delete_session_storage_removes_all_candidates(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime" / "session_abc"
    legacy_dir = tmp_path / "data" / "information_sessions" / "session-abc"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir.mkdir(parents=True, exist_ok=True)

    def _fake_candidates(session_id: str, *, include_legacy: bool = True):
        assert session_id == "abc"
        assert include_legacy is True
        return [runtime_dir, legacy_dir]

    monkeypatch.setattr(upload_storage, "get_session_storage_candidates", _fake_candidates)

    assert upload_storage.delete_session_storage("abc") is True
    assert not runtime_dir.exists()
    assert not legacy_dir.exists()

