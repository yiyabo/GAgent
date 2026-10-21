"""A bio_tools call can only read the calling session's files.

The host runner reaches every session's directory, so without this check ``seqkit head`` on another
session's upload printed it back to the user (the eval scenario S28).
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from tool_box.bio_tools import session_scope
from tool_box.context import ToolContext

bio_tools_module = importlib.import_module("tool_box.bio_tools.bio_tools_handler")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    project = tmp_path / "project"
    runtime = project / "runtime"
    runtime.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime))
    monkeypatch.delenv("DB_ROOT", raising=False)

    from app.services.session_paths import get_runtime_session_dir

    own = get_runtime_session_dir("own", create=True)
    other = get_runtime_session_dir("other", create=True)
    (own / "uploads").mkdir(parents=True, exist_ok=True)
    (other / "uploads").mkdir(parents=True, exist_ok=True)
    mine = own / "uploads" / "mine.fasta"
    theirs = other / "uploads" / "clinic_reads.fasta"
    mine.write_text(">mine\nACGT\n", encoding="utf-8")
    theirs.write_text(">clinic_isolate_K3\nACGT\n", encoding="utf-8")

    runs = runtime / "bio_tools" / "_native" / "runs" / "abc123def456" / "work"
    runs.mkdir(parents=True)
    (runs / "ref.fa.fai").write_text("x", encoding="utf-8")

    db_root = project / "data" / "databases"
    (db_root / "sessions").mkdir(parents=True)
    (db_root / "sessions" / "s.sqlite").write_bytes(b"SQLite format 3\x00")
    (db_root / "bio_tools" / "bakta_db").mkdir(parents=True)
    (db_root / "bio_tools" / "bakta_db" / "version.json").write_text("{}", encoding="utf-8")

    return {
        "ctx": ToolContext(session_id="own", work_dir=str(own / "raw_files")),
        "mine": mine,
        "theirs": theirs,
        "runs": runs,
        "db_root": db_root,
        "bio_runtime": runtime / "bio_tools",
        "tmp": tmp_path,
    }


def _error(world, input_file=None, params=None, **kwargs):
    kwargs.setdefault("shared_roots", (str(world["bio_runtime"]),))
    return session_scope.scope_error(input_file, params, world["ctx"], **kwargs)


def test_another_sessions_upload_as_input_is_refused(world):
    assert "cross-session" in _error(world, str(world["theirs"]))


def test_own_upload_and_the_runs_directory_are_allowed(world):
    assert _error(world, str(world["mine"])) is None
    assert _error(world, str(world["runs"] / "ref.fa.fai")) is None


def test_a_path_in_any_parameter_is_checked_whatever_it_is_called(world):
    assert _error(world, str(world["mine"]), {"reference": str(world["theirs"])})
    assert _error(world, str(world["mine"]), {"anything_else": str(world["theirs"])})
    assert _error(world, str(world["mine"]), {"bam_files": f"{world['mine']},{world['theirs']}"})
    assert _error(world, None, {"r1": [str(world["mine"]), str(world["theirs"])]})


def test_values_that_are_not_paths_are_ignored(world):
    assert _error(world, str(world["mine"]), {"pattern": "ATG", "threads": 4, "regex": "^>ctg", "flag": True}) is None


def test_the_database_directory_is_refused_but_reference_databases_are_not(world):
    assert "database directory" in _error(world, str(world["db_root"] / "sessions" / "s.sqlite"))
    assert _error(world, str(world["mine"]), {"db": str(world["db_root"] / "bio_tools" / "bakta_db")}) is None


def test_a_symlink_from_the_session_to_another_session_is_refused(world):
    link = world["mine"].parent / "link.fasta"
    link.symlink_to(world["theirs"])
    assert _error(world, str(link))


def test_dot_dot_does_not_get_around_it(world):
    sneaky = world["mine"].parent / ".." / ".." / "session_other" / "uploads" / "clinic_reads.fasta"
    assert _error(world, str(sneaky))


def test_a_path_outside_the_runtime_directories_is_not_restricted(world):
    reference = world["tmp"] / "refs" / "genome.fa"
    reference.parent.mkdir()
    reference.write_text(">r\nA\n", encoding="utf-8")
    assert _error(world, str(mineless := reference)) is None
    assert mineless.exists()


def test_without_a_session_nothing_is_restricted(world):
    assert session_scope.scope_error(str(world["theirs"]), None, None) is None
    assert session_scope.scope_error(str(world["theirs"]), None, ToolContext()) is None


def test_a_bare_session_id_is_enough(world):
    assert session_scope.scope_error(str(world["theirs"]), None, None, session_id="own")
    assert session_scope.scope_error(str(world["mine"]), None, None, session_id="own") is None


def _call_handler(world, monkeypatch, **kwargs):
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    reached = []

    async def fake_once(**inner):
        reached.append(inner)
        return {"success": True, "tool": inner["tool_name"], "operation": inner["operation"]}

    def fake_submit(**inner):
        reached.append(inner)
        return {"success": True, "job_id": "bio_x"}

    monkeypatch.setattr(bio_tools_module, "_execute_bio_tool_once", fake_once)
    monkeypatch.setattr(bio_tools_module, "_submit_background_bio_tools_job", fake_submit)
    result = asyncio.run(
        bio_tools_module.bio_tools_handler(tool_name="seqkit", operation="head", tool_context=world["ctx"], **kwargs)
    )
    return result, reached


def test_the_handler_refuses_before_anything_runs(world, monkeypatch):
    result, reached = _call_handler(world, monkeypatch, input_file=str(world["theirs"]), params={"count": 1})
    assert result["success"] is False
    assert result["error_code"] == "path_not_allowed"
    assert reached == []


def test_the_handler_refuses_a_background_run_too(world, monkeypatch):
    result, reached = _call_handler(
        world, monkeypatch, input_file=str(world["theirs"]), params={"count": 1}, background=True
    )
    assert result["error_code"] == "path_not_allowed"
    assert reached == []


def test_the_handler_runs_the_sessions_own_file(world, monkeypatch):
    result, reached = _call_handler(world, monkeypatch, input_file=str(world["mine"]), params={"count": 1})
    assert result["success"] is True
    assert len(reached) == 1
