from app.routers.plan_routes import _build_execution_dependency_plan, _to_dependency_plan_response
from app.services.plans.plan_models import PlanNode, PlanTree


def _build_tree() -> PlanTree:
    tree = PlanTree(id=10, title="Execution Order Test")
    tree.nodes = {
        1: PlanNode(id=1, plan_id=10, name="Root", parent_id=None, status="pending"),
        2: PlanNode(id=2, plan_id=10, name="Child A", parent_id=1, status="pending"),
        3: PlanNode(id=3, plan_id=10, name="Child B", parent_id=1, status="pending"),
        4: PlanNode(id=4, plan_id=10, name="Grandchild A1", parent_id=2, status="pending"),
        9: PlanNode(id=9, plan_id=10, name="External dependency", parent_id=None, status="pending"),
    }
    tree.nodes[2].dependencies = [9]
    tree.rebuild_adjacency()
    return tree


def test_execution_plan_includes_subtree_by_default() -> None:
    tree = _build_tree()

    plan = _build_execution_dependency_plan(
        tree,
        target_task_id=1,
        include_dependencies=False,
        include_subtasks=True,
    )

    order = plan.execution_order
    assert set(order) == {1, 2, 3, 4}
    assert order.index(4) < order.index(2)
    assert order.index(2) < order.index(1)
    assert order.index(3) < order.index(1)


def test_execution_plan_adds_unmet_dependencies_for_subtasks() -> None:
    tree = _build_tree()

    plan = _build_execution_dependency_plan(
        tree,
        target_task_id=1,
        include_dependencies=True,
        include_subtasks=True,
    )

    order = plan.execution_order
    assert set(order) == {1, 2, 3, 4, 9}
    assert order.index(9) < order.index(2)
    assert 9 in plan.missing_dependencies


def test_execution_plan_keeps_target_only_when_subtasks_disabled() -> None:
    tree = _build_tree()

    plan = _build_execution_dependency_plan(
        tree,
        target_task_id=1,
        include_dependencies=False,
        include_subtasks=False,
    )

    assert plan.execution_order == [1]


def test_dependency_plan_response_includes_execution_items_and_outputs() -> None:
    tree = _build_tree()
    tree.nodes[2].instruction = "Write `results/child_a.csv` for downstream analysis."
    tree.nodes[3].instruction = "Write `results/child_b.md` for downstream analysis."

    plan = _build_execution_dependency_plan(
        tree,
        target_task_id=1,
        include_dependencies=False,
        include_subtasks=True,
    )
    response = _to_dependency_plan_response(tree, plan)

    assert response.execution_items
    child_a = next(item for item in response.execution_items if item.task_id == 2)
    root = next(item for item in response.execution_items if item.task_id == 1)
    assert "results/child_a.csv" in child_a.expected_deliverables
    assert child_a.execution_state == "blocked"
    assert set(root.depends_on) == {2, 3}
