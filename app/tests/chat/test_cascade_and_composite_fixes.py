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
        row = conn.execute("SELECT status, execution_result FROM tasks WHERE id=20").fetchone()
        assert row["status"] == "completed"
        payload = json.loads(row["execution_result"])
        assert payload["status"] == "completed"
        assert payload["metadata"]["auto_completed_from_children"] is True

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

    def test_extension_only_match_no_longer_works(self, tmp_path):
        expected = tmp_path / "expected_output.csv"
        actual = tmp_path / "different_name.csv"
        actual.write_text("data")

        verifier = TaskVerificationService()
        result = verifier._fallback_artifact_match(
            expected, [str(actual)], lenient=True
        )
        assert result is None

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


# ===========================================================================
# 5. scope-guardrail-fix: full_plan_execution composite dep handling
# ===========================================================================

def _make_scope_tree(nodes_spec):
    """Build a minimal PlanTree mock from a spec list.

    Each entry: (task_id, status, dependencies, children_ids)
    """
    nodes = {}
    children_map = {}
    for tid, status, deps, children in nodes_spec:
        node = MagicMock()
        node.id = tid
        node.status = status
        node.dependencies = deps
        node.execution_result = None
        nodes[tid] = node
        children_map[tid] = children

    tree = MagicMock()
    tree.has_node = lambda tid: tid in nodes
    tree.get_node = lambda tid: nodes[tid]
    tree.children_ids = lambda tid: children_map.get(tid, [])
    return tree


