"""The directory with every session's SQLite files is closed to the file tool.

``data/`` is a normal project directory and stays open. Only ``DB_ROOT`` (default ``data/databases``) is not:
the agent could otherwise read or change the stored sessions, memories and plans of other users.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl import file_operations


def _call(operation: str, path, ctx=None, **kwargs):
    return asyncio.run(
        file_operations.file_operations_handler(operation, str(path), tool_context=ctx, **kwargs)
    )


def _denied(result) -> bool:
    return result["success"] is False and "database directory" in str(result.get("error", ""))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    runtime = root / "runtime"
    db_root = root / "data" / "databases"
    (db_root / "sessions").mkdir(parents=True)
    (db_root / "main").mkdir(parents=True)
    runtime.mkdir(parents=True)
    (db_root / "sessions" / "victim.sqlite").write_bytes(b"SQLite format 3\x00VICTIM")
    (db_root / "main" / "plan_registry.db").write_bytes(b"SQLite format 3\x00PLANS")
    (root / "data" / "notes.txt").write_text("project data", encoding="utf-8")

    monkeypatch.chdir(root)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime))
    monkeypatch.delenv("DB_ROOT", raising=False)
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(root / "data"), str(runtime)])
    return {"root": root, "db_root": db_root, "runtime": runtime}


def _session(project, name="own"):
    from app.services.session_paths import get_runtime_session_dir

    session_dir = get_runtime_session_dir(name, create=True)
    return session_dir, ToolContext(session_id=name, work_dir=str(session_dir / "raw_files"))


@pytest.mark.parametrize("with_session", [False, True])
def test_reading_a_session_database_is_refused(project, with_session):
    ctx = _session(project)[1] if with_session else None
    for target in (
        project["db_root"] / "sessions" / "victim.sqlite",
        project["db_root"] / "main" / "plan_registry.db",
    ):
        assert _denied(_call("read", target, ctx))


@pytest.mark.parametrize("operation", ["list", "profile", "census"])
def test_listing_the_database_directory_is_refused(project, operation):
    assert _denied(_call(operation, project["db_root"]))
    assert _denied(_call(operation, project["db_root"] / "sessions", _session(project)[1]))


def test_writing_deleting_copying_and_moving_there_are_refused(project):
    victim = project["db_root"] / "sessions" / "victim.sqlite"
    ctx = _session(project)[1]

    assert _denied(_call("write", project["db_root"] / "sessions" / "new.sqlite", ctx, content="x"))
    assert not (project["db_root"] / "sessions" / "new.sqlite").exists()

    assert _denied(_call("delete", victim, ctx))
    assert victim.exists()

    leak = project["root"] / "data" / "leak.sqlite"
    assert _denied(_call("copy", victim, ctx, destination=str(leak)))
    assert not leak.exists()

    assert _denied(_call("move", victim, ctx, destination=str(leak)))
    assert victim.exists() and not leak.exists()

    (project["root"] / "data" / "payload.bin").write_bytes(b"x")
    assert _denied(
        _call("copy", project["root"] / "data" / "payload.bin", ctx,
              destination=str(project["db_root"] / "sessions" / "planted.sqlite"))
    )
    assert not (project["db_root"] / "sessions" / "planted.sqlite").exists()


def test_the_rest_of_project_data_stays_open(project):
    notes = project["root"] / "data" / "notes.txt"
    assert _call("read", notes)["content"] == "project data"
    assert _call("read", notes, _session(project)[1])["content"] == "project data"

    written = project["root"] / "data" / "extra" / "table.csv"
    assert _call("write", written, content="a,b\n1,2\n")["success"] is True
    assert written.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_a_name_that_only_starts_like_the_database_directory_is_not_closed(project):
    sibling = project["root"] / "data" / "databases_old"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("fine", encoding="utf-8")
    assert _call("read", sibling / "keep.txt")["content"] == "fine"


def test_reference_databases_kept_under_the_database_directory_stay_readable(project):
    reference = project["db_root"] / "bio_tools" / "bakta_db"
    reference.mkdir(parents=True)
    (reference / "version.json").write_text('{"v": 1}', encoding="utf-8")

    assert _call("read", reference / "version.json")["content"] == '{"v": 1}'
    assert _denied(_call("read", project["db_root"] / "sessions" / "victim.sqlite"))


def test_a_symlink_from_the_session_into_the_database_directory_is_refused(project):
    session_dir, ctx = _session(project)
    link = session_dir / "results" / "db"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(project["db_root"], target_is_directory=True)

    assert _denied(_call("read", link / "sessions" / "victim.sqlite", ctx))
    assert _denied(_call("list", link, ctx))


def test_dot_dot_does_not_get_around_it(project):
    sneaky = project["root"] / "data" / "notes_dir" / ".." / "databases" / "main" / "plan_registry.db"
    assert _denied(_call("read", sneaky))


def test_the_configured_db_root_is_the_one_that_is_closed(project, monkeypatch, tmp_path):
    elsewhere = tmp_path / "var_lib" / "gagent_db"
    elsewhere.mkdir(parents=True)
    (elsewhere / "main.db").write_bytes(b"SQLite format 3\x00ELSEWHERE")
    monkeypatch.setenv("DB_ROOT", str(elsewhere))
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(tmp_path), str(project["root"])])

    assert _denied(_call("read", elsewhere / "main.db"))
    assert _denied(_call("read", elsewhere / "main.db", _session(project)[1]))


def test_a_relative_db_root_is_read_from_the_working_directory(project, monkeypatch):
    other = project["root"] / "state" / "db"
    other.mkdir(parents=True)
    (other / "main.db").write_bytes(b"x")
    monkeypatch.setenv("DB_ROOT", "state/db")
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project["root"])])

    assert _denied(_call("read", other / "main.db"))


def test_a_symlinked_data_directory_keeps_the_database_directory_closed(tmp_path, monkeypatch):
    root = tmp_path / "project"
    external = tmp_path / "volume" / "data"
    (external / "databases" / "main").mkdir(parents=True)
    (external / "databases" / "main" / "plan_registry.db").write_bytes(b"x")
    (external / "notes.txt").write_text("open", encoding="utf-8")
    root.mkdir()
    (root / "data").symlink_to(external, target_is_directory=True)

    monkeypatch.chdir(root)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(root / "runtime"))
    monkeypatch.delenv("DB_ROOT", raising=False)
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(root), str(root / "data")])

    assert _call("read", root / "data" / "notes.txt")["content"] == "open"
    assert _denied(_call("read", root / "data" / "databases" / "main" / "plan_registry.db"))
    assert _denied(_call("read", external / "databases" / "main" / "plan_registry.db"))
