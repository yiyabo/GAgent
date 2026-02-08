from tool_box.tools_impl.claude_code import (
    _detect_scope_blocked,
    _normalize_csv_values,
)


def test_normalize_csv_values_handles_strings_and_lists() -> None:
    assert _normalize_csv_values("Bash, Edit, Bash") == ["Bash", "Edit"]
    assert _normalize_csv_values(["Bash", "Edit", "Bash"]) == ["Bash", "Edit"]
    assert _normalize_csv_values(["Bash,Edit", "Read"]) == ["Bash", "Edit", "Read"]
    assert _normalize_csv_values(None) == []


def test_detect_scope_blocked_reads_marker_from_stdout_and_json() -> None:
    stdout = "\n".join(
        [
            "STATUS: BLOCKED_SCOPE",
            "REASON: NEED_ATOMIC_TASK",
            "DETAIL: request contains multiple objectives",
        ]
    )
    assert _detect_scope_blocked(stdout, None) == "request contains multiple objectives"

    output_data = {
        "result": "STATUS: BLOCKED_SCOPE\nREASON: NEED_ATOMIC_TASK\nDETAIL: task is not atomic"
    }
    assert _detect_scope_blocked("", output_data) == "task is not atomic"
