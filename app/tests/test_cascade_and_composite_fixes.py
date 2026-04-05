"""Tests for cascade completion, composite task expansion, and verification fixes.

These tests cover the fixes for the task-8-failure scenario:
1. _maybe_autocomplete_ancestors should NOT count cascade-completed children
2. cascade_update_descendants_status should NOT overwrite genuine execution results
3. resolve_all_explicit_task_scope_targets returns ALL executable leaves
4. _fallback_artifact_match no longer uses lenient "any file" fallback
"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.repository.plan_repository import PlanRepository
from app.services.plans.task_verification import TaskVerificationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tasks_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER,
            status TEXT DEFAULT 'pending',
            execution_result TEXT,
            updated_at TEXT DEFAULT ''
        )
        """
    )
    return conn


def _insert(conn, task_id, parent_id, status="pending", execution_result=None):
    conn.execute(
        "INSERT INTO tasks (id, parent_id, status, execution_result, updated_at) VALUES (?, ?, ?, ?, '')",
        (task_id, parent_id, status, execution_result),
    )


# ===========================================================================
# 1. _maybe_autocomplete_ancestors: cascade-completed children
# ===========================================================================

class TestAutocompleteAncestorsCascadeAware:
    """_maybe_autocomplete_ancestors must NOT auto-complete a parent when some
    children are only cascade-completed (never genuinely executed)."""

    def test_cascade_completed_children_do_not_trigger_parent_completion(self):
        conn = _make_tasks_conn()
        _insert(conn, 10, None, "pending")               # parent
        _insert(conn, 11, 10, "completed", '{"real": 1}')  # genuinely completed
        _insert(conn, 12, 10, "completed", "Completed as part of parent task #11")  # cascade
        _insert(conn, 13, 10, "completed", "Completed as part of parent task #11")  # cascade

        repo = PlanRepository()
        updated = repo._maybe_autocomplete_ancestors(conn, 11)

        assert updated == 0
        row = conn.execute("SELECT status FROM tasks WHERE id=10").fetchone()
        assert row["status"] == "pending"

    def test_all_genuinely_completed_children_still_trigger_parent_completion(self):
        conn = _make_tasks_conn()
        _insert(conn, 20, None, "pending")
        _insert(conn, 21, 20, "completed", '{"result": "ok"}')
        _insert(conn, 22, 20, "completed", '{"result": "ok"}')

        repo = PlanRepository()
        updated = repo._maybe_autocomplete_ancestors(conn, 21)

        assert updated == 1
        row = conn.execute("SELECT status FROM tasks WHERE id=20").fetchone()
        assert row["status"] == "completed"

    def test_mixed_genuine_and_null_result_does_complete_parent(self):
        """A child with status=completed and execution_result=NULL is treated as
        genuinely completed (the cascade marker is a specific string, not NULL)."""
        conn = _make_tasks_conn()
        _insert(conn, 30, None, "pending")
        _insert(conn, 31, 30, "completed", None)  # completed but no result stored
        _insert(conn, 32, 30, "completed", '{"result": "ok"}')

        repo = PlanRepository()
        updated = repo._maybe_autocomplete_ancestors(conn, 32)

        assert updated == 1
        row = conn.execute("SELECT status FROM tasks WHERE id=30").fetchone()
        assert row["status"] == "completed"

    def test_cascade_marker_case_insensitive(self):
        conn = _make_tasks_conn()
        _insert(conn, 40, None, "pending")
        _insert(conn, 41, 40, "completed", '{"result": "ok"}')
        # Mixed case cascade marker
        _insert(conn, 42, 40, "completed", "Completed As Part Of Parent Task #41")

        repo = PlanRepository()
        updated = repo._maybe_autocomplete_ancestors(conn, 41)

        assert updated == 0


# ===========================================================================
# 2. cascade_update_descendants_status: preserve genuine results
# ===========================================================================

