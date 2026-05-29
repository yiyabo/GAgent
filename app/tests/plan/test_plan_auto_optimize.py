import asyncio
from types import SimpleNamespace
from typing import cast

from app.routers.chat.agent import StructuredChatAgent
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_optimizer import auto_review_and_optimize_plan, plan_review_needs_optimization
from app.repository.plan_repository import PlanRepository
from app.services.plans.plan_rubric_evaluator import PlanRubricResult, rubric_definition_en
from tool_box.tools_impl.plan_tools import _optimize_plan


class _OptimizeRepoStub(PlanRepository):
    def __init__(self, tree: PlanTree) -> None:
        super().__init__()
        self.tree = tree
        self.metadata_updates = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self.tree.id
        return self.tree

    def create_task(
        self,
        plan_id: int,
        *,
        name: str,
        status: str = "pending",
        instruction: str | None = None,
        parent_id: int | None = None,
        metadata: dict[str, object] | None = None,
        dependencies: list[int] | None = None,
        position: int | None = None,
        anchor_task_id: int | None = None,
        anchor_position: str | None = None,
    ) -> PlanNode:
        _ = metadata, position, anchor_task_id, anchor_position
        next_id = max(self.tree.nodes) + 1
        node = PlanNode(
            id=next_id,
            plan_id=plan_id,
            name=name,
            status=status,
            instruction=instruction or "",
            parent_id=parent_id,
            dependencies=list(dependencies or []),
        )
        self.tree.nodes[next_id] = node
        self.tree.rebuild_adjacency()
        return node

    def update_task(
        self,
        plan_id: int,
        task_id: int,
        *,
        name: str | None = None,
        status: str | None = None,
        instruction: str | None = None,
        metadata: dict[str, object] | None = None,
        dependencies: list[int] | None = None,
        context_combined: str | None = None,
        context_sections: list[object] | None = None,
        context_meta: dict[str, object] | None = None,
        execution_result: str | None = None,
    ) -> PlanNode:
        _ = context_combined, context_sections, context_meta
        assert plan_id == self.tree.id
        node = self.tree.nodes[task_id]
        updates = {
            "name": name,
            "status": status,
            "instruction": instruction,
            "metadata": metadata,
            "dependencies": dependencies,
            "execution_result": execution_result,
        }
        for key, value in updates.items():
            if value is not None:
                setattr(node, key, value)
        self.tree.rebuild_adjacency()
        return node

    def update_plan_metadata(self, plan_id: int, metadata) -> None:
        assert plan_id == self.tree.id
        self.metadata_updates.append((plan_id, metadata))
        self.tree.metadata = dict(metadata or {})

    def apply_changes_atomically(self, plan_id: int, changes):
        assert plan_id == self.tree.id
        applied = []
        for change in changes:
            action = change.get("action")
            if action == "add_task":
                node = self.create_task(
                    plan_id,
                    name=change["name"],
                    status="pending",
                    instruction=change["instruction"],
                    parent_id=change.get("parent_id"),
                    dependencies=change.get("dependencies"),
                )
                applied.append({"action": "add_task", "task_id": node.id, "name": node.name})
            elif action == "update_task":
                task_id = int(change["task_id"])
                updates = {key: change[key] for key in ("name", "instruction", "dependencies") if key in change}
                self.update_task(plan_id, task_id, **updates)
                applied.append({"action": "update_task", "task_id": task_id, "updated_fields": list(updates)})
        return applied

    def reindex_all_positions(self, plan_id: int) -> None:
        assert plan_id == self.tree.id

    def upsert_plan_tree(self, tree: PlanTree, *, note: str | None = None) -> None:
        _ = note
        self.tree = tree


def _build_tree() -> PlanTree:
    root = PlanNode(
        id=1,
        plan_id=1,
        name="Demo Plan",
        metadata={"is_root": True, "task_type": "root"},
        parent_id=None,
        instruction="Original description",
    )
    nodes = {
        1: root,
        2: PlanNode(id=2, plan_id=1, name="Task A", instruction="Original task A", parent_id=1),
        3: PlanNode(id=3, plan_id=1, name="Task B", instruction="Original task B", parent_id=1),
    }
    tree = PlanTree(
        id=1,
        title="Demo Plan",
        description="Original description",
        nodes=nodes,
    )
    tree.rebuild_adjacency()
    return tree


def _build_review_result(*, overall: float, innovation: float, revisions=None) -> PlanRubricResult:
    return PlanRubricResult(
        plan_id=1,
        rubric_version="plan_rubric_v1",
        evaluator_provider="qwen",
        evaluator_model="qwen3.6-plus",
        evaluated_at="2026-03-18T00:00:00Z",
        overall_score=overall,
        dimension_scores={
            "contextual_completeness": 88.0,
            "accuracy": 86.0,
            "task_granularity_atomicity": 83.0,
            "reproducibility_parameterization": 84.0,
            "scientific_rigor": 81.0,
            "innovation_feasibility": innovation,
        },
        subcriteria_scores={"innovation_feasibility": {"I1": 0.5}},
        evidence={"innovation_feasibility": {"I1": ["[1] Missing feasibility framing"]}},
        feedback={
            "strengths": ["Strong overall structure"],
            "weaknesses": ["Innovation and feasibility framing are thin"],
            "actionable_revisions": list(revisions or []),
        },
        rule_evidence={},
    )


