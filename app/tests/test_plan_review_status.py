import asyncio
import json

from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_rubric_evaluator import (
    PlanRubricResult,
    evaluate_plan_rubric,
    rubric_definition_en,
)
from tool_box.tools_impl.plan_tools import _review_plan


class _RepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree
        self.metadata_updates = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self._tree.id
        return self._tree

    def update_plan_metadata(self, plan_id: int, metadata) -> None:
        self.metadata_updates.append((plan_id, metadata))


def _build_review_tree() -> PlanTree:
    root = PlanNode(
        id=1,
        plan_id=1,
        name="Root",
        task_type="root",
        metadata={"is_root": True, "task_type": "root"},
        parent_id=None,
    )
    nodes = {
        1: root,
        2: PlanNode(id=2, plan_id=1, name="Task A", instruction="Do task A carefully.", parent_id=1),
        3: PlanNode(id=3, plan_id=1, name="Task B", instruction="Do task B carefully.", parent_id=1),
        4: PlanNode(id=4, plan_id=1, name="Task C", instruction="Do task C carefully.", parent_id=1),
    }
    tree = PlanTree(
        id=1,
        title="Review Target",
        description="Plan used to verify degraded review semantics.",
        nodes=nodes,
    )
    tree.rebuild_adjacency()
    return tree


def test_review_plan_reports_degraded_status_when_rubric_is_unavailable(monkeypatch) -> None:
    tree = _build_review_tree()
    repo = _RepoStub(tree)
    rubric_result = PlanRubricResult(
        plan_id=tree.id,
        rubric_version="plan_rubric_v1",
        evaluator_provider="qwen",
        evaluator_model="qwen3.5-plus",
        evaluated_at="2026-03-18T00:00:00Z",
        overall_score=0.0,
        dimension_scores={"accuracy": 0.0},
        subcriteria_scores={"accuracy": {"A1": 0.0}},
        evidence={"accuracy": {"A1": []}},
        feedback={
            "status": "evaluation_unavailable",
            "strengths": [],
            "weaknesses": ["Evaluator LLM call failed."],
            "actionable_revisions": ["Retry when evaluator connectivity is restored."],
        },
        rule_evidence={},
    )

    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)
    monkeypatch.setattr(
        "app.services.plans.plan_rubric_evaluator.evaluate_plan_rubric",
        lambda *_args, **_kwargs: rubric_result,
    )

    result = asyncio.run(_review_plan(tree.id))

    assert result["success"] is True
    assert result["status"] == "evaluation_unavailable"
    assert result["structural_status"] == "good"
    assert result["health_score"] == 100
    assert result["structural_health_score"] == 100
    assert result["degraded"] is True
    assert "Rubric evaluation unavailable" in result["message"]
    assert repo.metadata_updates


class _AsyncOnlyEvaluatorClient:
    provider = "qwen"
    model = "qwen3.5-plus"
    api_key = "dummy-key"
    url = "https://example.com/v1/chat/completions"

    def __init__(self) -> None:
        self.sync_called = False
        self.stream_called = False
        self.async_called = False

    def chat(self, *_args, **_kwargs):
        self.sync_called = True
        raise AssertionError("sync chat should not be used for rubric evaluation")

    async def stream_chat_async(self, *_args, **_kwargs):
        self.stream_called = True
        rubric = rubric_definition_en()
        subcriteria_scores = {
            dim: {key: 0.8 for key in data["subcriteria"].keys()}
            for dim, data in rubric.items()
        }
        evidence = {
            dim: {key: [f"[1] Evidence for {dim}.{key}"] for key in data["subcriteria"].keys()}
            for dim, data in rubric.items()
        }
        payload = {
            "rubric_version": "plan_rubric_v1",
            "plan_id": 1,
            "subcriteria_scores": subcriteria_scores,
            "evidence": evidence,
            "feedback": {
                "strengths": ["Clear structure"],
                "weaknesses": ["Needs a bit more parameter detail"],
                "actionable_revisions": ["Add explicit dataset versions"],
            },
        }
        yield json.dumps(payload, ensure_ascii=False)

    async def chat_async(self, *_args, **_kwargs):
        self.async_called = True
        rubric = rubric_definition_en()
        subcriteria_scores = {
            dim: {key: 0.8 for key in data["subcriteria"].keys()}
            for dim, data in rubric.items()
        }
        evidence = {
            dim: {key: [f"[1] Evidence for {dim}.{key}"] for key in data["subcriteria"].keys()}
            for dim, data in rubric.items()
        }
        payload = {
            "rubric_version": "plan_rubric_v1",
            "plan_id": 1,
            "subcriteria_scores": subcriteria_scores,
            "evidence": evidence,
            "feedback": {
                "strengths": ["Clear structure"],
                "weaknesses": ["Needs a bit more parameter detail"],
                "actionable_revisions": ["Add explicit dataset versions"],
            },
        }
        return json.dumps(payload, ensure_ascii=False)


def test_evaluate_plan_rubric_prefers_async_client_path() -> None:
    tree = _build_review_tree()
    client = _AsyncOnlyEvaluatorClient()

    result = evaluate_plan_rubric(
        tree,
        evaluator_client=client,
    )

    assert client.stream_called is True
    assert client.async_called is False
    assert client.sync_called is False
    assert result.feedback.get("status") != "evaluation_unavailable"
    assert result.overall_score > 0
