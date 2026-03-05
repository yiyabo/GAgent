from __future__ import annotations

from app.routers.chat.action_execution import _build_lifecycle_consensus_from_tool_results


def test_lifecycle_consensus_agree() -> None:
    tool_results = [
        {
            "name": "deeppl",
            "result": {
                "success": True,
                "predicted_label": "lysogenic",
                "predicted_lifestyle": "temperate",
                "positive_window_fraction": 0.25,
            },
        },
        {
            "name": "phagescope",
            "result": {
                "success": True,
                "action": "result",
                "result_kind": "modules",
                "data": {
                    "results": {
                        "lifestyle": "temperate",
                    }
                },
            },
        },
    ]

    consensus = _build_lifecycle_consensus_from_tool_results(tool_results)
    assert consensus is not None
    assert consensus["consensus"] == "agree"
    assert consensus["confidence"] == "high"
    assert consensus["deeppl"]["label"] == "temperate"
    assert consensus["phagescope"]["label"] == "temperate"


def test_lifecycle_consensus_disagree() -> None:
    tool_results = [
        {
            "name": "deeppl",
            "result": {
                "success": True,
                "predicted_label": "lytic",
                "predicted_lifestyle": "virulent",
            },
        },
        {
            "name": "phagescope",
            "result": {
                "success": True,
                "action": "result",
                "data": {"results": {"lifestyle": "lysogenic"}},
            },
        },
    ]

    consensus = _build_lifecycle_consensus_from_tool_results(tool_results)
    assert consensus is not None
    assert consensus["consensus"] == "disagree"
    assert consensus["confidence"] == "needs_review"
    assert consensus["deeppl"]["label"] == "virulent"
    assert consensus["phagescope"]["label"] == "temperate"


def test_lifecycle_consensus_requires_both_signals() -> None:
    tool_results = [
        {
            "name": "deeppl",
            "result": {
                "success": True,
                "predicted_label": "lytic",
            },
        }
    ]

    consensus = _build_lifecycle_consensus_from_tool_results(tool_results)
    assert consensus is None

