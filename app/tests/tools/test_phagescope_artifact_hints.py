"""PhageScope: distinguish API/JSON success from save_all local bundle."""

from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result
from tool_box.tools_impl.phagescope import (
    ARTIFACT_SCOPE_API_ONLY,
    LOCAL_BUNDLE_HINT_EN,
    _attach_local_bundle_artifact_fields,
    _attach_local_file_artifact_fields,
    _with_api_only_artifact_hint,
)


def test_with_api_only_artifact_hint_adds_fields() -> None:
    base = {"success": True, "action": "result", "data": {"code": 0}}
    out = _with_api_only_artifact_hint(dict(base), "38619")
    assert out["artifact_scope"] == ARTIFACT_SCOPE_API_ONLY
    assert out["local_bundle_hint"] == LOCAL_BUNDLE_HINT_EN
    assert out["taskid"] == "38619"


def test_with_api_only_skips_save_all() -> None:
    base = {"success": True, "action": "save_all", "output_directory": "/tmp/x"}
    out = _with_api_only_artifact_hint(dict(base), "1")
    assert "artifact_scope" not in out or out.get("artifact_scope") != ARTIFACT_SCOPE_API_ONLY


def test_sanitize_and_summarize_surface_save_all_reminder() -> None:
    raw = {
        "success": True,
        "status_code": 200,
        "action": "result",
        "result_kind": "quality",
        "taskid": "38619",
        "artifact_scope": ARTIFACT_SCOPE_API_ONLY,
        "local_bundle_hint": LOCAL_BUNDLE_HINT_EN,
        "data": {"code": 0, "message": "ok"},
    }
    sanitized = sanitize_tool_result("phagescope", raw)
    assert sanitized.get("local_bundle_hint")
    line = summarize_tool_result("phagescope", sanitized)
    assert "save_all" in line.lower()


def test_attach_local_file_artifact_fields_adds_output_paths(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    local_file = runtime_root / "session_demo" / "work" / "phagescope" / "downloads" / "quality.tsv"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")

    out = _attach_local_file_artifact_fields(
        {"success": True, "action": "download"},
        local_path=local_file,
        session_id="demo",
    )

    assert out["output_file"] == str(local_file.resolve())
    assert out["saved_path"] == str(local_file.resolve())
    assert out["artifact_paths"] == [str(local_file.resolve())]
    assert out["output_file_rel"] == "work/phagescope/downloads/quality.tsv"
    assert out["session_artifact_paths"] == ["work/phagescope/downloads/quality.tsv"]
    assert out["output_location"]["base_dir"] == str(local_file.parent.resolve())
    assert out["output_location"]["files"] == ["work/phagescope/downloads/quality.tsv"]


def test_attach_local_bundle_artifact_fields_expands_saved_files(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    output_dir = runtime_root / "session_demo" / "work" / "phagescope" / "task_1"
    quality_file = output_dir / "metadata" / "quality.json"
    summary_file = output_dir / "summary.json"
    quality_file.parent.mkdir(parents=True, exist_ok=True)
    quality_file.write_text('{"ok": true}', encoding="utf-8")
    summary_file.write_text('{"done": true}', encoding="utf-8")

    out = _attach_local_bundle_artifact_fields(
        {"success": True, "action": "save_all"},
        output_dir=output_dir,
        saved_files={"quality": "metadata/quality.json"},
        summary_file=summary_file,
        session_id="demo",
    )

    assert out["artifact_paths"] == [
        str(summary_file.resolve()),
        str(quality_file.resolve()),
    ]
    assert out["session_artifact_paths"] == [
        "work/phagescope/task_1/summary.json",
        "work/phagescope/task_1/metadata/quality.json",
    ]
    assert out["output_location"]["base_dir"] == str(output_dir.resolve())
    assert out["output_location"]["files"] == [
        "work/phagescope/task_1/summary.json",
        "work/phagescope/task_1/metadata/quality.json",
    ]


def test_attach_local_bundle_artifact_fields_marks_task_scoped_output(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    output_dir = runtime_root / "session_demo" / "raw_files" / "task_9"
    report_file = output_dir / "report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text('{"ok": true}', encoding="utf-8")

    out = _attach_local_bundle_artifact_fields(
        {"success": True, "action": "save_all"},
        output_dir=output_dir,
        saved_files={"report": "report.json"},
        session_id="demo",
        task_id=9,
        ancestor_chain=[3],
    )

    assert out["output_location"]["type"] == "task"
    assert out["output_location"]["task_id"] == 9
    assert out["output_location"]["ancestor_chain"] == [3]
