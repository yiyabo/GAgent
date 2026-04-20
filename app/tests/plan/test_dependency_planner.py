import json
from types import SimpleNamespace

import pytest

from app.services.plans.dependency_planner import compute_dependency_plan
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor
from app.services.plans.plan_models import PlanNode, PlanTree


def test_dependency_planner_topological_order():
    tree = PlanTree(id=1, title="Test Plan")
    tree.nodes = {
        1: PlanNode(id=1, plan_id=1, name="Target", status="pending", dependencies=[2, 3]),
        2: PlanNode(id=2, plan_id=1, name="Dep A", status="pending", dependencies=[4]),
        3: PlanNode(id=3, plan_id=1, name="Dep B", status="completed", dependencies=[]),
        4: PlanNode(id=4, plan_id=1, name="Dep C", status="pending", dependencies=[]),
    }

    plan = compute_dependency_plan(tree, 1)

    assert plan.cycle_detected is False
    assert plan.direct_dependencies == [2, 3]
    assert set(plan.closure_dependencies) == {2, 3, 4}
    assert set(plan.missing_dependencies) == {2, 4}
    assert plan.running_dependencies == []
    assert plan.execution_order == [4, 2, 1]


def test_dependency_planner_cycle_detection():
    tree = PlanTree(id=1, title="Cycle Plan")
    tree.nodes = {
        1: PlanNode(id=1, plan_id=1, name="A", status="pending", dependencies=[2]),
        2: PlanNode(id=2, plan_id=1, name="B", status="pending", dependencies=[1]),
    }

    plan = compute_dependency_plan(tree, 1)

    assert plan.cycle_detected is True
    assert plan.execution_order == []
    assert plan.cycle_paths
    assert plan.cycle_paths[0][0] == plan.cycle_paths[0][-1]


class _FakeRepo:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree
        self.update_calls = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self._tree

    def update_task(self, plan_id: int, task_id: int, **kwargs):
        self.update_calls.append((plan_id, task_id, dict(kwargs)))
        node = self._tree.nodes[task_id]
        if "status" in kwargs and kwargs["status"] is not None:
            node.status = kwargs["status"]
        if "execution_result" in kwargs and kwargs["execution_result"] is not None:
            node.execution_result = kwargs["execution_result"]
        return node


class _LLMStub:
    def generate(self, prompt: str, config):  # pragma: no cover - should not be called
        raise AssertionError("LLM should not be invoked when dependencies block execution.")


def test_plan_executor_persists_skip_reason_when_blocked():
    tree = PlanTree(id=1, title="Skip Plan")
    tree.nodes = {
        1: PlanNode(id=1, plan_id=1, name="A", status="pending", dependencies=[2]),
        2: PlanNode(id=2, plan_id=1, name="B", status="pending", dependencies=[]),
    }

    repo = _FakeRepo(tree)
    executor = PlanExecutor(repo=repo, llm_service=_LLMStub())

    result = executor.execute_task(1, 1)

    assert result.status == "skipped"
    assert repo.update_calls, "Expected PlanExecutor to persist execution_result for skipped tasks."

    _, _, kwargs = repo.update_calls[-1]
    assert kwargs.get("status") == "skipped"
    raw = kwargs.get("execution_result")
    assert isinstance(raw, str) and raw.strip()

    payload = json.loads(raw)
    assert payload.get("status") == "skipped"
    assert payload.get("metadata", {}).get("blocked_by_dependencies") is True


def test_plan_executor_passes_reference_context_into_deep_think(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class _RepoWithContext(_FakeRepo):
        def update_plan_metadata(self, *_args, **_kwargs):
            return None

    class _DeepThinkProbe:
        def __init__(self, *args, **kwargs):
            captured["available_tools"] = kwargs.get("available_tools")
            return

        def pause(self) -> None:
            return

        def resume(self) -> None:
            return

        def skip_step(self) -> None:
            return

        async def think(self, user_query: str, context=None, task_context=None):
            captured["user_query"] = user_query
            captured["context"] = context
            captured["task_context"] = task_context
            return SimpleNamespace(
                final_answer="done",
                thinking_summary="ok",
                confidence=0.9,
                tools_used=[],
                total_iterations=1,
                thinking_steps=[],
            )

    monkeypatch.setattr("app.services.plans.plan_executor.DeepThinkAgent", _DeepThinkProbe)

    dep_payload = {
        "status": "success",
        "metadata": {
            "deliverables": {
                "manifest_path": "/tmp/manifest.json",
                "published_modules": ["results"],
            }
        },
        "output_path": "/tmp/analysis/output.csv",
    }
    tree = PlanTree(id=1, title="Reference Context Plan")
    tree.nodes = {
        1: PlanNode(
            id=1,
            plan_id=1,
            name="Write section",
            status="pending",
            instruction="Draft the results section",
            dependencies=[2],
            context_combined="Use the validated analysis outputs only.",
            context_sections=[{"title": "Evidence", "content": "Output table saved at /tmp/analysis/output.csv"}],
            metadata={"paper_mode": True, "paper_context_paths": ["/tmp/paper/evidence.md"]},
        ),
        2: PlanNode(
            id=2,
            plan_id=1,
            name="Run analysis",
            status="completed",
            execution_result=json.dumps(dep_payload),
        ),
    }

    repo = _RepoWithContext(tree)
    executor = PlanExecutor(repo=repo, llm_service=SimpleNamespace(_llm=object()))
    result = executor.execute_task(
        1,
        1,
        config=ExecutionConfig(enable_skills=False),
    )

    assert result.status == "completed"
    task_context = captured["task_context"]
    assert task_context.context_summary == "Use the validated analysis outputs only."
    assert task_context.context_sections == [
        {"title": "Evidence", "content": "Output table saved at /tmp/analysis/output.csv"}
    ]
    assert "/tmp/paper/evidence.md" in task_context.paper_context_paths
    # output_path from dependency results is no longer auto-injected into
    # paper_context_paths; the executor now injects canonical artifact paths
    # from the manifest instead.
    assert "deliverable_submit" in (captured.get("available_tools") or [])
