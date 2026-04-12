"""Tests for ExecutionConfig autonomous mode and smart throttle."""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

@dataclass
class _Node:
    id: int
    name: str = ""
    status: str = "pending"
    parent_id: Optional[int] = None
    instruction: Optional[str] = None
    dependencies: List[int] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_result: Optional[str] = None

    def display_name(self):
        return self.name or f"Task {self.id}"


class _Tree:
    def __init__(self, nodes: List[_Node]):
        self.nodes = {n.id: n for n in nodes}
        self.metadata = {}

    def has_node(self, task_id: int) -> bool:
        return task_id in self.nodes

    def get_node(self, task_id: int) -> _Node:
        return self.nodes[task_id]

    def children_ids(self, task_id: int) -> list:
        return [n.id for n in self.nodes.values() if n.parent_id == task_id]


class _RepoStub:
    def __init__(self, tree: _Tree):
        self._tree = tree
        self.updates: List[dict] = []

    def get_plan_tree(self, plan_id: int) -> _Tree:
        return self._tree

    def update_task(self, plan_id, task_id, **kwargs):
        self.updates.append({"plan_id": plan_id, "task_id": task_id, **kwargs})

    def update_plan_metadata(self, plan_id, metadata):
        pass


# ---------------------------------------------------------------------------
# Tests — ExecutionConfig autonomous mode
# ---------------------------------------------------------------------------


class TestExecutionConfigAutonomous:
    def test_autonomous_sets_auto_recovery_and_disables_throttle(self):
        from app.services.plans.plan_executor import ExecutionConfig

        cfg = ExecutionConfig(autonomous=True)
        assert cfg.auto_recovery is True
        assert cfg.dependency_throttle is False

    def test_autonomous_false_preserves_defaults(self):
        from app.services.plans.plan_executor import ExecutionConfig

        cfg = ExecutionConfig(autonomous=False)
        assert cfg.auto_recovery is False
        assert cfg.dependency_throttle is True

    def test_autonomous_overrides_explicit_throttle(self):
        from app.services.plans.plan_executor import ExecutionConfig

        cfg = ExecutionConfig(autonomous=True, dependency_throttle=True)
        # autonomous=True should override dependency_throttle to False
        assert cfg.dependency_throttle is False


# ---------------------------------------------------------------------------
# Tests — Smart throttle (transitive dep skip)
# ---------------------------------------------------------------------------


class TestSmartThrottle:
    """Test that when dependency_throttle=False, failed tasks don't break
    execution but their dependents are skipped."""

    def _make_executor(self, tree, run_results):
        """Create a minimal PlanExecutor with mocked _run_task."""
        from app.services.plans.plan_executor import PlanExecutor, ExecutionConfig, ExecutionResult

        repo = _RepoStub(tree)
        executor = PlanExecutor.__new__(PlanExecutor)
        executor._repo = repo
        executor._settings = None

        call_idx = {"i": 0}

        def _run_task(plan_id, node, tree_arg, cfg):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(run_results):
                status, content = run_results[idx]
            else:
                status, content = "completed", "ok"
            return ExecutionResult(
                plan_id=plan_id,
                task_id=node.id,
                status=status,
                content=content,
            )

        ordered_nodes = list(tree.nodes.values())

        executor._run_task = _run_task
        executor._execution_order = lambda t: ordered_nodes
        executor._preselect_skills_for_plan = lambda *a, **k: None
        executor._generate_plan_summary = lambda *a, **k: None
        return executor

    def test_throttle_true_breaks_on_failure(self):
        from app.services.plans.plan_executor import ExecutionConfig

        nodes = [
            _Node(id=1, name="A"),
            _Node(id=2, name="B"),
            _Node(id=3, name="C"),
        ]
        tree = _Tree(nodes)
        run_results = [
            ("completed", "ok"),
            ("failed", "error"),
            ("completed", "ok"),
        ]
        executor = self._make_executor(tree, run_results)
        cfg = ExecutionConfig(dependency_throttle=True, enable_skills=False)

        summary = executor.execute_plan(plan_id=1, config=cfg)
        assert 2 in summary.failed_task_ids
        # With throttle=True, execution breaks — task 3 never runs
        assert 3 not in summary.executed_task_ids
        assert 3 not in summary.failed_task_ids
        assert 3 not in summary.skipped_task_ids

    def test_throttle_false_continues_independent_tasks(self):
        from app.services.plans.plan_executor import ExecutionConfig

        nodes = [
            _Node(id=1, name="A"),
            _Node(id=2, name="B"),
            _Node(id=3, name="C"),  # no deps on B
        ]
        tree = _Tree(nodes)
        run_results = [
            ("completed", "ok"),
            ("failed", "error"),
            ("completed", "ok"),
        ]
        executor = self._make_executor(tree, run_results)
        cfg = ExecutionConfig(dependency_throttle=False, enable_skills=False)

        summary = executor.execute_plan(plan_id=1, config=cfg)
        assert 1 in summary.executed_task_ids
        assert 2 in summary.failed_task_ids
        # Task 3 has no deps on 2, should still execute
        assert 3 in summary.executed_task_ids

    def test_transitive_dep_skip(self):
        """Task 3 depends on task 2 which fails → task 3 should be skipped."""
        from app.services.plans.plan_executor import ExecutionConfig

        nodes = [
            _Node(id=1, name="A"),
            _Node(id=2, name="B"),
            _Node(id=3, name="C", dependencies=[2]),  # depends on B
        ]
        tree = _Tree(nodes)
        run_results = [
            ("completed", "ok"),
            ("failed", "error"),
            # task 3 should not run
        ]
        executor = self._make_executor(tree, run_results)
        cfg = ExecutionConfig(dependency_throttle=False, enable_skills=False)

        summary = executor.execute_plan(plan_id=1, config=cfg)
        assert 1 in summary.executed_task_ids
        assert 2 in summary.failed_task_ids
        assert 3 in summary.skipped_task_ids
        # Check skip reason
        skip_result = next(r for r in summary.results if r.task_id == 3)
        assert "upstream" in skip_result.content.lower()

    def test_deep_transitive_skip(self):
        """A→B→C chain: A fails → B,C both skipped."""
        from app.services.plans.plan_executor import ExecutionConfig

        nodes = [
            _Node(id=1, name="A"),
            _Node(id=2, name="B", dependencies=[1]),
            _Node(id=3, name="C", dependencies=[2]),
            _Node(id=4, name="D"),  # independent
        ]
        tree = _Tree(nodes)
        run_results = [
            ("failed", "error"),
            # 2, 3 skipped
            ("completed", "ok"),  # 4 runs
        ]
        executor = self._make_executor(tree, run_results)
        cfg = ExecutionConfig(dependency_throttle=False, enable_skills=False)

        summary = executor.execute_plan(plan_id=1, config=cfg)
        assert 1 in summary.failed_task_ids
        assert 2 in summary.skipped_task_ids
        assert 3 in summary.skipped_task_ids
        assert 4 in summary.executed_task_ids

    def test_autonomous_enables_smart_throttle(self):
        """autonomous=True → dependency_throttle=False → continues after failure."""
        from app.services.plans.plan_executor import ExecutionConfig

        nodes = [
            _Node(id=1, name="A"),
            _Node(id=2, name="B"),
            _Node(id=3, name="C"),
        ]
        tree = _Tree(nodes)
        run_results = [
            ("failed", "error"),
            ("completed", "ok"),
            ("completed", "ok"),
        ]
        executor = self._make_executor(tree, run_results)
        cfg = ExecutionConfig(autonomous=True, enable_skills=False)

        summary = executor.execute_plan(plan_id=1, config=cfg)
        assert 1 in summary.failed_task_ids
        assert 2 in summary.executed_task_ids
        assert 3 in summary.executed_task_ids