class TestCascadePreservesGenuineResults:
    """cascade_update_descendants_status should NOT overwrite descendants that
    have genuine (non-cascade) execution results."""

    def test_cascade_skips_descendant_with_genuine_result(self, monkeypatch, tmp_path):
        import app.repository.plan_repository as prm

        plan_path = tmp_path / "plan.sqlite"
        conn = sqlite3.connect(plan_path)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE plan_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, name TEXT, status TEXT DEFAULT 'pending',
                instruction TEXT, parent_id INTEGER, position INTEGER DEFAULT 0,
                path TEXT, depth INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
                execution_result TEXT, context_combined TEXT, context_sections TEXT DEFAULT '[]',
                context_meta TEXT DEFAULT '{}', context_updated_at TEXT DEFAULT '',
                created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '')"""
        )
        # Parent task 8 with path /8
        conn.execute(
            "INSERT INTO tasks (id, name, status, parent_id, path, depth) VALUES (8, 'Parent', 'completed', NULL, '/8', 0)"
        )
        # Child 19: already genuinely completed with real result
        conn.execute(
            "INSERT INTO tasks (id, name, status, parent_id, path, depth, execution_result) VALUES (19, 'Task19', 'completed', 8, '/8/19', 1, ?)",
            (json.dumps({"status": "completed", "content": "real analysis results"}),),
        )
        # Child 20: pending, should be cascade-updated
        conn.execute(
            "INSERT INTO tasks (id, name, status, parent_id, path, depth) VALUES (20, 'Task20', 'pending', 8, '/8/20', 1)"
        )
        conn.commit()
        conn.close()

        repo = PlanRepository()
        monkeypatch.setattr(prm, "get_plan_db_path", lambda _pid: plan_path)
        monkeypatch.setattr(repo, "_touch_plan", lambda _pid: None)

        updated = repo.cascade_update_descendants_status(
            plan_id=99,
            task_id=8,
            status="completed",
            execution_result="Completed as part of parent task #8",
        )

        # Only task 20 should be updated (pending → completed)
        # Task 19 should be untouched (has genuine result)
        assert updated == 1

        check = sqlite3.connect(plan_path)
        check.row_factory = sqlite3.Row
        row19 = check.execute("SELECT execution_result FROM tasks WHERE id=19").fetchone()
        row20 = check.execute("SELECT execution_result FROM tasks WHERE id=20").fetchone()
        check.close()

        assert "real analysis results" in row19["execution_result"]
        assert "Completed as part of parent task" in row20["execution_result"]

    def test_cascade_overwrites_previous_cascade_marker(self, monkeypatch, tmp_path):
        """A previously cascade-completed child that's already at the target
        status is correctly skipped (status != ? filter).  Only pending/failed
        descendants are cascade-updated."""
        import app.repository.plan_repository as prm

        plan_path = tmp_path / "plan.sqlite"
        conn = sqlite3.connect(plan_path)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE plan_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, name TEXT, status TEXT DEFAULT 'pending',
                instruction TEXT, parent_id INTEGER, position INTEGER DEFAULT 0,
                path TEXT, depth INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
                execution_result TEXT, context_combined TEXT, context_sections TEXT DEFAULT '[]',
                context_meta TEXT DEFAULT '{}', context_updated_at TEXT DEFAULT '',
                created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '')"""
        )
        conn.execute(
            "INSERT INTO tasks (id, name, status, parent_id, path, depth) VALUES (8, 'Parent', 'completed', NULL, '/8', 0)"
        )
        # Previously cascade-completed child — already at target status
        conn.execute(
            "INSERT INTO tasks (id, name, status, parent_id, path, depth, execution_result) VALUES (19, 'Task19', 'completed', 8, '/8/19', 1, 'Completed as part of parent task #999')"
        )
        # Pending child with cascade marker — should be updated
        conn.execute(
            "INSERT INTO tasks (id, name, status, parent_id, path, depth, execution_result) VALUES (20, 'Task20', 'pending', 8, '/8/20', 1, NULL)"
        )
        conn.commit()
        conn.close()

        repo = PlanRepository()
        monkeypatch.setattr(prm, "get_plan_db_path", lambda _pid: plan_path)
        monkeypatch.setattr(repo, "_touch_plan", lambda _pid: None)

        updated = repo.cascade_update_descendants_status(
            plan_id=99,
            task_id=8,
            status="completed",
            execution_result="Completed as part of parent task #8",
        )

        # Task 19 is already completed (same target status) → skipped
        # Task 20 is pending → updated
        assert updated == 1
        check = sqlite3.connect(plan_path)
        check.row_factory = sqlite3.Row
        row20 = check.execute("SELECT execution_result FROM tasks WHERE id=20").fetchone()
        check.close()
        assert "parent task #8" in row20["execution_result"]


# ===========================================================================
# 3. resolve_all_explicit_task_scope_targets
# ===========================================================================

