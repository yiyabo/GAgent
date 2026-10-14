"""Rule errors stay distinct from missing outputs through both execution lanes."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from app.services.deep_think.acceptance import acceptance_missing, prepare_acceptance_spec
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans.task_verification import TaskVerificationService


def _metadata(missing=False):
    checks = [
        {"type": "file_exists", "path": "corpus.jsonl"},
        {"type": "glob_count_at_least", "path": "corpus.jsonl", "min_count": 80},
    ]
    if missing:
        checks.append({"type": "file_exists", "path": "report.md"})
    return {
        "required_outputs": [{"kind": "data", "target_path": "corpus.jsonl", "extensions": [".jsonl"]}],
        "acceptance_criteria": {"blocking": True, "checks": checks},
    }


@pytest.mark.parametrize("missing", [False, True])
def test_rule_error_survives_verification_persist_reload_and_status(tmp_path, monkeypatch, missing):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "corpus.jsonl"
    output.write_text('{"synthetic": true}\n', encoding="utf-8")
    metadata = _metadata(missing)
    original = copy.deepcopy(metadata)
    node = PlanNode(id=1, plan_id=1, name="Collect records", metadata=metadata)
    final = TaskVerificationService().finalize_payload(
        node, {"status": "completed", "artifact_paths": [str(output)]}, execution_status="completed"
    )
    restored = PlanNode(id=1, plan_id=1, name=node.name, metadata=original,
                        status=final.final_status, execution_result=json.dumps(final.payload))
    tree = PlanTree(id=1, title="Fixture")
    tree.nodes[1] = restored
    tree.rebuild_adjacency()
    state = PlanStatusResolver().resolve_plan_states(1, tree)[1]
    expected = "failed" if missing else "needs_review"
    assert final.final_status == state["effective_status"] == expected
    assert TaskVerificationService.has_authoritative_verification_failure(restored, final.payload["metadata"]) is missing
    if not missing:
        assert state["reason_code"] == "verification_config_error"
        assert "规则" in state["status_reason"]
        assert final.payload["metadata"]["verification"]["failures"] == []
    assert restored.metadata["acceptance_criteria"] == original["acceptance_criteria"]
    assert output.read_text() == '{"synthetic": true}\n'


@pytest.mark.parametrize("missing", [False, True])
def test_loop_does_not_treat_broken_checker_as_deliverable(tmp_path, monkeypatch, missing):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "corpus.jsonl"
    output.write_text('{"synthetic": true}\n', encoding="utf-8")
    context = {**_metadata(missing), "output_spec_base_dir": str(tmp_path)}
    agent = SimpleNamespace(request_profile={}, _request_tier=lambda: "execute")
    asyncio.run(prepare_acceptance_spec(agent, "Produce the declared files", context))
    gaps = acceptance_missing(agent, [], [str(output)])
    assert "glob_count_at_least" not in gaps
    assert bool(gaps) is missing
    assert agent._output_verification["status"] == ("failed" if missing else "config_error")
    assert agent._output_verification["config_errors"][0]["verification_config_error"]
    assert context["acceptance_criteria"]["checks"][1]["min_count"] == 80


def test_mixed_persisted_rule_and_content_failure_remains_rejected():
    node = PlanNode(id=1, plan_id=1, name="Fixture", metadata=_metadata())
    metadata = {"verification": {"status": "config_error", "failures": [
        {"verification_config_error": True, "type": "glob_count_at_least"},
        {"type": "file_exists", "success": False, "path": "corpus.jsonl"},
    ]}}
    assert TaskVerificationService.has_authoritative_verification_failure(node, metadata)
