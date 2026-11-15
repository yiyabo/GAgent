"""Chat-lane relative paths resolve inside the session, like execute_code's cwd.

Regression for a live run where ``file_operations list "."`` resolved to the
repo root (``/app``) and was rejected as "Path outside allowed directories",
and ``results/<file>`` pointed at a repo-level folder that does not exist,
while execute_code had written those files under the session directory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tool_box.context import ToolContext
from tool_box.path_resolution import resolve_tool_path
from tool_box.tools_impl import file_operations
from tool_box.tools_impl.document_reader import document_reader_handler
from tool_box.tools_impl.file_operations import file_operations_handler

SESSION = "session_cwd_case"


@pytest.fixture()
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "app"
    runtime = root / "runtime"
    session_dir = runtime / f"session_{SESSION.removeprefix('session_')}"
    (session_dir / "results").mkdir(parents=True)
    (session_dir / "results" / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (session_dir / "results" / "report.md").write_text(
        "session report", encoding="utf-8"
    )
    (session_dir / "notes.txt").write_text("session note", encoding="utf-8")
    other_session = runtime / "session_other"
    other_session.mkdir(parents=True)
    (other_session / "secret.txt").write_text("other user's data", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "shared.txt").write_text("shared reference", encoding="utf-8")

    monkeypatch.chdir(root)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime))
    # The PathRouter singleton caches its runtime root; rebuild it for this project.
    from app.services import path_router

    monkeypatch.setattr(path_router, "_default_router", None)
    monkeypatch.setattr(file_operations, "_get_project_root", lambda: root.resolve())
    monkeypatch.setattr(
        file_operations, "ALLOWED_BASE_PATHS", [str(root / "data"), str(runtime)]
    )
    return {
        "root": root.resolve(),
        "session": session_dir.resolve(),
        "other": other_session.resolve(),
    }


def _chat() -> ToolContext:
    return ToolContext(session_id=SESSION)


def _run(*args, **kwargs):
    return asyncio.run(file_operations_handler(*args, **kwargs))


def test_list_dot_lists_the_session_directory(project) -> None:
    result = _run("list", ".", tool_context=_chat())
    assert result["success"] is True, result
    assert result["path"] == str(project["session"])
    names = {item["name"] for item in result["items"]}
    assert {"results", "notes.txt"} <= names


def test_bare_relative_file_reads_from_the_session(project) -> None:
    result = _run("read", "notes.txt", tool_context=_chat())
    assert result["success"] is True, result
    assert result["content"] == "session note"


def test_results_prefix_reads_the_session_results_folder(project) -> None:
    listed = _run("list", "results", tool_context=_chat())
    assert listed["success"] is True, listed
    assert listed["path"] == str(project["session"] / "results")
    read = _run("read", "results/summary.csv", tool_context=_chat())
    assert read["success"] is True, read
    assert read["content"].startswith("a,b")


def test_shared_data_prefix_still_resolves_to_repo_data(project) -> None:
    result = _run("read", "data/shared.txt", tool_context=_chat())
    assert result["success"] is True, result
    assert result["path"] == str(project["root"] / "data" / "shared.txt")


def test_relative_escape_into_another_session_is_still_denied(project) -> None:
    result = _run("read", "../session_other/secret.txt", tool_context=_chat())
    assert result["success"] is False
    assert "other user's data" not in str(result)


def test_writes_keep_landing_inside_the_session(project) -> None:
    result = _run("write", "draft.md", content="draft", tool_context=_chat())
    assert result["success"] is True, result
    written = Path(result["path"]).resolve()
    assert project["session"] in written.parents
    assert written.read_text(encoding="utf-8") == "draft"


def test_document_reader_relative_results_path_uses_the_session(project) -> None:
    result = asyncio.run(
        document_reader_handler("read_any", "results/report.md", tool_context=_chat())
    )
    assert result["success"] is True, result
    assert result["file_path"] == str(project["session"] / "results" / "report.md")


def test_resolution_rules_outside_the_chat_lane_are_unchanged(
    project, tmp_path: Path
) -> None:
    root = project["root"]
    assert resolve_tool_path(".") == root
    assert resolve_tool_path("results/x.csv") == root / "results" / "x.csv"
    work_dir = tmp_path / "task_work"
    work_dir.mkdir()
    task = ToolContext(session_id=SESSION, task_id=3, work_dir=str(work_dir))
    assert resolve_tool_path(".", tool_context=task) == work_dir.resolve()
    assert (
        resolve_tool_path("results/x.csv", tool_context=task)
        == root / "results" / "x.csv"
    )
    assert (
        resolve_tool_path("/etc/hosts", tool_context=_chat())
        == Path("/etc/hosts").resolve()
    )