class TestFullPlanExecutionCompositeDeps:
    """resolve_explicit_task_scope_target with full_plan_execution=True should
    not block on composite parent deps whose leaves are all covered."""

    def test_p1_composite_dep_all_leaves_completed_not_blocked(self):
        """P1: Task A depends on composite B; all leaves of B are completed.
        full_plan_execution=True → A is returned (not blocked)."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        # Tree:
        #   B (composite, pending) → children: [B1, B2]
        #   B1 (leaf, completed)
        #   B2 (leaf, completed)
        #   A (leaf, pending, depends on B)
        tree = _make_scope_tree([
            (100, "pending", [], [101, 102]),   # B composite
            (101, "completed", [], []),          # B1 leaf
            (102, "completed", [], []),          # B2 leaf
            (200, "pending", [100], []),         # A leaf, depends on B
        ])
        tree.get_node(101).execution_result = '{"result": "ok"}'
        tree.get_node(102).execution_result = '{"result": "ok"}'

        result = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            auto_include_dependency_closure=True,
            full_plan_execution=True,
        )
        assert result == 200, "A should be executable when composite dep's leaves are all done"

    def test_p1b_composite_dep_leaves_in_scope_not_blocked(self):
        """P1b: Task A depends on composite B; leaves of B are pending but in scope.
        full_plan_execution=True → A is returned (leaves will be run)."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        # Tree:
        #   B (composite, pending) → children: [B1, B2]
        #   B1 (leaf, pending) — in scope
        #   B2 (leaf, pending) — in scope
        #   A (leaf, pending, depends on B) — in scope
        tree = _make_scope_tree([
            (100, "pending", [], [101, 102]),   # B composite
            (101, "pending", [], []),            # B1 leaf, in scope
            (102, "pending", [], []),            # B2 leaf, in scope
            (200, "pending", [100], []),         # A leaf, depends on B
        ])

        # scope = [101, 102, 200] — all three are in explicit_task_ids
        # B1 and B2 are in scope → composite B is "satisfied"
        # But A depends on B (composite), and B1/B2 are in scope_ids
        # So A should NOT be blocked by B
        # However A has unmet_in_scope deps (101, 102 are in scope and pending)
        # → resolve returns 101 first (the first executable in scope)
        result = resolve_explicit_task_scope_target(
            tree, [101, 102, 200],
            allow_cascade_rerun=True,
            auto_include_dependency_closure=True,
            full_plan_execution=True,
        )
        # 101 has no deps → it's the first executable
        assert result == 101

    def test_p2_leaf_dep_out_of_scope_still_blocks(self):
        """P2: Task A depends on leaf Task X (pending, not in scope).
        full_plan_execution=False → A is blocked (returns None)."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        # Tree:
        #   X (leaf, pending) — NOT in scope
        #   A (leaf, pending, depends on X)
        tree = _make_scope_tree([
            (50, "pending", [], []),    # X leaf, out of scope
            (200, "pending", [50], []), # A leaf, depends on X
        ])

        result = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            auto_include_dependency_closure=False,
            full_plan_execution=False,
        )
        assert result is None, "A should be blocked when out-of-scope leaf dep is pending"

    def test_p2_leaf_dep_out_of_scope_also_blocks_with_full_plan(self):
        """P2 variant: Even with full_plan_execution=True, a pending out-of-scope
        LEAF dep still blocks (only composite deps get the relaxed check)."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        tree = _make_scope_tree([
            (50, "pending", [], []),    # X leaf, out of scope
            (200, "pending", [50], []), # A leaf, depends on X
        ])

        result = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            auto_include_dependency_closure=False,
            full_plan_execution=True,
        )
        assert result is None, "Leaf out-of-scope pending dep should still block even in full_plan mode"

    def test_p3_all_deps_completed_same_result_both_modes(self):
        """P3: All deps completed → same result regardless of full_plan_execution."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        tree = _make_scope_tree([
            (100, "completed", [], []),  # dep, completed
            (200, "pending", [100], []), # A, depends on completed dep
        ])
        tree.get_node(100).execution_result = '{"result": "ok"}'

        result_normal = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            full_plan_execution=False,
        )
        result_full = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            full_plan_execution=True,
        )
        assert result_normal == 200
        assert result_full == 200

    def test_p4_in_scope_ordering_preserved(self):
        """P4: Task A depends on in-scope Task B (pending).
        B is returned first (ordering preserved), not A."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        tree = _make_scope_tree([
            (10, "pending", [], []),    # B, in scope, no deps
            (20, "pending", [10], []),  # A, in scope, depends on B
        ])

        result = resolve_explicit_task_scope_target(
            tree, [10, 20],
            allow_cascade_rerun=True,
            full_plan_execution=True,
        )
        assert result == 10, "B should be returned first since A depends on it"

    def test_p5_composite_dep_with_unfinished_leaf_not_in_scope_still_blocks(self):
        """P5: Task A depends on composite B; one leaf of B is pending and NOT
        in scope. full_plan_execution=True still blocks A."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        # Tree:
        #   B (composite, pending) → children: [B1, B2]
        #   B1 (leaf, completed)
        #   B2 (leaf, pending) — NOT in scope
        #   A (leaf, pending, depends on B)
        tree = _make_scope_tree([
            (100, "pending", [], [101, 102]),   # B composite
            (101, "completed", [], []),          # B1 leaf, done
            (102, "pending", [], []),            # B2 leaf, pending, NOT in scope
            (200, "pending", [100], []),         # A leaf, depends on B
        ])
        tree.get_node(101).execution_result = '{"result": "ok"}'

        result = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            auto_include_dependency_closure=False,
            full_plan_execution=True,
        )
        assert result is None, "A should be blocked when composite dep has unfinished leaf not in scope"

    def test_real_world_plan72_pattern(self):
        """Reproduce the Plan 72 pattern: leaf tasks depend on composite parent
        nodes that are pending because not all their children have run yet.

        Scope = [25, 26] (leaves of Task 7 "撰写第5章")
        Task 25 depends on Task 2 (composite "文献检索", pending)
        Task 2 has children [14, 15, 16]; 14 and 15 are completed, 16 is in scope.

        With full_plan_execution=True → Task 25 should NOT be blocked.
        """
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        # Task 2: composite "文献检索", pending (some children still pending)
        # Task 14, 15: completed leaves of Task 2
        # Task 16: pending leaf of Task 2, in scope
        # Task 25: pending leaf, depends on Task 2
        # Task 26: pending leaf, depends on Task 25
        tree = _make_scope_tree([
            (2,  "pending",   [],     [14, 15, 16]),  # composite, pending
            (14, "completed", [],     []),             # leaf, done
            (15, "completed", [],     []),             # leaf, done
            (16, "pending",   [],     []),             # leaf, in scope
            (25, "pending",   [2],    []),             # leaf, depends on composite 2
            (26, "pending",   [25],   []),             # leaf, depends on 25
        ])
        tree.get_node(14).execution_result = '{"result": "ok"}'
        tree.get_node(15).execution_result = '{"result": "ok"}'

        # scope = [16, 25, 26] — leaf 16 is in scope, so composite 2 is "satisfied"
        result = resolve_explicit_task_scope_target(
            tree, [16, 25, 26],
            allow_cascade_rerun=True,
            auto_include_dependency_closure=True,
            full_plan_execution=True,
        )
        # 16 has no deps → it's the first executable
        assert result == 16, f"Expected 16 (first executable), got {result}"

    def test_full_plan_false_blocks_on_composite_dep(self):
        """Regression: with full_plan_execution=False, composite pending dep
        still blocks (old behaviour preserved)."""
        from app.routers.chat.guardrail_handlers import resolve_explicit_task_scope_target

        tree = _make_scope_tree([
            (100, "pending", [], [101, 102]),
            (101, "completed", [], []),
            (102, "pending", [], []),            # leaf of composite, pending, not in scope
            (200, "pending", [100], []),
        ])
        tree.get_node(101).execution_result = '{"result": "ok"}'

        result = resolve_explicit_task_scope_target(
            tree, [200],
            allow_cascade_rerun=True,
            full_plan_execution=False,
        )
        assert result is None, "Old behaviour: composite pending dep should block when full_plan_execution=False"