# ---------------------------------------------------------------------------
# Tests — Result context expansion
# ---------------------------------------------------------------------------


class TestResultContextExpansion:
    def test_dep_context_max_length_is_4000(self):
        """Verify the dep context summarization uses 4000 chars."""
        import app.services.plans.plan_executor as mod
        import inspect

        source = inspect.getsource(mod)
        # The old value was 1200, new should be 4000
        assert "max_length=4000" in source
        assert "max_length=1200" not in source


# ---------------------------------------------------------------------------
# Tests — Artifact verification
# ---------------------------------------------------------------------------


class TestArtifactVerification:
    def test_missing_artifact_downgrades_status(self, tmp_path):
        """If artifact_paths point to missing absolute paths, success → failed."""
        import os

        missing_path = str(tmp_path / "nonexistent.csv")

        artifact_paths = [missing_path]
        success = True
        missing_artifacts = []
        empty_artifacts = []
        for ap in artifact_paths:
            if not os.path.isabs(ap):
                continue
            if not os.path.exists(ap):
                missing_artifacts.append(ap)
            elif os.path.isfile(ap) and os.path.getsize(ap) == 0:
                empty_artifacts.append(ap)
        if missing_artifacts or empty_artifacts:
            success = False

        assert success is False
        assert len(missing_artifacts) == 1

    def test_empty_artifact_downgrades_status(self, tmp_path):
        """If artifact_paths point to empty files, success → failed."""
        import os

        empty_file = tmp_path / "empty.csv"
        empty_file.write_text("")

        artifact_paths = [str(empty_file)]
        success = True
        missing_artifacts = []
        empty_artifacts = []
        for ap in artifact_paths:
            if not os.path.isabs(ap):
                continue
            if not os.path.exists(ap):
                missing_artifacts.append(ap)
            elif os.path.isfile(ap) and os.path.getsize(ap) == 0:
                empty_artifacts.append(ap)
        if missing_artifacts or empty_artifacts:
            success = False

        assert success is False
        assert len(empty_artifacts) == 1

    def test_valid_artifact_keeps_status(self, tmp_path):
        """If artifact_paths point to valid files, success stays True."""
        import os

        valid_file = tmp_path / "results.csv"
        valid_file.write_text("col1,col2\n1,2\n")

        artifact_paths = [str(valid_file)]
        success = True
        missing_artifacts = []
        empty_artifacts = []
        for ap in artifact_paths:
            if not os.path.isabs(ap):
                continue
            if not os.path.exists(ap):
                missing_artifacts.append(ap)
            elif os.path.isfile(ap) and os.path.getsize(ap) == 0:
                empty_artifacts.append(ap)
        if missing_artifacts or empty_artifacts:
            success = False

        assert success is True

    def test_relative_paths_skipped(self):
        """Relative paths should be skipped, not trigger false failure."""
        import os

        artifact_paths = ["results/output.csv", "data/input.txt"]
        success = True
        missing_artifacts = []
        empty_artifacts = []
        for ap in artifact_paths:
            if not os.path.isabs(ap):
                continue
            if not os.path.exists(ap):
                missing_artifacts.append(ap)
        if missing_artifacts or empty_artifacts:
            success = False

        assert success is True
        assert len(missing_artifacts) == 0
