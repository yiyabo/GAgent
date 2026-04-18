from __future__ import annotations

import asyncio
from pathlib import Path

from tool_box.context import ToolContext
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
