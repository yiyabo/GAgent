"""p74: ``file_operations`` edit, ``vision_reader`` qa_figure, figure-QA soft warning."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.deliverables import submit_payload
from tool_box.context import ToolContext
from tool_box.tools_impl import file_operations, vision_reader
from tool_box.tools_impl.file_operations import file_operations_handler
from tool_box.tools_impl.vision_reader import vision_reader_handler

SESSION = "session_p74_case"


@pytest.fixture()
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "app"
    runtime = root / "runtime"
    session_dir = runtime / f"session_{SESSION.removeprefix('session_')}"
    (session_dir / "results").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime))
    from app.services import path_router

    monkeypatch.setattr(path_router, "_default_router", None)
    monkeypatch.setattr(file_operations, "_get_project_root", lambda: root.resolve())
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(root / "data"), str(runtime)])
    return {"root": root.resolve(), "session": session_dir.resolve()}


def _ctx() -> ToolContext:
    return ToolContext(session_id=SESSION)


def _edit(path: str, old: Any, new: Any, **kwargs):
    return asyncio.run(file_operations_handler("edit", path, old_text=old, new_text=new, tool_context=_ctx(), **kwargs))


# --- file_operations edit -------------------------------------------------


def test_edit_replaces_unique_span_and_returns_diff(project) -> None:
    target = project["session"] / "results" / "analysis.py"
    target.write_bytes(b"a = 1\r\nb = 2\r\nc = 3")
    result = _edit("results/analysis.py", "b = 2", "b = 20")
    assert result["success"] is True, result
    assert result["replacements"] == 1
    assert "-b = 2" in result["diff"] and "+b = 20" in result["diff"]
    # CRLF and the missing final newline survive byte-for-byte.
    assert target.read_bytes() == b"a = 1\r\nb = 20\r\nc = 3"


def test_edit_refuses_ambiguous_match_with_line_numbers(project) -> None:
    target = project["session"] / "results" / "dup.txt"
    target.write_text("x\nx\ny\nx\n", encoding="utf-8")
    result = _edit("results/dup.txt", "x", "z")
    assert result["success"] is False
    assert result["occurrences"] == 3
    assert result["match_lines"] == [1, 2, 4]
    assert target.read_text(encoding="utf-8") == "x\nx\ny\nx\n"


def test_edit_replace_all(project) -> None:
    target = project["session"] / "results" / "dup.txt"
    target.write_text("x\nx\ny\n", encoding="utf-8")
    result = _edit("results/dup.txt", "x", "z", replace_all="true")
    assert result["success"] is True and result["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "z\nz\ny\n"


def test_edit_empty_new_text_deletes(project) -> None:
    target = project["session"] / "results" / "notes.md"
    target.write_text("keep DROP keep", encoding="utf-8")
    assert _edit("results/notes.md", " DROP", "")["success"] is True
    assert target.read_text(encoding="utf-8") == "keep keep"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("", "x", "old_text must be a non-empty string"),
        (None, "x", "old_text must be a non-empty string"),
        ("a", None, "new_text is required"),
    ],
)
def test_edit_argument_validation(project, old, new, message) -> None:
    (project["session"] / "results" / "f.txt").write_text("a", encoding="utf-8")
    result = _edit("results/f.txt", old, new)
    assert result["success"] is False and message in result["error"]


def test_edit_not_found_and_missing_file(project) -> None:
    (project["session"] / "results" / "f.txt").write_text("abc", encoding="utf-8")
    assert _edit("results/f.txt", "zzz", "y")["error"] == "old_text not found in file"
    assert _edit("results/missing.txt", "a", "b")["success"] is False


def test_edit_rejects_non_utf8(project) -> None:
    (project["session"] / "results" / "bin.dat").write_bytes(b"\xff\xfe\x00a")
    result = _edit("results/bin.dat", "a", "b")
    assert result["success"] is False and "UTF-8" in result["error"]


def test_edit_refuses_project_source(project) -> None:
    result = asyncio.run(
        file_operations_handler(
            "edit",
            str(project["root"] / "app" / "main.py"),
            old_text="x = 1",
            new_text="x = 2",
        )
    )
    assert result["success"] is False
    assert (project["root"] / "app" / "main.py").read_text(encoding="utf-8") == "x = 1\n"


def test_edit_is_in_tool_schema() -> None:
    schema = file_operations.file_operations_tool["parameters_schema"]
    assert "edit" in schema["properties"]["operation"]["enum"]
    for key in ("old_text", "new_text", "replace_all"):
        assert key in schema["properties"]


# --- vision_reader qa_figure ----------------------------------------------


def _qa(path: Path, ctx=None):
    return asyncio.run(vision_reader_handler("qa_figure", file_path=str(path), tool_context=ctx))


def test_qa_figure_empty_file_fails_without_model(monkeypatch, tmp_path) -> None:
    async def boom(*_a, **_k):
        raise AssertionError("model must not be called")

    monkeypatch.setattr(vision_reader, "_call_qwen_vision_api", boom)
    figure = tmp_path / "blank.png"
    figure.write_bytes(b"")
    result = _qa(figure)
    assert result["success"] is False and result["code"] == "empty_figure"
    assert result["qa"]["pass"] is False and result["qa"]["severity"] == "high"


def test_qa_figure_rejects_vector_formats(tmp_path) -> None:
    figure = tmp_path / "plot.pdf"
    figure.write_bytes(b"%PDF-1.4")
    assert _qa(figure)["code"] == "unsupported_figure_format"


def test_qa_figure_normalizes_fenced_verdict_and_records_check(monkeypatch, tmp_path) -> None:
    reply = (
        "Here:\n```json\n"
        + json.dumps(
            {
                "pass": True,
                "severity": "low",
                "issues": [{"type": "clipped", "detail": "x label cut", "severity": "high"}],
                "recommendations": ["increase bottom margin"],
                "summary": "label clipped",
            }
        )
        + "\n```"
    )

    async def fake(prompt, path):
        assert "strict visual QA" in prompt
        return reply

    monkeypatch.setattr(vision_reader, "_call_qwen_vision_api", fake)
    figure = tmp_path / "fig.png"
    figure.write_bytes(b"\x89PNG fake")
    ctx = ToolContext(session_id=SESSION)
    result = _qa(figure, ctx)
    assert result["success"] is True
    # A high-severity issue overrides the optimistic pass.
    assert result["pass"] is False and result["severity"] == "high"
    assert "qa_checked_figures" not in ctx.extra


def test_qa_figure_unparseable_reply(monkeypatch, tmp_path) -> None:
    async def fake(prompt, path):
        return "looks fine to me"

    monkeypatch.setattr(vision_reader, "_call_qwen_vision_api", fake)
    figure = tmp_path / "fig.png"
    figure.write_bytes(b"\x89PNG fake")
    ctx = ToolContext(session_id=SESSION)
    result = _qa(figure, ctx)
    assert result["code"] == "qa_parse_error"
    assert "qa_checked_figures" not in ctx.extra


# --- deliverable_submit soft figure-QA warning ----------------------------


def test_figure_qa_warning_only_for_unchecked_raster_image_tabular(tmp_path) -> None:
    checked = tmp_path / "checked.png"
    unchecked = tmp_path / "unchecked.png"
    ctx = SimpleNamespace(extra={"qa_checked_figures": [str(checked)]})
    qa = submit_payload._qa_checked_figure_set({}, ctx)
    assert qa is not None and qa == {str(checked.resolve())}
    item = {"module": "image_tabular"}
    assert submit_payload._needs_figure_qa_warning(item, unchecked, qa) is True
    assert submit_payload._needs_figure_qa_warning(item, checked, qa) is False
    assert submit_payload._needs_figure_qa_warning(item, tmp_path / "a.pdf", qa) is False
    assert submit_payload._needs_figure_qa_warning({"module": "code"}, unchecked, qa) is False


def test_figure_qa_untracked_lane_gets_no_warning() -> None:
    assert submit_payload._qa_checked_figure_set({}, None) is None
    assert submit_payload._qa_checked_figure_set({"qa_checked_figures": ["/x/a.png"]}, None) == {
        str(Path("/x/a.png").resolve())
    }


# --- edit: in-place write semantics ---------------------------------------


def test_edit_preserves_permissions_and_inode(project) -> None:
    target = project["session"] / "results" / "run.sh"
    target.write_text("echo old\n", encoding="utf-8")
    target.chmod(0o750)
    inode = target.stat().st_ino
    result = _edit("results/run.sh", "old", "new")
    assert result["success"] is True, result
    assert target.read_text(encoding="utf-8") == "echo new\n"
    assert target.stat().st_mode & 0o777 == 0o750
    assert target.stat().st_ino == inode


def test_concurrent_edits_on_one_file_both_apply(project) -> None:
    target = project["session"] / "results" / "table.txt"
    lines = [f"row{i} = 0" for i in range(12)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def run_all():
        return await asyncio.gather(
            *(
                file_operations_handler(
                    "edit",
                    "results/table.txt",
                    old_text=f"row{i} = 0",
                    new_text=f"row{i} = {i + 100}",
                    tool_context=_ctx(),
                )
                for i in range(12)
            )
        )

    results = asyncio.run(run_all())
    assert all(r["success"] for r in results), results
    assert target.read_text(encoding="utf-8") == "".join(f"row{i} = {i + 100}\n" for i in range(12))


# --- figure-QA ledger across the real executor boundary -------------------


def test_qa_ledger_survives_unified_executor_and_keys_submit_path(monkeypatch, tmp_path) -> None:
    from app.services.deep_think.dispatch import _run_qa_checked_figures
    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        UnifiedToolExecutor,
    )

    async def fake(prompt, path):
        return '{"pass": true, "severity": "low", "issues": [], "summary": "ok"}'

    monkeypatch.setattr(vision_reader, "_call_qwen_vision_api", fake)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    figure = real_dir / "fig.png"
    figure.write_bytes(b"\x89PNG fake")
    alias = tmp_path / "alias"
    alias.symlink_to(real_dir, target_is_directory=True)

    agent = SimpleNamespace()
    # Native dispatch puts the agent's run ledger on the ToolContext it hands the
    # plan-lane executor, which rebuilds its own ToolContext before the tool runs.
    caller_ctx = ToolContext(session_id=SESSION, extra={"qa_checked_figures": _run_qa_checked_figures(agent)})
    response = asyncio.run(
        UnifiedToolExecutor().execute(
            "vision_reader",
            {"operation": "qa_figure", "file_path": str(alias / "fig.png"), "tool_context": caller_ctx},
            context=ToolExecutionContext(session_id=SESSION),
        )
    )
    assert response["success"] is True, response
    assert agent._qa_checked_figures == [str(figure.resolve())]
    assert _run_qa_checked_figures(agent) is agent._qa_checked_figures

    # A later deliverable_submit in the same run sees the same ledger and keys
    # the submitted output by its resolved path, so no warning for this figure.
    submit_ctx = ToolContext(session_id=SESSION, extra={"qa_checked_figures": _run_qa_checked_figures(agent)})
    qa = submit_payload._qa_checked_figure_set({}, submit_ctx)
    assert qa is not None
    item = {"module": "image_tabular"}
    assert submit_payload._needs_figure_qa_warning(item, figure, qa) is False
    assert submit_payload._needs_figure_qa_warning(item, alias / "fig.png", qa) is False
    assert submit_payload._needs_figure_qa_warning(item, real_dir / "other.png", qa) is True



def test_chat_tool_context_shares_qa_checked_figures_across_calls(monkeypatch, tmp_path) -> None:
    from app.routers.chat.action_handlers import _build_chat_tool_context

    monkeypatch.setattr(
        "app.services.session_paths.get_runtime_session_dir",
        lambda session_id, create=True: tmp_path / session_id,
    )
    agent = SimpleNamespace(session_id=SESSION, extra_context={})
    vision_ctx = _build_chat_tool_context(agent, "vision_reader")
    submit_ctx = _build_chat_tool_context(agent, "deliverable_submit")
    assert vision_ctx is not None and submit_ctx is not None
    assert vision_ctx.extra["qa_checked_figures"] is submit_ctx.extra["qa_checked_figures"]

    unchecked = tmp_path / "unchecked.png"
    unchecked.write_bytes(b"\x89PNG fake")
    item = {"module": "image_tabular"}
    qa_before = submit_payload._qa_checked_figure_set({}, submit_ctx)
    assert qa_before is not None and qa_before == set()
    assert submit_payload._needs_figure_qa_warning(item, unchecked, qa_before) is True

    vision_ctx.extra["qa_checked_figures"].append(str(unchecked.resolve()))
    qa_after = submit_payload._qa_checked_figure_set({}, submit_ctx)
    assert qa_after is not None
    assert submit_payload._needs_figure_qa_warning(item, unchecked, qa_after) is False


def test_edit_rejects_symlink_target_without_following(project) -> None:
    outside = project["root"] / "outside_secret.txt"
    outside.write_text("secret = 1\n", encoding="utf-8")
    link = project["session"] / "results" / "link.txt"
    link.symlink_to(outside)

    result = _edit("results/link.txt", "secret = 1", "secret = 2")
    assert result["success"] is False
    assert outside.read_text(encoding="utf-8") == "secret = 1\n"
