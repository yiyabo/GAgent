from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.plans.artifact_preflight import ArtifactPreflightIssue, ArtifactPreflightResult
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_rubric_evaluator import PlanRubricResult
from tool_box.tools_impl.plan_tools import _create_plan, _optimize_plan, _review_plan


def _build_tree() -> PlanTree:
    root = PlanNode(
        id=1,
        plan_id=1,
        name="Demo Plan",
        task_type="root",
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


class _ReviewRepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self.tree = tree
        self.metadata_updates = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self.tree.id
        return self.tree

    def update_plan_metadata(self, plan_id: int, metadata) -> None:
        self.metadata_updates.append((plan_id, metadata))

    def create_task(
        self,
        plan_id: int,
        *,
        name: str,
        status: str,
        instruction: str,
        parent_id: int | None,
        metadata=None,
        dependencies=None,
    ):
        _ = plan_id, name, status, instruction, parent_id, metadata, dependencies
        raise AssertionError("create_task should not run in this test")

    def update_task(self, *args, **kwargs) -> None:
        _ = args, kwargs


class _OptimizeRepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self.tree = tree

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self.tree.id
        return self.tree

    def create_task(
        self,
        plan_id: int,
        *,
        name: str,
        status: str,
        instruction: str,
        parent_id: int | None,
        metadata=None,
        dependencies=None,
    ) -> PlanNode:
        next_id = max(self.tree.nodes) + 1
        node = PlanNode(
            id=next_id,
            plan_id=plan_id,
            name=name,
            status=status,
            instruction=instruction,
            parent_id=parent_id,
            dependencies=list(dependencies or []),
        )
        self.tree.nodes[next_id] = node
        self.tree.rebuild_adjacency()
        return node

    def update_task(self, plan_id: int, task_id: int, **kwargs) -> None:
        assert plan_id == self.tree.id
        node = self.tree.nodes[task_id]
        for key, value in kwargs.items():
            setattr(node, key, value)
        self.tree.rebuild_adjacency()

    def delete_task(self, plan_id: int, task_id: int) -> None:
        assert plan_id == self.tree.id
        self.tree.nodes.pop(task_id)
        self.tree.rebuild_adjacency()

    def move_task(self, plan_id: int, task_id: int, *, new_position: int) -> None:
        assert plan_id == self.tree.id
        node = self.tree.nodes[task_id]
        node.position = new_position


def test_create_plan_uses_integrated_generation_and_allows_missing_seed_tasks(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_create_plan_and_generate(**kwargs):
        captured.update(kwargs)
        generated_nodes = {
            1: SimpleNamespace(id=1),
            2: SimpleNamespace(id=2),
            3: SimpleNamespace(id=3),
        }
        return SimpleNamespace(
            plan_tree=SimpleNamespace(id=7, nodes=generated_nodes),
            root_task_id=1,
            seeded_tasks=[],
            decomposition=SimpleNamespace(
                created_tasks=[
                    SimpleNamespace(id=2, name="Collect evidence", instruction="Collect evidence.", parent_id=1),
                    SimpleNamespace(id=3, name="Execute analysis", instruction="Execute analysis.", parent_id=1),
                ],
                failed_nodes=[],
                stats={"created": 2},
            ),
            collected_materials=[{"tool": "web_search", "summary": "Latest benchmark guidance."}],
            session_context={"session_id": "session-1"},
            decomposition_status="completed",
            auto_completed_generation=True,
        )

    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: object())
    monkeypatch.setattr(
        "tool_box.tools_impl.plan_tools.create_plan_and_generate",
        _fake_create_plan_and_generate,
    )

    result = asyncio.run(
        _create_plan(
            "Integrated Plan",
            "Need a full generated plan",
            None,
            tool_context=SimpleNamespace(
                session_id="session-1",
                plan_id=None,
                extra={"owner_id": "alice", "user_message": "make me a plan"},
            ),
        )
    )

    assert result["success"] is True
    assert result["decomposition_completed"] is True
    assert result["task_count"] == 2
    assert result["material_collection"]["used"] is True
    assert captured["tasks"] is None
    assert captured["owner"] == "alice"


def test_review_plan_ensures_generation_ready_before_scoring(monkeypatch) -> None:
    tree = _build_tree()
    repo = _ReviewRepoStub(tree)
    readiness_called = {"value": False}
    rubric_result = PlanRubricResult(
        plan_id=tree.id,
        rubric_version="plan_rubric_v1",
        evaluator_provider="qwen",
        evaluator_model="qwen3.6-plus",
        evaluated_at="2026-03-18T00:00:00Z",
        overall_score=91.0,
        dimension_scores={"accuracy": 91.0},
        subcriteria_scores={"accuracy": {"A1": 0.91}},
        evidence={"accuracy": {"A1": ["[1] grounded"]}},
        feedback={
            "status": "completed",
            "strengths": ["Clear structure."],
            "weaknesses": [],
            "actionable_revisions": [],
        },
        rule_evidence={},
    )

    async def _fake_ensure(**_kwargs):
        readiness_called["value"] = True
        return SimpleNamespace(plan_tree=tree, decomposition_status="completed")

    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)
    monkeypatch.setattr("tool_box.tools_impl.plan_tools.ensure_plan_generation_ready", _fake_ensure)
    monkeypatch.setattr(
        "app.services.plans.plan_rubric_evaluator.evaluate_plan_rubric",
        lambda *_args, **_kwargs: rubric_result,
    )

    result = asyncio.run(_review_plan(tree.id))

    assert result["success"] is True
    assert result["decomposition_status"] == "completed"
    assert readiness_called["value"] is True
    assert repo.metadata_updates


def test_optimize_plan_ensures_generation_ready_before_mutation(monkeypatch) -> None:
    tree = _build_tree()
    repo = _OptimizeRepoStub(tree)
    readiness_called = {"value": False}

    async def _fake_ensure(**_kwargs):
        readiness_called["value"] = True
        return SimpleNamespace(plan_tree=tree, decomposition_status="completed")

    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)
    monkeypatch.setattr("tool_box.tools_impl.plan_tools.ensure_plan_generation_ready", _fake_ensure)

    result = asyncio.run(
        _optimize_plan(
            1,
            [{"action": "update_task", "task_id": 2, "instruction": "Updated task A"}],
        )
    )

    assert result["success"] is True
    assert result["decomposition_status"] == "completed"
    assert readiness_called["value"] is True
    assert repo.tree.nodes[2].instruction == "Updated task A"


# NOTE: test_review_plan_blocks_on_artifact_preflight_failure and
# test_optimize_plan_blocks_on_artifact_preflight_failure were removed —
# review and optimize intentionally skip preflight so they can fix
# broken artifact contracts.