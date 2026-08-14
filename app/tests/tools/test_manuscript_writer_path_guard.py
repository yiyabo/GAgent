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


def test_session_scoped_project_results_write_is_redirected(tmp_path: Path, monkeypatch) -> None:
    """Project-root results/ writes in a session context must land in the
    session workspace, otherwise they are invisible to the Artifacts UI."""
    project_root = tmp_path / "project"
    session_dir = project_root / "runtime" / "session_demo"
    session_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "results").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mw, "_PROJECT_ROOT", project_root.resolve())

    target = project_root / "results" / "Protocol_D2HGA_Type_I.md"
    resolved = mw._resolve_session_scoped_project_path(str(target), session_dir)
    assert resolved == (session_dir / "raw_files" / "tmp" / "Protocol_D2HGA_Type_I.md").resolve()
    assert not str(resolved).endswith(str(target))


def test_project_results_write_still_allowed_without_session(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    results_file = project_root / "results" / "out.md"
    results_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mw, "_PROJECT_ROOT", project_root.resolve())
    resolved = mw._resolve_session_scoped_project_path(str(results_file), None)
    assert resolved == results_file.resolve()


def test_local_assembly_with_empty_context_uses_no_sources(tmp_path: Path) -> None:
    """Precondition for the handler guard: with no usable context files the
    local assembler reports zero sources, and the handler must then fail with
    no_usable_context_sources instead of publishing an all-placeholder draft."""
    draft_text, _memo, used_sources, _counts, _sections = mw._assemble_local_draft_from_context(
        task="write something",
        context_paths=[],
        max_context_bytes=1024,
        section_list=[],
    )
    assert used_sources == []
    assert "Not available in provided context." in draft_text
