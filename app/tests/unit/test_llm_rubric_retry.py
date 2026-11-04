"""Plan-review rubric retries an unusable evaluator reply, up to PLAN_RUBRIC_MAX_ATTEMPTS calls."""

import json

import pytest

import app.services.plans.plan_rubric_evaluator as rubric
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_rubric_evaluator import PLAN_RUBRIC_MAX_ATTEMPTS, evaluate_plan_rubric
from app.services.run_budget import RunDeadlineExceeded


def _tree() -> PlanTree:
    nodes = {
        1: PlanNode(id=1, plan_id=1, name="Collect genomes", instruction="Download genomes", parent_id=None, position=0, depth=0, path="/1"),
        2: PlanNode(id=2, plan_id=1, name="Annotate", instruction="Run Prodigal", parent_id=1, position=0, depth=1, path="/1/2"),
    }
    return PlanTree(id=1, title="Phage annotation", nodes=nodes, adjacency={None: [1], 1: [2]})


def _valid_reply() -> str:
    rubric_def = rubric.rubric_definition_en()
    subs = {dim: {k: 0.8 for k in data["subcriteria"]} for dim, data in rubric_def.items()}
    evidence = {dim: {k: [f"[1] {dim}.{k}"] for k in data["subcriteria"]} for dim, data in rubric_def.items()}
    payload = {
        "rubric_version": "plan_rubric_v1",
        "plan_id": 1,
        "subcriteria_scores": subs,
        "evidence": evidence,
        "feedback": {"strengths": ["ok"], "weaknesses": ["ok"], "actionable_revisions": ["ok"]},
    }
    return json.dumps(payload)


def _scripted(monkeypatch, replies):
    """Replace the transport with a scripted sequence; each item is a reply or an exception."""
    calls = []

    def fake(client, *, prompt, evaluator_model):
        calls.append(prompt)
        item = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(rubric, "_invoke_evaluator_client", fake)
    return calls


class _Client:
    provider = "openai"
    model = "CS/DeepSeek-V4-Flash-FP8"
    api_key = "test-key"
    url = "https://example.invalid/v1/chat/completions"


def test_valid_reply_is_not_retried(monkeypatch):
    calls = _scripted(monkeypatch, [_valid_reply()])
    res = evaluate_plan_rubric(_tree(), evaluator_client=_Client())
    assert len(calls) == 1
    assert res.feedback.get("status") != "evaluation_unavailable"


def test_unusable_reply_then_valid_succeeds_on_retry(monkeypatch):
    calls = _scripted(monkeypatch, ["not json at all", _valid_reply()])
    res = evaluate_plan_rubric(_tree(), evaluator_client=_Client())
    assert len(calls) == 2
    assert res.feedback.get("status") != "evaluation_unavailable"


def test_gives_up_after_max_attempts(monkeypatch):
    calls = _scripted(monkeypatch, ["garbage"])
    res = evaluate_plan_rubric(_tree(), evaluator_client=_Client())
    assert len(calls) == PLAN_RUBRIC_MAX_ATTEMPTS == 3
    assert res.feedback.get("status") == "evaluation_unavailable"
    assert "invalid JSON" in " ".join(res.feedback.get("weaknesses", []))


def test_schema_mismatch_is_retried_and_reports_its_reason(monkeypatch):
    bad = json.dumps({"rubric_version": "plan_rubric_v1", "subcriteria_scores": {}})
    calls = _scripted(monkeypatch, [bad])
    res = evaluate_plan_rubric(_tree(), evaluator_client=_Client())
    assert len(calls) == PLAN_RUBRIC_MAX_ATTEMPTS
    assert res.feedback.get("status") == "evaluation_unavailable"
    assert "schema mismatch" in " ".join(res.feedback.get("weaknesses", []))


def test_transport_error_is_not_retried(monkeypatch):
    calls = _scripted(monkeypatch, [ConnectionError("upstream down")])
    res = evaluate_plan_rubric(_tree(), evaluator_client=_Client())
    assert len(calls) == 1
    assert res.feedback.get("status") == "evaluation_unavailable"


def test_run_deadline_is_raised_without_retry(monkeypatch):
    calls = _scripted(monkeypatch, [RunDeadlineExceeded("budget gone")])
    with pytest.raises(RunDeadlineExceeded):
        evaluate_plan_rubric(_tree(), evaluator_client=_Client())
    assert len(calls) == 1
