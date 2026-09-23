"""gating_probe regression: non-JSON code_executor results must not crash detection."""

from __future__ import annotations

from app.services.deep_think.gating_probe import (
    _detect_partial_completion_in_tool_results,
)


def test_partial_completion_detection_survives_non_json_result_text() -> None:
    """Bare-name JSONDecodeError (pre-split latent NameError) must not resurface."""
    results = [
        {"tool_name": "code_executor", "tool_result_text": "not a json payload at all"},
        {"tool_name": "code_executor", "tool_result_text": "{broken json"},
        {"tool_name": "code_executor", "tool_result_text": None},
    ]
    assert _detect_partial_completion_in_tool_results(results) is None


def test_partial_completion_detection_still_detects_flag() -> None:
    results = [
        {
            "tool_name": "code_executor",
            "tool_result_text": '{"result": {"partial_completion_suspected": true, "stderr": "boom"}}',
        },
    ]
    found = _detect_partial_completion_in_tool_results(results)
    assert found is not None
