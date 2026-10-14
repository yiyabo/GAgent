import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.read_projection import execution_summary
from tool_box.tools_impl.plan_tools import _get_plan


@pytest.mark.parametrize("raw", ["bad JSON", "[]", 1])
def test_malformed_previous_result_is_unknown(raw):
    assert execution_summary(SimpleNamespace(execution_result=raw)) == {"last_status": "unknown"}


def test_projection_distinguishes_live_retry_from_old_failure_and_excludes_transcript(monkeypatch):
    previous = {
        "status": "failed",
        "result": "huge model transcript",
        "metadata": {
            "verification_status": "failed",
            "failure_kind": "contract_mismatch",
            "repair_attempts": 2,
            "execution_history_ref": {"path": "private.json"},
            "contract_diff": {"missing_required_outputs": ["outline.md", "sources.bib"]},
            "verification": {
                "failures": [
                    {
                        "type": "file_exists",
                        "path": "outline.md",
                        "message": "Missing output",
                        "hard": True,
                        "internal_transcript": "private",
                    }
                ]
            },
        },
    }
    node = PlanNode(id=14, plan_id=3, name="Outline", status="running", execution_result=json.dumps(previous))
    tree = PlanTree(id=3, title="Review", nodes={14: node}, adjacency={None: [14]})
    monkeypatch.setattr(
        "app.repository.plan_repository.PlanRepository", lambda: SimpleNamespace(get_plan_tree=lambda _: tree)
    )
    result = asyncio.run(_get_plan(3))
    task = result["task_tree"][0]
    assert task["status"] == "running" and task["last_execution"]["last_status"] == "failed"
    assert task["last_execution"]["missing_outputs"] == ["outline.md", "sources.bib"]
    assert task["last_execution"]["repair_attempts"] == 2
    assert task["last_execution"]["failed_checks"][0]["message"] == "Missing output"
    assert "previous result" in result["state_note"]
    assert "transcript" not in json.dumps(result) and "private" not in json.dumps(result)


def test_projection_is_bounded_and_missing_result_is_not_claimed_complete():
    assert execution_summary(SimpleNamespace(execution_result=None)) is None
    result = execution_summary(
        SimpleNamespace(
            execution_result={
                "status": "failed",
                "metadata": {
                    "contract_diff": {"missing_required_outputs": [str(i) for i in range(40)]},
                    "verification": {"failures": [{"message": "missing"}] * 30},
                },
            }
        )
    )
    assert len(result["missing_outputs"]) == 20 and result["missing_output_count"] == 40
    assert len(result["failed_checks"]) == 10 and result["failed_check_count"] == 30
