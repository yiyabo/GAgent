import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import get_plan_db_path
from app.services.plans.plan_models import PlanNode


def test_create_and_delete_plan(plan_repo: PlanRepository, main_db_path: Path):
    plan = plan_repo.create_plan("Demo", description="desc", metadata={"owner": "tester"})
    plan_id = plan.id

    with sqlite3.connect(main_db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT title, metadata, plan_db_path FROM plans WHERE id=?", (plan_id,)).fetchone()
    assert row is not None
    assert row["title"] == "Demo"

    plan_path = get_plan_db_path(plan_id)
    assert plan_path.exists()

    plan_repo.delete_plan(plan_id)
    with sqlite3.connect(main_db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT 1 FROM plans WHERE id=?", (plan_id,)).fetchone()
    assert row is None
    assert not plan_path.exists()


def test_plan_initialization_tables(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Init")
    plan_path = get_plan_db_path(plan.id)
    with sqlite3.connect(plan_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plan_meta", "tasks", "task_dependencies", "snapshots"}.issubset(tables)


def test_task_crud_and_context(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("CRUD")
    plan_id = plan.id

    root = plan_repo.create_task(plan_id, name="Root")
    dependency = plan_repo.create_task(plan_id, name="Dependency")
    child = plan_repo.create_task(
        plan_id,
        name="Child",
        parent_id=root.id,
        metadata={"kind": "leaf"},
        dependencies=[dependency.id],
    )
    assert child.parent_id == root.id
    assert child.dependencies == [dependency.id]
    assert child.status == "pending"

    updated = plan_repo.update_task(
        plan_id,
        child.id,
        name="Child Updated",
        status="running",
        instruction="New instruction",
        metadata={"kind": "leaf", "version": 2},
        dependencies=[dependency.id, root.id],
        context_combined="context",
        context_sections=[{"title": "section", "content": "value"}],
        context_meta={"source": "test"},
        execution_result="success",
    )
    assert updated.name == "Child Updated"
    assert set(updated.dependencies) == {dependency.id, root.id}
    assert updated.context_combined == "context"
    assert updated.context_sections == [{"title": "section", "content": "value"}]
    assert updated.context_meta == {"source": "test"}
    assert updated.execution_result == "success"
    assert updated.status == "running"

    reloaded = plan_repo.get_node(plan_id, child.id)
    assert reloaded.execution_result == "success"
    assert reloaded.status == "running"

    moved = plan_repo.move_task(plan_id, child.id, new_parent_id=dependency.id, new_position=0)
    assert moved.parent_id == dependency.id

    plan_repo.delete_task(plan_id, dependency.id)
    with pytest.raises(ValueError):
        plan_repo.get_node(plan_id, child.id)


def test_plan_tree_snapshot(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Snapshot")
    plan_id = plan.id
    root = plan_repo.create_task(plan_id, name="Root")
    plan_repo.create_task(plan_id, name="Child", parent_id=root.id)

    tree = plan_repo.get_plan_tree(plan_id)
    root_node = tree.get_node(root.id)
    root_node.name = "Root Updated"

    injected = PlanNode(
        id=999,
        plan_id=plan_id,
        name="Injected",
        parent_id=root.id,
        position=1,
        depth=1,
        path=f"/{root.id}/999",
        metadata={"source": "upsert"},
        dependencies=[],
    )
    tree.nodes[injected.id] = injected
    tree.adjacency.setdefault(root.id, []).append(injected.id)
    tree.rebuild_adjacency()

    plan_repo.upsert_plan_tree(tree, note="snapshot")

    refreshed = plan_repo.get_plan_tree(plan_id)
    assert refreshed.get_node(root.id).name == "Root Updated"
    assert refreshed.get_node(injected.id).metadata == {"source": "upsert"}
    assert refreshed.get_node(root.id).status == root_node.status
    assert refreshed.get_node(injected.id).status == injected.status

    plan_path = get_plan_db_path(plan_id)
    with sqlite3.connect(plan_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert count >= 1