def test_rubric_definition_includes_innovation_feasibility() -> None:
    rubric = rubric_definition_en()

    assert "innovation_feasibility" in rubric
    assert set(rubric["innovation_feasibility"]["subcriteria"].keys()) == {
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
    }


def test_plan_review_needs_optimization_for_low_innovation_score() -> None:
    result = _build_review_result(
        overall=82.0,
        innovation=58.0,
        revisions=["Add a feasibility and fallback task."],
    )

    assert plan_review_needs_optimization(result) is True


def test_plan_review_needs_optimization_uses_configurable_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.plans.plan_optimizer.get_settings",
        lambda: SimpleNamespace(
            plan_auto_optimize_overall_threshold=80.0,
            plan_auto_optimize_dimension_threshold=50.0,
            plan_auto_optimize_max_changes=3,
        ),
    )

    result = _build_review_result(
        overall=82.0,
        innovation=58.0,
        revisions=[],
    )

    assert plan_review_needs_optimization(result) is False


def test_optimize_plan_auto_generates_changes_when_missing(monkeypatch) -> None:
    repo = _OptimizeRepoStub(_build_tree())
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)

    async def _fake_ensure_plan_generation_ready(**_kwargs):
        return SimpleNamespace(plan_tree=repo.tree, decomposition_status="completed")

    monkeypatch.setattr(
        "tool_box.tools_impl.plan_tools.ensure_plan_generation_ready",
        _fake_ensure_plan_generation_ready,
    )

    async def _fake_auto_optimize_plan(*, plan_id: int, repo=None, **_kwargs):
        assert plan_id == 1
        assert repo is not None
        return SimpleNamespace(
            review_before=_build_review_result(overall=72.0, innovation=55.0),
            review_after=_build_review_result(overall=89.0, innovation=82.0),
            generated_changes=[
                {
                    "action": "add_task",
                    "name": "Feasibility check",
                    "instruction": "Validate resource and fallback assumptions.",
                    "parent_id": 1,
                }
            ],
            applied_changes=[
                {
                    "action": "add_task",
                    "name": "Feasibility check",
                }
            ],
            summary="Applied rubric-driven improvements.",
            optimization_needed=True,
        )

    monkeypatch.setattr("tool_box.tools_impl.plan_tools.auto_optimize_plan", _fake_auto_optimize_plan)

    result = asyncio.run(_optimize_plan(1, None))

    assert result["success"] is True
    assert result["auto_generated_changes"] is True
    assert result["applied_changes"] == 1
    assert result["rubric_score_before"] == 72.0
    assert result["rubric_score_after"] == 89.0
    assert result["generated_changes"][0]["action"] == "add_task"


def test_auto_review_and_optimize_plan_applies_low_score_plan(monkeypatch) -> None:
    repo = _OptimizeRepoStub(_build_tree())
    low_review = _build_review_result(
        overall=72.0,
        innovation=55.0,
        revisions=["Add a feasibility and fallback validation step."],
    )
    high_review = _build_review_result(overall=89.0, innovation=83.0, revisions=[])
    review_calls = [low_review, high_review]

    def _fake_evaluate(*_args, **_kwargs):
        return review_calls.pop(0)

    monkeypatch.setattr("app.services.plans.plan_optimizer.evaluate_plan_rubric", _fake_evaluate)
    monkeypatch.setattr(
        "app.services.plans.plan_optimizer._call_optimizer_llm",
        lambda *_args, **_kwargs: SimpleNamespace(
            summary="Added feasibility guardrails.",
            rationale=["Low feasibility score required a concrete validation step."],
            changes=[
                {
                    "action": "add_task",
                    "name": "Validate feasibility",
                    "instruction": "Check resource constraints, fallback paths, and acceptance criteria.",
                    "parent_id": 1,
                }
            ],
        ),
    )

    outcome = asyncio.run(auto_review_and_optimize_plan(plan_id=1, repo=cast(PlanRepository, repo)))
    payload = outcome.to_dict()

    assert payload["success"] is True
    assert payload["status"] == "optimized"
    assert payload["optimization_needed"] is True
    assert payload["auto_optimize"]["applied_changes"] == 1
    assert payload["auto_optimize"]["rubric_score_before"] == 72.0
    assert payload["auto_optimize"]["rubric_score_after"] == 89.0
    assert repo.tree.metadata["plan_auto_review"]["trigger"] == "post_create"
    assert repo.tree.metadata["plan_evaluation"]["overall_score"] == 89.0