class TestResolveAllExplicitTaskScopeTargets:
    """resolve_all_explicit_task_scope_targets should return ALL executable
    leaves for a composite task, in dependency order."""

    def _make_tree(self):
        """Build a mock PlanTree:
        Task 8 (composite)
          ├── Task 19 (leaf, pending)
          ├── Task 20 (leaf, pending, depends on 19)
          ├── Task 21 (leaf, pending, depends on 20)
          └── Task 22 (leaf, pending, depends on 21)
        """
        from app.routers.chat.guardrail_handlers import resolve_all_explicit_task_scope_targets

        nodes = {}
        for tid, status, deps in [
            (8, "pending", []),
            (19, "pending", []),
            (20, "pending", [19]),
            (21, "pending", [20]),
            (22, "pending", [21]),
        ]:
            node = MagicMock()
            node.id = tid
            node.status = status
            node.dependencies = deps
            node.execution_result = None
            nodes[tid] = node

        tree = MagicMock()
        tree.has_node = lambda tid: tid in nodes
        tree.get_node = lambda tid: nodes[tid]
        tree.children_ids = lambda tid: (
            [19, 20, 21, 22] if tid == 8 else []
        )

        return tree, resolve_all_explicit_task_scope_targets

    def test_returns_all_leaves_in_order(self):
        tree, resolve_all = self._make_tree()
        result = resolve_all(tree, [8])
        assert result == [19, 20, 21, 22]

    def test_skips_completed_leaves(self):
        tree, resolve_all = self._make_tree()
        tree.get_node(19).status = "completed"
        tree.get_node(19).execution_result = '{"result": "ok"}'
        result = resolve_all(tree, [8])
        assert result == [20, 21, 22]
        assert 19 not in result

    def test_includes_cascade_completed_with_allow_rerun(self):
        tree, resolve_all = self._make_tree()
        tree.get_node(19).status = "completed"
        tree.get_node(19).execution_result = "Completed as part of parent task #22"
        result = resolve_all(tree, [8], allow_cascade_rerun=True)
        assert 19 in result

    def test_empty_for_all_completed(self):
        tree, resolve_all = self._make_tree()
        for tid in [19, 20, 21, 22]:
            tree.get_node(tid).status = "completed"
            tree.get_node(tid).execution_result = '{"result": "ok"}'
        result = resolve_all(tree, [8])
        assert result == []

    def test_single_leaf_task_returns_single_item(self):
        tree, resolve_all = self._make_tree()
        result = resolve_all(tree, [19])
        assert result == [19]

    def test_auto_dependency_closure_includes_unfinished_prerequisites(self):
        from app.routers.chat.guardrail_handlers import (
            resolve_all_explicit_task_scope_targets,
            resolve_explicit_task_scope_target,
        )

        tree, _ = self._make_tree()
        result = resolve_all_explicit_task_scope_targets(
            tree,
            [21],
            auto_include_dependency_closure=True,
        )
        target = resolve_explicit_task_scope_target(
            tree,
            [21],
            auto_include_dependency_closure=True,
        )

        assert result == [19, 20, 21]
        assert target == 19

    def test_auto_dependency_closure_skips_completed_prerequisites(self):
        from app.routers.chat.guardrail_handlers import (
            resolve_all_explicit_task_scope_targets,
            resolve_explicit_task_scope_target,
        )

        tree, _ = self._make_tree()
        tree.get_node(19).status = "completed"
        tree.get_node(19).execution_result = '{"result": "ok"}'

        result = resolve_all_explicit_task_scope_targets(
            tree,
            [21],
            auto_include_dependency_closure=True,
        )
        target = resolve_explicit_task_scope_target(
            tree,
            [21],
            auto_include_dependency_closure=True,
        )

        assert result == [20, 21]
        assert target == 20


# ===========================================================================
# 4. _fallback_artifact_match: no lenient "any file"
# ===========================================================================

class TestFallbackArtifactMatchNoLenient:
    """_fallback_artifact_match should NOT match any-file when extension differs."""

    def test_basename_match_still_works(self, tmp_path):
        expected = tmp_path / "subdir" / "results.csv"
        actual = tmp_path / "results.csv"
        actual.write_text("data")

        verifier = TaskVerificationService()
        result = verifier._fallback_artifact_match(
            expected, [str(actual)], lenient=True
        )
        assert result == actual

    def test_extension_match_still_works(self, tmp_path):
        expected = tmp_path / "expected_output.csv"
        actual = tmp_path / "different_name.csv"
        actual.write_text("data")

        verifier = TaskVerificationService()
        result = verifier._fallback_artifact_match(
            expected, [str(actual)], lenient=True
        )
        assert result == actual

    def test_different_extension_no_match(self, tmp_path):
        expected = tmp_path / "output.pdf"
        actual = tmp_path / "output.png"
        actual.write_bytes(b"\x89PNG")

        verifier = TaskVerificationService()
        result = verifier._fallback_artifact_match(
            expected, [str(actual)], lenient=True
        )
        assert result is None

    def test_no_artifacts_returns_none(self):
        verifier = TaskVerificationService()
        result = verifier._fallback_artifact_match(
            Path("/some/path.csv"), [], lenient=True
        )
        assert result is None
