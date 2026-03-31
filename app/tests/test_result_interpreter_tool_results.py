import json

from app.routers.chat.tool_results import (
    append_recent_tool_result,
    sanitize_tool_result,
    summarize_tool_result,
)


def test_sanitize_result_interpreter_keeps_execution_summary_and_paths() -> None:
    raw = {
        "success": True,
        "operation": "analyze",
        "metadata": [
            {
                "filename": "gvd_phage_meta_data.tsv",
                "file_format": "tsv",
                "file_size_bytes": 5079532,
                "total_rows": 31402,
                "total_columns": 9,
                "column_names": [
                    "Phage_ID",
                    "Length",
                    "GC_content",
                    "Taxonomy",
                    "Completeness",
                ],
                "sample_values": {"Phage_ID": ["x", "y"]},
            }
        ],
        "code_description": "Task completed by Claude Code: 人类肠道噬菌体宿主筛选",
        "execution_status": "success",
        "execution_output": json.dumps(
            {
                "type": "result",
                "result": "任务已完成！以下是结果摘要：\n\n筛选后噬菌体数：18490",
            },
            ensure_ascii=False,
        ),
        "work_dir": "/tmp/interpreter_run",
    }

    sanitized = sanitize_tool_result("result_interpreter", raw)

    assert sanitized["success"] is True
    assert sanitized["operation"] == "analyze"
    assert sanitized["execution_output"].startswith("任务已完成")
    assert sanitized["work_dir"] == "/tmp/interpreter_run"
    assert sanitized["metadata"][0]["filename"] == "gvd_phage_meta_data.tsv"
    assert "sample_values" not in sanitized["metadata"][0]


def test_summarize_result_interpreter_uses_execution_summary() -> None:
    sanitized = sanitize_tool_result(
        "result_interpreter",
        {
            "success": True,
            "operation": "analyze",
            "execution_status": "success",
            "execution_output": json.dumps(
                {
                    "type": "result",
                    "result": "任务已完成！以下是结果摘要：\n\n筛选后噬菌体数：18490",
                },
                ensure_ascii=False,
            ),
        },
    )

    summary = summarize_tool_result("result_interpreter", sanitized)

    assert "result_interpreter analyze succeeded" in summary
    assert "任务已完成" in summary


def test_summarize_terminal_session_write_defaults_to_dispatch_summary() -> None:
    sanitized = sanitize_tool_result(
        "terminal_session",
        {
            "success": True,
            "bytes_sent": 32,
            "status": "completed",
            "command_state": "unverified",
            "verification_state": "not_attempted",
        },
    )

    summary = summarize_tool_result("terminal_session", sanitized)

    assert summary.startswith("terminal_session write: command dispatched;")


def test_sanitize_code_executor_rewrites_legacy_http_400_config_hint() -> None:
    sanitized = sanitize_tool_result(
        "code_executor",
        {
            "success": False,
            "language": "python",
            "error": (
                "Claude CLI execution failed: exit_code=1; "
                "stderr=Claude CLI crashed (likely HTTP 400 from API). "
                "Check ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL configuration."
            ),
        },
    )

    assert "HTTP 400 from upstream Anthropic-compatible API" in sanitized["error"]
    assert "ANTHROPIC_API_KEY" not in sanitized["error"]


def test_append_recent_tool_result_keeps_image_anchors_when_summary_only() -> None:
    extra_context = {}
    sanitized = {
        "success": True,
        "artifact_paths": ["tool_outputs/run_1/figure.png"],
        "artifact_gallery": [
            {
                "path": "tool_outputs/run_1/figure.png",
                "display_name": "figure.png",
                "source_tool": "code_executor",
            }
        ],
        "storage": {
            "preview_path": "/Users/apple/LLM/agent/runtime/session_demo/tool_outputs/run_1/figure.png",
            "relative": {
                "preview_path": "tool_outputs/run_1/figure.png",
            },
        },
        "stdout": "x" * 9000,
    }

    append_recent_tool_result(
        extra_context,
        "code_executor",
        "generated figure.png",
        sanitized,
    )

    entry = extra_context["recent_tool_results"][0]["result"]
    assert entry["_compressed"] is True
    assert entry["artifact_paths"] == ["tool_outputs/run_1/figure.png"]
    assert entry["artifact_gallery"][0]["path"] == "tool_outputs/run_1/figure.png"
    assert entry["storage"]["relative"]["preview_path"] == "tool_outputs/run_1/figure.png"
