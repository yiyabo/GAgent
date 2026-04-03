"""Tests for the 5-fix plan executor recovery overhaul.

Covers:
1. skipped + blocked_by_dependencies triggers auto-recovery
2. Recovery success → summary.results reflects completed (not failed/skipped)
3. FailureAnalyzer.classify() uses structured metadata first
4. qwen_code prompt does NOT authorize upstream overwrite
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
from app.services.plans.failure_recovery import (
    FailureAnalyzer,
    FailureCategory,
    RECOVERABLE,
)


# ===================================================================
# Test Group 1: FailureAnalyzer structured classification
# ===================================================================


class TestFailureAnalyzerStructured:
    """classify() should prefer structured metadata over text matching."""

    def test_blocked_by_dependencies_flag(self):
        """blocked_by_dependencies=True → UPSTREAM_INCOMPLETE regardless of text."""
        cat = FailureAnalyzer().classify(
            "Everything looks fine, no errors at all.",
            {},
            result_status="skipped",
            result_metadata={"blocked_by_dependencies": True, "incomplete_dependencies": [3, 4]},
        )
        assert cat == FailureCategory.UPSTREAM_INCOMPLETE

    def test_incomplete_dependencies_list(self):
        """Non-empty incomplete_dependencies → UPSTREAM_INCOMPLETE."""
        cat = FailureAnalyzer().classify(
            "",
            {},
            result_metadata={"incomplete_dependencies": [5]},
        )
        assert cat == FailureCategory.UPSTREAM_INCOMPLETE

    def test_error_category_blocked_dependency(self):
        """error_category='blocked_dependency' → UPSTREAM_INCOMPLETE."""
        cat = FailureAnalyzer().classify(
            "",
            {},
            result_metadata={"error_category": "blocked_dependency"},
        )
        assert cat == FailureCategory.UPSTREAM_INCOMPLETE

    def test_error_category_timeout(self):
        """error_category='timeout' → TIMEOUT."""
        cat = FailureAnalyzer().classify(
            "",
            {},
            result_metadata={"error_category": "timeout"},
        )
        assert cat == FailureCategory.TIMEOUT

    def test_error_category_data_missing(self):
        """error_category='data_missing' → DATA_MISSING."""
        cat = FailureAnalyzer().classify(
            "",
            {},
            result_metadata={"error_category": "data_missing"},
        )
        assert cat == FailureCategory.DATA_MISSING

    def test_skipped_with_enforce_dependencies(self):
        """status=skipped + enforce_dependencies → UPSTREAM_INCOMPLETE."""
        cat = FailureAnalyzer().classify(
            "some random text",
            {},
            result_status="skipped",
            result_metadata={"enforce_dependencies": True},
        )
        assert cat == FailureCategory.UPSTREAM_INCOMPLETE

    def test_runtime_failure_flag(self):
        """runtime_failure=True → CODE_BUG."""
        cat = FailureAnalyzer().classify(
            "",
            {},
            result_metadata={"runtime_failure": True},
        )
        assert cat == FailureCategory.CODE_BUG

    def test_text_fallback_traceback(self):
        """Text fallback still works for traceback."""
        cat = FailureAnalyzer().classify(
            "Traceback (most recent call last):\n  File ...",
            {},
        )
        assert cat == FailureCategory.CODE_BUG

    def test_text_fallback_filenotfound(self):
        """Text fallback still works for FileNotFoundError."""
        cat = FailureAnalyzer().classify(
            "FileNotFoundError: [Errno 2] No such file: '/tmp/x.csv'",
            {},
        )
        assert cat == FailureCategory.DATA_MISSING

    def test_structured_beats_text(self):
        """Structured metadata takes priority over contradicting text."""
        # Text says "traceback" (CODE_BUG), but metadata says blocked_by_deps
        cat = FailureAnalyzer().classify(
            "Traceback (most recent call last):\n  KeyError: 'x'",
            {},
            result_metadata={"blocked_by_dependencies": True},
        )
        assert cat == FailureCategory.UPSTREAM_INCOMPLETE

    def test_backward_compatible_metadata_param(self):
        """Old-style metadata dict still works."""
        cat = FailureAnalyzer().classify(
            "",
            {"error_category": "blocked_dependency"},
        )
        assert cat == FailureCategory.UPSTREAM_INCOMPLETE

    def test_empty_result_unrecoverable(self):
        """Empty result with no metadata → UNRECOVERABLE."""
        cat = FailureAnalyzer().classify("", {})
        assert cat == FailureCategory.UNRECOVERABLE

    def test_upstream_incomplete_is_recoverable(self):
        """UPSTREAM_INCOMPLETE must be in RECOVERABLE set."""
        assert FailureCategory.UPSTREAM_INCOMPLETE in RECOVERABLE


# ===================================================================
# Test Group 2: Skipped recovery & summary consistency
# ===================================================================


@dataclass
class _FakePlanNode:
    id: int
    parent_id: Optional[int] = None
    plan_id: int = 1
    status: Optional[str] = "pending"
    instruction: Optional[str] = "test task"
    execution_result: Optional[str] = None
    dependencies: List[int] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    context_combined: Optional[str] = None
    context_sections: Optional[list] = None
    path: str = "/"
    name: Optional[str] = None

    def display_name(self) -> str:
        return self.name or f"Task {self.id}"


@dataclass
class _FakePlanTree:
    id: int = 1
    title: str = "Test Plan"
    description: Optional[str] = None
    metadata: Optional[dict] = None
    nodes: Dict[int, Any] = field(default_factory=dict)

    def get_node(self, node_id: int):
        return self.nodes[node_id]

    def has_node(self, node_id: int) -> bool:
        return node_id in self.nodes

    def root_node_ids(self) -> List[int]:
        parent_ids = {n.parent_id for n in self.nodes.values() if n.parent_id is not None}
        return [nid for nid in self.nodes if nid not in parent_ids and self.nodes[nid].parent_id is None]

    def children_ids(self, parent_id: int) -> List[int]:
        return [nid for nid, n in self.nodes.items() if n.parent_id == parent_id]

    def to_outline(self, **_) -> str:
        return "outline"


class TestSkippedRecoveryAndSummary:
    """Verify that skipped tasks with blocked_by_dependencies can auto-recover."""

    def _make_executor_with_mock_run_task(self, run_task_side_effects):
        """Create a PlanExecutor with _run_task mocked to return predefined results."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
            PlanExecutor,
        )

        call_counter = {"n": 0}
        effects = list(run_task_side_effects)

        def mock_run_task(plan_id, node, tree, config):
            idx = call_counter["n"]
            call_counter["n"] += 1
            if idx < len(effects):
                return effects[idx]
            return ExecutionResult(
                plan_id=plan_id,
                task_id=node.id,
                status="completed",
                content="fallback",
            )

        executor = PlanExecutor.__new__(PlanExecutor)
        executor._repo = MagicMock()
        executor._llm = MagicMock()
        executor._prompt_builder = MagicMock()
        executor._deliverable_publisher = MagicMock()
        executor._tool_executor = MagicMock()
        executor._task_verifier = MagicMock()
        executor._run_task = mock_run_task
        executor._test_call_counter = call_counter
        return executor

    def test_skipped_blocked_triggers_recovery(self):
        """A skipped task with blocked_by_dependencies should trigger recovery."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
        )

        # Task 1 (dep) is pending, Task 2 depends on Task 1.
        dep_node = _FakePlanNode(id=1, status="pending")
        task_node = _FakePlanNode(id=2, status="pending", dependencies=[1])

        tree = _FakePlanTree(nodes={1: dep_node, 2: task_node})

        # First call: task 2 returns skipped (blocked by deps)
        skipped_result = ExecutionResult(
            plan_id=1, task_id=2, status="skipped",
            content="Blocked by dependencies",
            metadata={
                "blocked_by_dependencies": True,
                "incomplete_dependencies": [1],
                "enforce_dependencies": True,
            },
        )
        # Second call: recovery re-runs dep (task 1) → completed
        dep_completed = ExecutionResult(
            plan_id=1, task_id=1, status="completed",
            content="Dep done",
        )
        # Third call: retry task 2 → completed
        task_completed = ExecutionResult(
            plan_id=1, task_id=2, status="completed",
            content="Task done after recovery",
        )

        executor = self._make_executor_with_mock_run_task([
            skipped_result,  # initial run of task 2
            dep_completed,   # recovery: re-run dep 1
            task_completed,  # recovery: retry task 2
        ])

        # Mock _execution_order to return only task 2 (dep 1 handled by recovery)
        executor._execution_order = lambda tree: [task_node]
        executor._preselect_skills_for_plan = lambda *a, **kw: None
        executor._repo.get_plan_tree = MagicMock(return_value=tree)

        cfg = ExecutionConfig(
            auto_recovery=True,
            max_recovery_attempts=2,
            dependency_throttle=True,
        )

        summary = executor.execute_plan(1, config=cfg)

        # The final result in summary should include both the recovered
        # dependency and the completed task.
        assert len(summary.results) == 2
        assert [result.task_id for result in summary.results] == [1, 2]
        final_result = summary.results[-1]
        assert final_result.status == "completed"
        assert final_result.content == "Task done after recovery"

        # Task 2 should be in executed, not in skipped or failed
        assert 2 in summary.executed_task_ids
        assert 2 not in summary.skipped_task_ids
        assert 2 not in summary.failed_task_ids

    def test_failed_recovery_summary_consistency(self):
        """A failed task that recovers should appear as completed in summary."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
        )

        task_node = _FakePlanNode(id=1, status="pending")
        tree = _FakePlanTree(nodes={1: task_node})

        failed_result = ExecutionResult(
            plan_id=1, task_id=1, status="failed",
            content="KeyError: 'x'",
            metadata={"runtime_failure": True},
        )
        retry_completed = ExecutionResult(
            plan_id=1, task_id=1, status="completed",
            content="Fixed and done",
        )

        executor = self._make_executor_with_mock_run_task([
            failed_result,
            retry_completed,
        ])
        executor._execution_order = lambda tree: [task_node]
        executor._preselect_skills_for_plan = lambda *a, **kw: None
        executor._repo.get_plan_tree = MagicMock(return_value=tree)

        cfg = ExecutionConfig(
            auto_recovery=True,
            max_recovery_attempts=2,
        )

        summary = executor.execute_plan(1, config=cfg)

        assert len(summary.results) == 1
        assert summary.results[0].status == "completed"
        assert 1 in summary.executed_task_ids
        assert 1 not in summary.failed_task_ids

    def test_completed_dependency_can_be_rerun_when_outputs_are_missing(self):
        """Blocked downstream retry may need to rerun a dependency already marked completed."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
        )

        dep_node = _FakePlanNode(id=1, status="completed")
        task_node = _FakePlanNode(id=2, status="pending", dependencies=[1])
        tree = _FakePlanTree(nodes={1: dep_node, 2: task_node})

        initial_failed = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="failed",
            content="BLOCKED_DEPENDENCY: filtered_cancer2.h5ad missing",
            metadata={"error_category": "blocked_dependency"},
        )
        dep_rerun_completed = ExecutionResult(
            plan_id=1,
            task_id=1,
            status="completed",
            content="Dependency regenerated outputs",
        )
        retry_completed = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="completed",
            content="Downstream task succeeded",
        )

        executor = self._make_executor_with_mock_run_task([
            initial_failed,
            dep_rerun_completed,
            retry_completed,
        ])
        executor._execution_order = lambda tree: [task_node]
        executor._preselect_skills_for_plan = lambda *a, **kw: None
        executor._repo.get_plan_tree = MagicMock(return_value=tree)

        summary = executor.execute_plan(
            1,
            config=ExecutionConfig(auto_recovery=True, max_recovery_attempts=2),
        )

        assert [result.task_id for result in summary.results] == [1, 2]
        assert summary.results[0].content == "Dependency regenerated outputs"
        assert summary.results[1].status == "completed"
        assert summary.executed_task_ids.count(1) == 1
        assert summary.executed_task_ids.count(2) == 1

    def test_retry_failure_replaces_original_failure_reason(self):
        """If recovery retry still fails, summary should reflect the latest failure."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
        )

        dep_node = _FakePlanNode(id=1, status="completed")
        task_node = _FakePlanNode(id=2, status="pending", dependencies=[1])
        tree = _FakePlanTree(nodes={1: dep_node, 2: task_node})

        initial_failed = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="failed",
            content="BLOCKED_DEPENDENCY: upstream output missing",
            metadata={"error_category": "blocked_dependency"},
        )
        dep_rerun_completed = ExecutionResult(
            plan_id=1,
            task_id=1,
            status="completed",
            content="Dependency rerun completed",
        )
        retry_failed = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="failed",
            content="KeyError: 'sample_id'",
            metadata={"runtime_failure": True},
        )

        executor = self._make_executor_with_mock_run_task([
            initial_failed,
            dep_rerun_completed,
            retry_failed,
        ])
        executor._execution_order = lambda tree: [task_node]
        executor._preselect_skills_for_plan = lambda *a, **kw: None
        executor._repo.get_plan_tree = MagicMock(return_value=tree)

        summary = executor.execute_plan(
            1,
            config=ExecutionConfig(
                auto_recovery=True,
                max_recovery_attempts=2,
                dependency_throttle=False,
            ),
        )

        assert len(summary.results) == 2
        final_task_result = [result for result in summary.results if result.task_id == 2][0]
        assert final_task_result.content == "KeyError: 'sample_id'"
        assert final_task_result.status == "failed"
        assert 2 in summary.failed_task_ids

    def test_recovered_dependency_is_included_in_summary(self):
        """Dependency reruns during recovery should be visible in plan summary inputs."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
        )

        dep_node = _FakePlanNode(id=1, status="pending")
        task_node = _FakePlanNode(id=2, status="pending", dependencies=[1])
        tree = _FakePlanTree(nodes={1: dep_node, 2: task_node})

        skipped_result = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="skipped",
            content="Blocked by dependencies",
            metadata={
                "blocked_by_dependencies": True,
                "incomplete_dependencies": [1],
                "enforce_dependencies": True,
            },
        )
        dep_completed = ExecutionResult(
            plan_id=1,
            task_id=1,
            status="completed",
            content="Dependency completed in recovery",
        )
        task_completed = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="completed",
            content="Task completed after dependency rerun",
        )

        executor = self._make_executor_with_mock_run_task([
            skipped_result,
            dep_completed,
            task_completed,
        ])
        executor._execution_order = lambda tree: [task_node]
        executor._preselect_skills_for_plan = lambda *a, **kw: None
        executor._repo.get_plan_tree = MagicMock(return_value=tree)

        summary = executor.execute_plan(
            1,
            config=ExecutionConfig(auto_recovery=True, max_recovery_attempts=2),
        )

        assert [result.task_id for result in summary.results] == [1, 2]
        assert summary.results[0].content == "Dependency completed in recovery"

    def test_dependency_recovery_failure_stops_current_task_retry(self):
        """If a dependency rerun fails, the current task should not be retried."""
        from app.services.plans.plan_executor import (
            ExecutionConfig,
            ExecutionResult,
        )

        dep_node = _FakePlanNode(id=1, status="pending")
        task_node = _FakePlanNode(id=2, status="pending", dependencies=[1])
        tree = _FakePlanTree(nodes={1: dep_node, 2: task_node})

        initial_skipped = ExecutionResult(
            plan_id=1,
            task_id=2,
            status="skipped",
            content="Blocked by dependencies",
            metadata={
                "blocked_by_dependencies": True,
                "incomplete_dependencies": [1],
                "enforce_dependencies": True,
            },
        )
        dep_failed = ExecutionResult(
            plan_id=1,
            task_id=1,
            status="failed",
            content="Dependency rerun failed with FileNotFoundError",
            metadata={"error_category": "data_missing"},
        )

        executor = self._make_executor_with_mock_run_task([
            initial_skipped,
            dep_failed,
        ])
        executor._execution_order = lambda tree: [task_node]
        executor._preselect_skills_for_plan = lambda *a, **kw: None
        executor._repo.get_plan_tree = MagicMock(return_value=tree)

        summary = executor.execute_plan(
            1,
            config=ExecutionConfig(
                auto_recovery=True,
                max_recovery_attempts=2,
                dependency_throttle=False,
            ),
        )

        assert executor._test_call_counter["n"] == 2
        assert [result.task_id for result in summary.results] == [1, 2]
        final_task_result = [result for result in summary.results if result.task_id == 2][0]
        assert final_task_result.status == "skipped"
        assert final_task_result.metadata["dependency_recovery_failed"] is True
        assert final_task_result.metadata["failed_dependency_task_id"] == 1


# ===================================================================
# Test Group 3: qwen_code prompt boundary
# ===================================================================


class TestQwenCodePromptBoundary:
    """Verify qwen_code prompt does NOT authorize upstream overwrite."""

    def test_prompt_does_not_authorize_upstream_fix(self):
        """The qwen_code enhanced_task must NOT contain authorization to fix upstream."""
        from tool_box.tools_impl.code_executor import _build_qwen_code_command

        cmd = _build_qwen_code_command(
            task="Analyze data from task 3",
            work_dir="/tmp/test",
            file_prefix="run_001",
            output_format="json",
            allowed_tools=["Bash", "Edit"],
            allowed_dirs=["/tmp/data"],
            model=None,
            debug=False,
            allowed_dirs_info="",
        )

        # The prompt is the argument after -p
        prompt_idx = cmd.index("-p") + 1
        prompt_text = cmd[prompt_idx]

        # Must NOT contain the old authorization language
        assert "AUTHORIZED to produce or fix them" not in prompt_text
        assert "Do NOT stop and report BLOCKED_DEPENDENCY" not in prompt_text

        # Must contain the new boundary language
        assert "BLOCKED_DEPENDENCY" in prompt_text
        assert "Do NOT silently fabricate" in prompt_text

    def test_prompt_consistent_with_claude_code_boundary(self):
        """qwen_code and claude_code should both treat missing deps as blockers."""
        from tool_box.tools_impl.code_executor import _build_qwen_code_command

        cmd = _build_qwen_code_command(
            task="Run analysis",
            work_dir="/tmp/w",
            file_prefix="p",
            output_format="json",
            allowed_tools=["Bash"],
            allowed_dirs=[],
            model=None,
            debug=False,
            allowed_dirs_info="",
        )

        prompt_idx = cmd.index("-p") + 1
        prompt_text = cmd[prompt_idx]

        # Both backends should instruct to report BLOCKED_DEPENDENCY
        assert "STATUS: BLOCKED_DEPENDENCY" in prompt_text
