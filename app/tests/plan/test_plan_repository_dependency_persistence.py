"""Regression tests for dependency persistence in upsert_plan_tree.

Root cause being guarded: a leaf task that declares a dependency on a composite
(branch) node previously lost the edge entirely. The branch id was treated as an
invalid ancestor and dropped without expanding to its executable leaves, and the
``task_dependencies`` table diverged from each node's ``metadata.dependencies``.

Fix A made ``upsert_plan_tree`` derive both the dependency table and node
metadata from a single normalized map, so these tests assert that the branch
dependency expands to leaves AND that table/metadata stay consistent.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.repository import plan_repository as plan_repository_module
from app.repository.plan_repository import PlanRepository
from app.services.plans.dependency_validation import build_normalized_dependency_map
from app.services.plans.plan_models import PlanNode, PlanTree


def _node(
    *,
    node_id: int,
    name: str,
    parent_id: int | None,
    position: int,
    depth: int,
    path: str,
    dependencies: list[int] | None = None,
) -> PlanNode:
    return PlanNode(
        id=node_id,
        plan_id=1,
        name=name,
        parent_id=parent_id,
        position=position,
        depth=depth,
        path=path,
        dependencies=list(dependencies or []),
    )


def _make_branch_dependency_tree() -> PlanTree:
    """Tree where leaf 13 depends on composite branch 2 (leaves 6, 7)."""
    nodes = [
        _node(node_id=1, name="Root", parent_id=None, position=0, depth=0, path="/1"),
        _node(node_id=2, name="Branch A", parent_id=1, position=0, depth=1, path="/1/2"),
        _node(node_id=6, name="Leaf A1", parent_id=2, position=0, depth=2, path="/1/2/6"),
        _node(node_id=7, name="Leaf A2", parent_id=2, position=1, depth=2, path="/1/2/7"),
        _node(node_id=3, name="Branch B", parent_id=1, position=1, depth=1, path="/1/3"),
        _node(
            node_id=13,
            name="Leaf B1",
            parent_id=3,
            position=0,
            depth=2,
            path="/1/3/13",
            dependencies=[2],
        ),
    ]
    tree = PlanTree(id=1, title="Dependency Persistence Plan")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def test_build_normalized_dependency_map_expands_branch_dependency_to_leaves() -> None:
    tree = _make_branch_dependency_tree()

    dep_map, issues = build_normalized_dependency_map(tree)

    assert sorted(dep_map[13]) == [6, 7]
    assert dep_map[1] == []
    assert dep_map[2] == []
    assert dep_map[3] == []
    assert dep_map[6] == []
    assert dep_map[7] == []
    codes = {issue.code for issue in issues}
    assert "composite_dependency_expanded" in codes
    assert "composite_dependency_dropped" not in codes


def _make_plan_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE plan_meta (key TEXT PRIMARY KEY, value TEXT)
        """
    )
    conn.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            instruction TEXT,
            parent_id INTEGER,
            position INTEGER DEFAULT 0,
            path TEXT,
            depth INTEGER DEFAULT 0,
            metadata TEXT,
            execution_result TEXT,
            context_combined TEXT,
            context_sections TEXT,
            context_meta TEXT,
            context_updated_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE task_dependencies (
            task_id INTEGER NOT NULL,
            depends_on INTEGER NOT NULL,
            PRIMARY KEY (task_id, depends_on)
        )
        """
    )
    conn.commit()
    conn.close()


def test_upsert_plan_tree_persists_expanded_dependencies_in_table_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.sqlite"
    _make_plan_db(plan_path)

    main_db = sqlite3.connect(":memory:")
    main_db.row_factory = sqlite3.Row
    main_db.execute(
        """
        CREATE TABLE plans (
            id INTEGER PRIMARY KEY,
            title TEXT,
            description TEXT,
            metadata TEXT,
            plan_db_path TEXT,
            updated_at TEXT
        )
        """
    )
    main_db.commit()

    from contextlib import contextmanager

    @contextmanager
    def _fake_get_db():
        try:
            yield main_db
            main_db.commit()
        except Exception:
            main_db.rollback()
            raise

    repo = PlanRepository()
    monkeypatch.setattr(plan_repository_module, "get_db", _fake_get_db)
    monkeypatch.setattr(plan_repository_module, "get_plan_db_path", lambda _plan_id: plan_path)
    monkeypatch.setattr(plan_repository_module, "update_plan_metadata", lambda *a, **k: None)
    monkeypatch.setattr(repo, "_touch_plan", lambda _plan_id: None)

    repo.upsert_plan_tree(_make_branch_dependency_tree())
    main_db.close()

    check = sqlite3.connect(plan_path)
    check.row_factory = sqlite3.Row

    edge_rows = check.execute(
        "SELECT depends_on FROM task_dependencies WHERE task_id=? ORDER BY depends_on",
        (13,),
    ).fetchall()
    table_deps = [row["depends_on"] for row in edge_rows]
    assert table_deps == [6, 7]

    branch_edges = check.execute(
        "SELECT COUNT(*) AS n FROM task_dependencies WHERE depends_on=?",
        (2,),
    ).fetchone()
    assert branch_edges["n"] == 0

    meta_row = check.execute("SELECT metadata FROM tasks WHERE id=?", (13,)).fetchone()
    check.close()
    metadata_deps = json.loads(meta_row["metadata"]).get("dependencies")
    assert metadata_deps == table_deps
