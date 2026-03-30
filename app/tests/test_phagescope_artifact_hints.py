"""PhageScope: distinguish API/JSON success from save_all local bundle."""

from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result
from tool_box.tools_impl.phagescope import (
    ARTIFACT_SCOPE_API_ONLY,
    LOCAL_BUNDLE_HINT_EN,
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
