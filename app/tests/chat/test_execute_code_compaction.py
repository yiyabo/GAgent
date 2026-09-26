"""Native-lane compaction for execute_code results (dispatch branch).

Without a branch the raw JSON (up to 50KB stdout head/tail + spill metadata)
rode into the prompt whole whenever it exceeded MAX_TOOL_RESULT_TEXT_CHARS;
the branch keeps the operational payload (status/error/hint, kernel truth
metadata, produced_files, spill pointer) inside the cap.
"""

from __future__ import annotations

from app.services.deep_think_agent import DeepThinkAgent


def test_compact_execute_code_keeps_operational_payload() -> None:
    result = {
        "success": True,
        "status": "ok",
        "exit_code": 0,
        "output": "short output",
        "duration_seconds": 1.25,
        "tool_calls_made": 3,
        "kernel": {"reused": True, "execution_count": 4, "state_reset": False, "noise": "drop"},
        "produced_files": ["results/chart.png"],
        "noise_top": "drop",
    }

    compact = DeepThinkAgent._compact_execute_code_result_for_llm(result)

    assert compact["tool"] == "execute_code"
    assert compact["success"] is True
    assert compact["status"] == "ok"
    assert compact["output"] == "short output"
    assert compact["duration_seconds"] == 1.25
    assert compact["tool_calls_made"] == 3
    assert compact["kernel"] == {"reused": True, "execution_count": 4, "state_reset": False}
    assert compact["produced_files"] == ["results/chart.png"]
    assert "noise_top" not in compact


def test_compact_execute_code_head_tail_caps_oversized_output() -> None:
    result = {
        "success": True,
        "status": "ok",
        "exit_code": 0,
        "output": "A" * 3000 + "B" * 3000,
    }

    compact = DeepThinkAgent._compact_execute_code_result_for_llm(result)

    assert "…[truncated]…" in compact["output"]
    assert compact["output"].startswith("A" * 100)
    assert compact["output"].endswith("B" * 100)
    assert len(compact["output"]) < 5000


def test_compact_execute_code_non_dict_returns_none() -> None:
    assert DeepThinkAgent._compact_execute_code_result_for_llm("nope") is None


def test_compact_dispatch_routes_execute_code() -> None:
    result = {"success": True, "status": "ok", "exit_code": 0, "output": "x"}

    compact = DeepThinkAgent._compact_tool_result_for_llm("execute_code", result)

    assert compact is not None
    assert compact["tool"] == "execute_code"
