"""Full-plan execution respects dependencies across the displayed tree."""

from unittest.mock import MagicMock

import pytest

from app.services.plans.plan_executor import ExecutionConfig, ExecutionResult, PlanExecutor
from app.services.plans.plan_models import PlanNode, PlanTree


@pytest.mark.parametrize("nested,max_tasks", [(False, None), (True, None), (False, 1)])
def test_full_plan_runs_producer_before_earlier_position_consumer(nested, max_tasks):
    if nested:
        nodes = [
            PlanNode(id=10, plan_id=1, name="Consumer branch", position=0),
            PlanNode(id=1, plan_id=1, name="Consumer", parent_id=10, dependencies=[2]),
            PlanNode(id=20, plan_id=1, name="Producer branch", position=1),
            PlanNode(id=2, plan_id=1, name="Producer", parent_id=20),
        ]
    else:
        nodes = [
            PlanNode(id=1, plan_id=1, name="Consumer", position=0, dependencies=[2]),
            PlanNode(id=2, plan_id=1, name="Producer", position=1),
        ]
    tree = PlanTree(id=1, title="Cross-branch dependencies", nodes={n.id: n for n in nodes})
    tree.rebuild_adjacency()
    repo = MagicMock()
    repo.get_plan_tree.return_value = tree
    executor = PlanExecutor(repo=repo, llm_service=MagicMock())
    executor._artifact_preflight = MagicMock()
    executor._artifact_preflight.validate_plan.return_value.has_errors.return_value = False
    executor._infer_missing_dependencies = lambda value: value
    executor._get_artifact_manifest = lambda *_args: {}
    executor._generate_plan_summary = lambda *_args: None
    executor._status_resolver = MagicMock()
    executor._status_resolver.resolve_plan_states.side_effect = lambda *_args, **_kwargs: {
        tid: {"effective_status": node.status} for tid, node in tree.nodes.items()
    }
    calls = []

    def run_task(plan_id, node, _tree, _config):
        calls.append(node.id)
        if any(tree.nodes[dep].status != "completed" for dep in node.dependencies):
            return ExecutionResult(plan_id=plan_id, task_id=node.id, status="skipped", content="Input not ready")
        node.status = "completed"
        return ExecutionResult(plan_id=plan_id, task_id=node.id, status="completed", content="Produced result")

    executor._run_task = run_task
    summary = executor.execute_plan(
        1, config=ExecutionConfig(enable_skills=False, auto_recovery=False, max_tasks=max_tasks),
    )

    if max_tasks is None:
        assert calls.index(2) < calls.index(1)
        assert set(calls) == set(tree.nodes)
    else:
        assert calls == [2]
    assert len(calls) == len(set(calls))
    assert summary.skipped_task_ids == []
    assert summary.failed_task_ids == []
    if nested:
        assert calls.index(1) < calls.index(10)
        assert calls.index(2) < calls.index(20)
