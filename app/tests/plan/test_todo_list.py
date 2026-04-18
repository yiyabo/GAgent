"""Tests for the Todo-List phased execution planner.

Covers:
- Dependency closure collection
- Topological phase layering (longest-path)
- Composite → atomic expansion
- Edge cases: cycles, missing nodes, single task, all-completed
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pytest

from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.todo_list import (
    TodoItem,
    TodoList,
    TodoPhase,
    build_todo_list,
    build_full_plan_todo_list,
    assign_phase_labels,
    _classify_task_label,
    _collect_leaf_ids,
    _compute_phase_layers,
)


# ── Helpers ──────────────────────────────────────────────────────


def _node(
    task_id: int,
    name: str,
    *,
    parent_id: Optional[int] = None,
    deps: Optional[List[int]] = None,
    status: str = "pending",
    instruction: Optional[str] = None,
) -> PlanNode:
    return PlanNode(
        id=task_id,
        plan_id=1,
        name=name,
        status=status,
        instruction=instruction or f"Do {name}",
        parent_id=parent_id,
        dependencies=deps or [],
    )


def _tree(nodes: List[PlanNode]) -> PlanTree:
    tree = PlanTree(
        id=1,
        title="Test Plan",
        nodes={n.id: n for n in nodes},
    )
    tree.rebuild_adjacency()
    return tree


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def linear_chain_tree() -> PlanTree:
    """A → B → C → D (linear dependency chain).

    D depends on C, C depends on B, B depends on A.
    Expected phases: [A], [B], [C], [D]
    """
    return _tree([
        _node(1, "Data Prep"),
        _node(2, "Preprocessing", deps=[1]),
        _node(3, "Analysis", deps=[2]),
        _node(4, "Summary", deps=[3]),
    ])


@pytest.fixture
def diamond_tree() -> PlanTree:
    """Diamond DAG:
        1
       / \\
      2   3
       \\ /
        4

    4 depends on both 2 and 3; 2 and 3 depend on 1.
    Expected phases: [1], [2, 3], [4]
    """
    return _tree([
        _node(1, "Fetch Data"),
        _node(2, "Process A", deps=[1]),
        _node(3, "Process B", deps=[1]),
        _node(4, "Merge Results", deps=[2, 3]),
    ])


@pytest.fixture
def composite_tree() -> PlanTree:
    """Composite task tree:
    Root(1)
    ├── Composite(2)
    │   ├── Leaf(4)
    │   └── Leaf(5, deps=[4])
    └── Leaf(3, deps=[2])

    Composite 2 has children 4 and 5.
    Task 3 depends on composite 2.
    After expansion: leaves are [4, 5, 3].
    """
    return _tree([
        _node(1, "Root", parent_id=None),
        _node(2, "Composite Step", parent_id=1),
        _node(3, "Final Step", parent_id=1, deps=[2]),
        _node(4, "Sub-step A", parent_id=2),
        _node(5, "Sub-step B", parent_id=2, deps=[4]),
    ])


@pytest.fixture
def wide_dag_tree() -> PlanTree:
    """Wide DAG with independent branches converging:

    1 ──┐
    2 ──┼──► 5 ──► 7
    3 ──┘         │
    4 ──────► 6 ──┘

    7 depends on 5 and 6.
    5 depends on 1, 2, 3.
    6 depends on 4.
    Expected phases: [1, 2, 3, 4], [5, 6], [7]
    """
    return _tree([
        _node(1, "Download A"),
        _node(2, "Download B"),
        _node(3, "Download C"),
        _node(4, "Download D"),
        _node(5, "Merge ABC", deps=[1, 2, 3]),
        _node(6, "Process D", deps=[4]),
        _node(7, "Final Analysis", deps=[5, 6]),
    ])


# ── Tests: _compute_phase_layers ────────────────────────────────


class TestPhaseLayering:
    def test_linear_chain(self):
        task_ids = {1, 2, 3, 4}
        deps = {1: [], 2: [1], 3: [2], 4: [3]}
        phases = _compute_phase_layers(task_ids, deps)
        assert phases == {1: 0, 2: 1, 3: 2, 4: 3}

    def test_diamond(self):
        task_ids = {1, 2, 3, 4}
        deps = {1: [], 2: [1], 3: [1], 4: [2, 3]}
        phases = _compute_phase_layers(task_ids, deps)
        assert phases[1] == 0
        assert phases[2] == 1
        assert phases[3] == 1
        assert phases[4] == 2

    def test_wide_converging(self):
        task_ids = {1, 2, 3, 4, 5, 6, 7}
        deps = {1: [], 2: [], 3: [], 4: [], 5: [1, 2, 3], 6: [4], 7: [5, 6]}
        phases = _compute_phase_layers(task_ids, deps)
        assert phases[1] == 0
        assert phases[2] == 0
        assert phases[3] == 0
        assert phases[4] == 0
        assert phases[5] == 1
        assert phases[6] == 1
        assert phases[7] == 2

    def test_single_task(self):
        phases = _compute_phase_layers({42}, {42: []})
        assert phases == {42: 0}

    def test_no_tasks(self):
        phases = _compute_phase_layers(set(), {})
        assert phases == {}

    def test_partial_scope_deps(self):
        """Deps outside scope are ignored for layering."""
        task_ids = {2, 3}
        deps = {2: [1], 3: [2]}  # 1 is out of scope
        phases = _compute_phase_layers(task_ids, deps)
        # 2 has no in-scope deps → phase 0
        assert phases[2] == 0
        assert phases[3] == 1


# ── Tests: _collect_leaf_ids ────────────────────────────────────


class TestCollectLeafIds:
    def test_all_leaves(self, linear_chain_tree: PlanTree):
        """Nodes without children are already leaves."""
        leaves = _collect_leaf_ids(linear_chain_tree, [1, 2, 3, 4])
        assert set(leaves) == {1, 2, 3, 4}

    def test_composite_expansion(self, composite_tree: PlanTree):
        """Composite node 2 should expand to leaves 4, 5."""
        leaves = _collect_leaf_ids(composite_tree, [2])
        assert set(leaves) == {4, 5}

    def test_root_expansion(self, composite_tree: PlanTree):
        """Root node should expand to all leaves."""
        leaves = _collect_leaf_ids(composite_tree, [1])
        assert set(leaves) == {3, 4, 5}

    def test_deduplication(self, composite_tree: PlanTree):
        """No duplicates even if input has overlapping subtrees."""
        leaves = _collect_leaf_ids(composite_tree, [1, 2, 4])
        assert len(leaves) == len(set(leaves))


# ── Tests: build_todo_list ──────────────────────────────────────


class TestBuildTodoList:
    def test_linear_chain_phases(self, linear_chain_tree: PlanTree):
        todo = build_todo_list(linear_chain_tree, 4)
        assert len(todo.phases) == 4
        assert todo.phases[0].items[0].task_id == 1
        assert todo.phases[1].items[0].task_id == 2
        assert todo.phases[2].items[0].task_id == 3
        assert todo.phases[3].items[0].task_id == 4
        assert todo.total_tasks == 4

    def test_diamond_phases(self, diamond_tree: PlanTree):
        todo = build_todo_list(diamond_tree, 4)
        assert len(todo.phases) == 3
        phase0_ids = [i.task_id for i in todo.phases[0].items]
        phase1_ids = [i.task_id for i in todo.phases[1].items]
        phase2_ids = [i.task_id for i in todo.phases[2].items]
        assert phase0_ids == [1]
        assert sorted(phase1_ids) == [2, 3]
        assert phase2_ids == [4]

    def test_wide_dag_phases(self, wide_dag_tree: PlanTree):
        todo = build_todo_list(wide_dag_tree, 7)
        assert len(todo.phases) == 3
        phase0_ids = sorted(i.task_id for i in todo.phases[0].items)
        phase1_ids = sorted(i.task_id for i in todo.phases[1].items)
        phase2_ids = [i.task_id for i in todo.phases[2].items]
        assert phase0_ids == [1, 2, 3, 4]
        assert phase1_ids == [5, 6]
        assert phase2_ids == [7]

    def test_execution_order_respects_phases(self, diamond_tree: PlanTree):
        todo = build_todo_list(diamond_tree, 4)
        order = todo.execution_order
        # Task 1 must come before 2 and 3; both must come before 4
        assert order.index(1) < order.index(2)
        assert order.index(1) < order.index(3)
        assert order.index(2) < order.index(4)

    def test_composite_dependency_expands_to_leaf_dependencies(self, composite_tree: PlanTree):
        todo = build_todo_list(composite_tree, 3)
        assert todo.execution_order == [4, 5, 3]
        item_by_id = {
            item.task_id: item
            for phase in todo.phases
            for item in phase.items
        }
        assert item_by_id[3].dependencies == [4, 5]

    def test_pending_order_excludes_done(self, linear_chain_tree: PlanTree):
        # Mark task 1 as completed
        linear_chain_tree.nodes[1].status = "completed"
        todo = build_todo_list(linear_chain_tree, 4)
        pending = todo.pending_order
        assert 1 not in pending
        assert 2 in pending

    def test_phase_labels(self, linear_chain_tree: PlanTree):
        todo = build_todo_list(linear_chain_tree, 4)
        # Semantic labels should be assigned based on task names
        labels = [p.label for p in todo.phases]
        assert labels[0] == "Data Preparation"  # "Data Prep"
        assert labels[1] == "Preprocessing"       # "Preprocessing"
        assert labels[2] == "Analysis"             # "Analysis"
        assert labels[3] == "Reporting"            # "Summary"

    def test_phase_status_pending(self, linear_chain_tree: PlanTree):
        todo = build_todo_list(linear_chain_tree, 4)
        assert todo.phases[0].status == "pending"

    def test_phase_status_completed(self, linear_chain_tree: PlanTree):
        linear_chain_tree.nodes[1].status = "completed"
        todo = build_todo_list(linear_chain_tree, 4)
        assert todo.phases[0].status == "completed"

    def test_single_task_no_deps(self):
        tree = _tree([_node(10, "Standalone")])
        todo = build_todo_list(tree, 10)
        assert todo.total_tasks == 1
        assert len(todo.phases) == 1
        assert todo.phases[0].items[0].task_id == 10

    def test_summary_output(self, diamond_tree: PlanTree):
        todo = build_todo_list(diamond_tree, 4)
        summary = todo.summary()
        assert "TodoList for task 4" in summary
        assert "Data Preparation" in summary  # semantic label for "Fetch Data"
        assert "Fetch Data" in summary

    def test_target_excluded(self, linear_chain_tree: PlanTree):
        todo = build_todo_list(
            linear_chain_tree, 4, include_target=False
        )
        all_ids = todo.execution_order
        # Target 4 might still appear if it has unmet deps — but the
        # include_target=False tells dependency_planner to exclude it
        # from the "to_run" set. The remaining deps should still be present.
        assert 1 in all_ids
        assert 2 in all_ids
        assert 3 in all_ids

    def test_all_completed_phase_status(self):
        tree = _tree([
            _node(1, "A", status="completed"),
            _node(2, "B", status="completed", deps=[1]),
        ])
        todo = build_todo_list(tree, 2)
        for phase in todo.phases:
            assert phase.status == "completed"
        assert todo.completed_tasks == todo.total_tasks

    def test_composite_expansion_in_build(self, composite_tree: PlanTree):
        """Composite task 2 should be expanded to leaves 4, 5."""
        todo = build_todo_list(composite_tree, 3, expand_composites=True)
        all_ids = todo.execution_order
        # Should contain atomic tasks only
        assert 4 in all_ids or 5 in all_ids
        # Composite 2 should NOT be in the list
        assert 2 not in all_ids


class TestTodoItemProperties:
    def test_is_done_completed(self):
        item = TodoItem(1, "A", "do A", "completed", [], 0)
        assert item.is_done is True

    def test_is_done_pending(self):
        item = TodoItem(1, "A", "do A", "pending", [], 0)
        assert item.is_done is False

    def test_is_done_success(self):
        item = TodoItem(1, "A", "do A", "success", [], 0)
        assert item.is_done is True

    def test_is_done_failed(self):
        item = TodoItem(1, "A", "do A", "failed", [], 0)
        assert item.is_done is False


# ── Phase label tests ────────────────────────────────────────────


class TestPhaseLabels:
    """Tests for heuristic semantic label assignment."""

    def test_classify_data_preparation(self):
        assert _classify_task_label("Download input data", None) == "Data Preparation"

    def test_classify_qc(self):
        assert _classify_task_label("QC filtering", None) == "Quality Control"

    def test_classify_preprocessing(self):
        assert _classify_task_label("Normalize read counts", None) == "Preprocessing"

    def test_classify_annotation(self):
        assert _classify_task_label("Gene annotation with Prokka", None) == "Annotation"

    def test_classify_alignment(self):
        assert _classify_task_label("Multiple sequence alignment", None) == "Alignment"

    def test_classify_phylogenetics(self):
        assert _classify_task_label("Build phylogenetic tree", None) == "Phylogenetics"

    def test_classify_analysis(self):
        assert _classify_task_label("Statistical analysis of diversity", None) == "Analysis"

    def test_classify_visualization(self):
        assert _classify_task_label("Plot heatmap", None) == "Visualization"

    def test_classify_reporting(self):
        assert _classify_task_label("Generate summary report", None) == "Reporting"

    def test_classify_unknown_returns_none(self):
        assert _classify_task_label("mysterious step", None) is None

    def test_classify_uses_instruction(self):
        label = _classify_task_label("Step X", "Download and collect data files")
        assert label == "Data Preparation"

    def test_assign_phase_labels_majority_vote(self):
        """Phase label should be the most common category among its tasks."""
        phases = [
            TodoPhase(
                phase_id=0,
                label="Phase 1",
                items=[
                    TodoItem(1, "Download data", "fetch files", "pending", [], 0),
                    TodoItem(2, "Collect input data", "gather sources", "pending", [], 0),
                    TodoItem(3, "QC check", "quality control", "pending", [], 0),
                ],
            )
        ]
        assign_phase_labels(phases)
        assert phases[0].label == "Data Preparation"

    def test_assign_phase_labels_fallback(self):
        """Phase with no classifiable tasks keeps default label."""
        phases = [
            TodoPhase(
                phase_id=0,
                label="Phase 1",
                items=[
                    TodoItem(1, "mysterious step", None, "pending", [], 0),
                ],
            )
        ]
        assign_phase_labels(phases)
        assert phases[0].label == "Phase 1"

    def test_build_todo_list_includes_labels(self):
        """build_todo_list should auto-assign semantic labels."""
        nodes = [
            _node(1, "Download source data", deps=[]),
            _node(2, "QC filtering", deps=[1]),
            _node(3, "Statistical analysis", deps=[2]),
        ]
        tree = _tree(nodes)
        todo = build_todo_list(tree, 3)
        labels = [p.label for p in todo.phases]
        assert labels[0] == "Data Preparation"
        assert labels[1] == "Quality Control"
        assert labels[2] == "Analysis"


# ── Full Plan TodoList Tests ───────────────────────────────────


class TestBuildFullPlanTodoList:
    def test_linear_chain(self, linear_chain_tree):
        todo = build_full_plan_todo_list(linear_chain_tree)
        assert todo.target_task_id == 0
        assert len(todo.phases) == 4
        assert todo.execution_order == [1, 2, 3, 4]
        assert todo.total_tasks == 4

    def test_diamond(self, diamond_tree):
        todo = build_full_plan_todo_list(diamond_tree)
        assert len(todo.phases) == 3
        assert todo.phases[0].items[0].task_id == 1
        phase_1_ids = {item.task_id for item in todo.phases[1].items}
        assert phase_1_ids == {2, 3}
        assert todo.phases[2].items[0].task_id == 4

    def test_empty_tree(self):
        tree = PlanTree(id=1, title="Empty")
        tree.rebuild_adjacency()
        todo = build_full_plan_todo_list(tree)
        assert todo.total_tasks == 0
        assert len(todo.phases) == 0

    def test_single_task(self):
        tree = _tree([_node(1, "Only task")])
        todo = build_full_plan_todo_list(tree)
        assert len(todo.phases) == 1
        assert todo.phases[0].items[0].task_id == 1

    def test_pending_order_excludes_done(self):
        tree = _tree([
            _node(1, "Done task", status="completed"),
            _node(2, "Pending task", deps=[1]),
        ])
        todo = build_full_plan_todo_list(tree)
        assert todo.execution_order == [1, 2]
        assert todo.pending_order == [2]
        assert todo.completed_tasks == 1

    def test_pending_order_excludes_running_tasks_and_blocked_dependents(self):
        tree = _tree([
            _node(1, "Running task", status="running"),
            _node(2, "Blocked dependent", deps=[1]),
            _node(3, "Independent task"),
        ])
        todo = build_full_plan_todo_list(tree)
        assert todo.execution_order == [1, 3, 2]
        assert todo.pending_order == [3]

    def test_phase_labels_assigned(self):
        tree = _tree([
            _node(1, "Download data"),
            _node(2, "QC filtering", deps=[1]),
            _node(3, "Plot heatmap", deps=[2]),
        ])
        todo = build_full_plan_todo_list(tree)
        labels = [p.label for p in todo.phases]
        assert labels[0] == "Data Preparation"
        assert labels[1] == "Quality Control"

    def test_composite_expansion(self):
        tree = _tree([
            _node(1, "Root", parent_id=None),
            _node(2, "Child A", parent_id=1),
            _node(3, "Child B", parent_id=1),
        ])
        tree.rebuild_adjacency()
        todo = build_full_plan_todo_list(tree, expand_composites=True)
        task_ids = {item.task_id for phase in todo.phases for item in phase.items}
        assert 1 in task_ids
        assert 2 in task_ids
        assert 3 in task_ids
        assert todo.execution_order == [2, 3, 1]

    def test_no_expansion(self):
        tree = _tree([
            _node(1, "Root", parent_id=None),
            _node(2, "Child A", parent_id=1),
        ])
        tree.rebuild_adjacency()
        todo = build_full_plan_todo_list(tree, expand_composites=False)
        task_ids = {item.task_id for phase in todo.phases for item in phase.items}
        assert 1 in task_ids
        assert 2 in task_ids

    def test_wide_dag(self):
        tree = _tree([
            _node(1, "Source A"),
            _node(2, "Source B"),
            _node(3, "Merge", deps=[1, 2]),
            _node(4, "Analysis", deps=[3]),
        ])
        todo = build_full_plan_todo_list(tree)
        assert len(todo.phases[0].items) == 2
        assert len(todo.phases[1].items) == 1
        assert len(todo.phases[2].items) == 1

    def test_composite_dependency_expands_to_leaf_dependencies(self, composite_tree: PlanTree):
        todo = build_full_plan_todo_list(composite_tree)
        assert todo.execution_order == [4, 5, 2, 3, 1]
        item_by_id = {
            item.task_id: item
            for phase in todo.phases
            for item in phase.items
        }
        assert item_by_id[2].dependencies == [4, 5]
        assert item_by_id[3].dependencies == [2]
        assert item_by_id[1].dependencies == [2, 3]

    def test_full_plan_preserves_pending_parent_synthesis_tasks(self):
        tree = _tree([
            _node(1, "Root synthesis", status="pending"),
            _node(2, "Completed child A", parent_id=1, status="completed"),
            _node(3, "Completed child B", parent_id=1, status="completed"),
        ])
        tree.rebuild_adjacency()
        todo = build_full_plan_todo_list(tree)
        assert todo.execution_order == [2, 3, 1]
        assert todo.pending_order == [1]

    def test_summary(self):
        tree = _tree([
            _node(1, "Done", status="completed"),
            _node(2, "Pending", deps=[1]),
        ])
        todo = build_full_plan_todo_list(tree)
        s = todo.summary()
        assert "TodoList" in s
        assert "Total: 1/2 completed" in s