def test_auto_review_and_optimize_plan_skips_high_score_plan(monkeypatch) -> None:
    repo = _OptimizeRepoStub(_build_tree())
    high_review = _build_review_result(overall=92.0, innovation=88.0, revisions=[])

    monkeypatch.setattr(
        "app.services.plans.plan_optimizer.evaluate_plan_rubric",
        lambda *_args, **_kwargs: high_review,
    )
    monkeypatch.setattr(
        "app.services.plans.plan_optimizer._call_optimizer_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("optimizer should not run")),
    )

    outcome = asyncio.run(auto_review_and_optimize_plan(plan_id=1, repo=cast(PlanRepository, repo)))
    payload = outcome.to_dict()

    assert payload["success"] is True
    assert payload["status"] == "completed"
    assert payload["optimization_needed"] is False
    assert "auto_optimize" not in payload
    assert repo.tree.metadata["plan_auto_review"]["status"] == "completed"
    assert repo.tree.metadata["plan_evaluation"]["overall_score"] == 92.0


def test_created_plan_auto_review_sync_triggers_optimize_for_low_score(monkeypatch) -> None:
    tool_calls = []

    async def _fake_execute_tool(name: str, **params):
        assert name == "plan_operation"
        tool_calls.append(params["operation"])
        if params["operation"] == "review":
            return {
                "success": True,
                "status": "needs_improvement",
                "rubric_score": 74.0,
                "rubric_dimension_scores": {
                    "innovation_feasibility": 59.0,
                    "scientific_rigor": 76.0,
                },
                "rubric_feedback": {
                    "strengths": ["Plan has a clear main objective"],
                    "weaknesses": ["Feasibility checks are missing"],
                    "actionable_revisions": ["Add a resource and fallback validation step."],
                },
                "rubric_evaluator": {
                    "provider": "qwen",
                    "model": "qwen3.6-plus",
                    "evaluated_at": "2026-03-18T00:00:00Z",
                    "rubric_version": "plan_rubric_v1",
                },
            }
        if params["operation"] == "optimize":
            return {
                "success": True,
                "applied_changes": 2,
                "rubric_score_before": 74.0,
                "rubric_score_after": 88.0,
            }
        raise AssertionError(f"Unexpected operation: {params['operation']}")

    monkeypatch.setattr("app.routers.chat.agent.execute_tool", _fake_execute_tool)

    agent = object.__new__(StructuredChatAgent)
    result = StructuredChatAgent._run_created_plan_auto_review_sync(agent, 67)

    assert tool_calls == ["review", "optimize"]
    assert result is not None
    assert result["auto_optimize"]["success"] is True
    assert result["auto_optimize"]["rubric_score_after"] == 88.0


def test_auto_optimize_returns_gracefully_when_post_commit_scoring_fails(monkeypatch) -> None:
    """auto_optimize_plan should return a degraded outcome (not raise) when
    capture_plan_optimization_outcome fails after changes are already committed."""
    from app.services.plans.plan_optimizer import auto_optimize_plan
    from dataclasses import asdict

    tree = _build_tree()
    review = _build_review_result(overall=72.0, innovation=55.0)
    # Inject cached review into tree metadata so resolve_plan_review_result
    # returns it without hitting a real evaluator.
    tree.metadata = {"plan_evaluation": asdict(review)}

    repo = _OptimizeRepoStub(tree)

    # Stub the LLM optimizer to return a valid proposal
    monkeypatch.setattr(
        "app.services.plans.plan_optimizer._call_optimizer_llm",
        lambda prompt, **kw: SimpleNamespace(
            summary="Test optimization",
            rationale=["reason"],
            changes=[{"action": "update_task", "task_id": 2, "name": "Updated A"}],
        ),
    )

    # Make capture_plan_optimization_outcome raise after changes are committed
    async def _failing_capture(**kwargs):
        raise RuntimeError("Simulated post-commit scoring failure")

    monkeypatch.setattr(
        "app.services.plans.plan_optimizer.capture_plan_optimization_outcome",
        _failing_capture,
    )

    # Stub resolve_plan_review_result to return the cached review
    async def _resolve_review(tree, **kw):
        return review

    monkeypatch.setattr(
        "app.services.plans.plan_optimizer.resolve_plan_review_result",
        _resolve_review,
    )

    # Stub apply_changes_atomically to simulate successful commit
    repo.apply_changes_atomically = lambda plan_id, changes: [
        {"action": "update_task", "task_id": 2, "updated_fields": ["name"]}
    ]
    monkeypatch.setattr("app.services.plans.plan_optimizer.PlanRepository", lambda: repo)

    result = asyncio.run(auto_optimize_plan(plan_id=1, repo=cast(PlanRepository, repo)))

    # Should succeed with degraded outcome, not raise
    assert result is not None
    assert len(result.applied_changes) == 1
    assert result.review_after is None  # scoring failed
    assert result.review_before is not None  # pre-change review preserved
    assert result.optimization_needed is True
