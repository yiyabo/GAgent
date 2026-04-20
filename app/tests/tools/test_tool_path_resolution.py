from __future__ import annotations

import asyncio
from pathlib import Path

from tool_box.context import ToolContext
from tool_box.tools_impl import file_operations
from tool_box.tools_impl.document_reader import document_reader_handler
from tool_box.tools_impl.file_operations import file_operations_handler


def test_file_operations_list_results_uses_repo_root(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    results_dir = repo_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "foo.md").write_text("hello", encoding="utf-8")

    monkeypatch.chdir(repo_root)

    result = asyncio.run(file_operations_handler("list", "results"))

    assert result["success"] is True
    assert result["path"] == str(results_dir.resolve())
    names = {entry["name"] for entry in result["items"]}
    assert "foo.md" in names


def test_file_operations_list_dot_uses_tool_work_dir(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    work_dir = repo_root / "runtime" / "session_demo"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "evidence.md").write_text("evidence", encoding="utf-8")

    monkeypatch.chdir(repo_root)

    result = asyncio.run(
        file_operations_handler(
            "list",
            ".",
            tool_context=ToolContext(work_dir=str(work_dir)),
        )
    )

    assert result["success"] is True
    assert result["path"] == str(work_dir.resolve())
    names = {entry["name"] for entry in result["items"]}
    assert "evidence.md" in names


def test_file_operations_write_results_uses_task_work_dir(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    task_work_dir = repo_root / "runtime" / "session_demo" / "raw_files" / "task_12"
    task_work_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(repo_root.resolve())])

    result = asyncio.run(
        file_operations_handler(
            "write",
            "results/summary.md",
            content="scoped output",
            tool_context=ToolContext(
                session_id="demo",
                task_id=12,
                work_dir=str(task_work_dir),
            ),
        )
    )

    expected = (task_work_dir / "summary.md").resolve()
    assert result["success"] is True
    assert result["path"] == str(expected)
    assert expected.read_text(encoding="utf-8") == "scoped output"


def test_file_operations_write_bare_filename_uses_task_work_dir(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    task_work_dir = repo_root / "runtime" / "session_demo" / "raw_files" / "task_12"
    task_work_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(repo_root.resolve())])

    result = asyncio.run(
        file_operations_handler(
            "write",
            "notes.md",
            content="bare output",
            tool_context=ToolContext(
                session_id="demo",
                task_id=12,
                work_dir=str(task_work_dir),
            ),
        )
    )

    expected = (task_work_dir / "notes.md").resolve()
    assert result["success"] is True
    assert result["path"] == str(expected)
    assert expected.read_text(encoding="utf-8") == "bare output"


def test_file_operations_write_absolute_session_workspace_path_redirects_to_task_work_dir(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    runtime_root = repo_root / "runtime"
    session_dir = runtime_root / "session_demo"
    workspace_dir = session_dir / "workspace"
    task_work_dir = session_dir / "raw_files" / "task_12"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    task_work_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(repo_root.resolve())])

    workspace_target = (workspace_dir / "task12_report.md").resolve()
    result = asyncio.run(
        file_operations_handler(
            "write",
            str(workspace_target),
            content="redirected output",
            tool_context=ToolContext(
                session_id="demo",
                task_id=12,
                work_dir=str(task_work_dir),
            ),
        )
    )

    expected = (task_work_dir / "task12_report.md").resolve()
    assert result["success"] is True
    assert result["path"] == str(expected)
    assert expected.read_text(encoding="utf-8") == "redirected output"
    assert not workspace_target.exists()


def test_document_reader_results_relative_path_uses_repo_root(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target = repo_root / "results" / "foo.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("artifact contract", encoding="utf-8")

    monkeypatch.chdir(repo_root)

    result = asyncio.run(document_reader_handler("read_any", "results/foo.md"))

    assert result["success"] is True
    assert result["file_path"] == str(target.resolve())
    assert "artifact contract" in result["text"]


def test_document_reader_results_relative_path_keeps_repo_root_in_task_context(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target = repo_root / "results" / "foo.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("repo scoped artifact", encoding="utf-8")

    task_work_dir = repo_root / "runtime" / "session_demo" / "raw_files" / "task_12"
    task_work_dir.mkdir(parents=True, exist_ok=True)
    (task_work_dir / "foo.md").write_text("task scoped artifact", encoding="utf-8")

    monkeypatch.chdir(repo_root)

    result = asyncio.run(
        document_reader_handler(
            "read_any",
            "results/foo.md",
            tool_context=ToolContext(
                session_id="demo",
                task_id=12,
                work_dir=str(task_work_dir),
            ),
        )
    )

    assert result["success"] is True
    assert result["file_path"] == str(target.resolve())
    assert "repo scoped artifact" in result["text"]
    assert "task scoped artifact" not in result["text"]
