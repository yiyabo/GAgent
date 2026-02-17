from pathlib import Path

from app.services import session_paths


def test_normalize_session_base_strips_repeated_prefixes() -> None:
    assert session_paths.normalize_session_base("session_session-abc123") == "abc123"
    assert session_paths.normalize_session_base("session-abc123") == "abc123"
    assert session_paths.normalize_session_base("abc123") == "abc123"
    assert session_paths.normalize_session_base("session_../../etc/passwd") == "etc-passwd"


def test_runtime_and_legacy_candidates(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    legacy_root = tmp_path / "data" / "information_sessions"
    monkeypatch.setattr(session_paths, "_RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(session_paths, "_LEGACY_INFO_SESSIONS_ROOT", legacy_root)

    runtime_dir = session_paths.get_runtime_session_dir("session_abc123", create=True)
    assert runtime_dir == (runtime_root / "session_abc123").resolve()
    assert runtime_dir.exists()

    candidates = session_paths.get_session_storage_candidates("session_abc123", include_legacy=True)
    candidate_set = {str(path) for path in candidates}
    assert str(runtime_dir) in candidate_set
    assert str((legacy_root / "session-session_abc123").resolve()) in candidate_set
    assert str((legacy_root / "session-abc123").resolve()) in candidate_set
    assert str((legacy_root / "session_abc123").resolve()) in candidate_set

    runtime_only = session_paths.get_session_storage_candidates("session_abc123", include_legacy=False)
    assert runtime_only == [runtime_dir]


def test_storage_candidates_are_sanitized(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    legacy_root = tmp_path / "data" / "information_sessions"
    monkeypatch.setattr(session_paths, "_RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(session_paths, "_LEGACY_INFO_SESSIONS_ROOT", legacy_root)

    candidates = session_paths.get_session_storage_candidates("session_../../etc/passwd", include_legacy=True)
    assert candidates
    for path in candidates:
        resolved = path.resolve()
        assert str(resolved).startswith(str(runtime_root.resolve())) or str(resolved).startswith(
            str(legacy_root.resolve())
        )
