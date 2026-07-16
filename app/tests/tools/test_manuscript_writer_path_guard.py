from __future__ import annotations

from pathlib import Path

import pytest

from tool_box.tools_impl import manuscript_writer as mw


def test_session_scoped_relative_path_goes_to_raw_files_tmp(tmp_path: Path) -> None:
    session_dir = tmp_path / "runtime" / "session_demo"
    session_dir.mkdir(parents=True, exist_ok=True)
    resolved = mw._resolve_session_scoped_project_path("开题报告.md", session_dir)
    assert resolved == (session_dir / "raw_files" / "tmp" / "开题报告.md").resolve()


def test_session_scoped_project_root_absolute_is_redirected(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    session_dir = project_root / "runtime" / "session_demo"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mw, "_PROJECT_ROOT", project_root.resolve())

    leak = project_root / "开题报告_leak.md"
    resolved = mw._resolve_session_scoped_project_path(str(leak), session_dir)
    assert resolved == (session_dir / "raw_files" / "tmp" / "开题报告_leak.md").resolve()
    assert not str(resolved).endswith(str(leak))


def test_no_session_project_root_write_is_rejected(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mw, "_PROJECT_ROOT", project_root.resolve())
    leak = project_root / "开题报告_leak.md"
    with pytest.raises(ValueError, match="not allowed"):
        mw._resolve_session_scoped_project_path(str(leak), None)


def test_runtime_path_still_allowed_without_session(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    runtime_file = project_root / "runtime" / "session_x" / "out.md"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mw, "_PROJECT_ROOT", project_root.resolve())
    resolved = mw._resolve_session_scoped_project_path(str(runtime_file), None)
    assert resolved == runtime_file.resolve()
