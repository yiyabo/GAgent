
import os
import pytest
from typing import List, Dict, Any

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import bfs_schedule

# Helper to create a temporary, isolated database for each test
@pytest.fixture
def repo(tmp_path, monkeypatch):
    db_path = tmp_path / f"test_{os.urandom(4).hex()}.db"
    monkeypatch.setattr("app.database.DB_PATH", str(db_path))
    init_db()
    return SqliteTaskRepository()

def _get_names(rows: List[Dict[str, Any]]) -> List[str]:
    """Extracts task names from a list of task dicts."""
    return [r.get("name", "") for r in rows]


def test_bfs_empty_plan(repo: SqliteTaskRepository):
    """Test that bfs_schedule returns an empty list for a plan with no tasks."""
    plan_id = repo.create_plan("Empty Plan")
    order = bfs_schedule(plan_id)
    assert order == []

def test_bfs_no_pending_tasks(repo: SqliteTaskRepository):
    """Test that bfs_schedule returns an empty list if a plan has no pending tasks."""
    plan_id = repo.create_plan("Completed Plan")
    task_a_id = repo.create_task("A", status="done")
    repo.link_task_to_plan(plan_id, task_a_id)
    
    order = bfs_schedule(plan_id)
    assert order == []


def test_bfs_sort_by_root_priority(repo: SqliteTaskRepository):
    """Test that tasks are grouped by their root task's priority."""
    plan_id = repo.create_plan("Multi-Root Plan")
    
    # Create two root tasks with different priorities
    root_a_id = repo.create_task("Root A", status="pending", priority=10)
    root_b_id = repo.create_task("Root B", status="pending", priority=5)
    
    # Create children for each root
    child_a_id = repo.create_task("Child A", status="pending", parent_id=root_a_id)
    child_b_id = repo.create_task("Child B", status="pending", parent_id=root_b_id)

    # Link all tasks to the plan
    repo.link_task_to_plan(plan_id, root_a_id)
    repo.link_task_to_plan(plan_id, root_b_id)
    repo.link_task_to_plan(plan_id, child_a_id)
    repo.link_task_to_plan(plan_id, child_b_id)

    order = bfs_schedule(plan_id)
    names = _get_names(order)

    # Root B (p=5) and its children should come before Root A (p=10) and its children
    assert names == ["Root B", "Child B", "Root A", "Child A"]


def test_bfs_sort_by_depth_then_priority(repo: SqliteTaskRepository):
    """Test that within a task group, sorting is by depth, then priority."""
    plan_id = repo.create_plan("Hierarchical Plan")

    # Create a tree structure
    root_id = repo.create_task("Root", status="pending", priority=10)
    child1_id = repo.create_task("Child1", status="pending", priority=20, parent_id=root_id)
    child2_id = repo.create_task("Child2", status="pending", priority=5, parent_id=root_id)
    grandchild_id = repo.create_task("Grandchild", status="pending", priority=15, parent_id=child1_id)

    # Link all to plan
    repo.link_task_to_plan(plan_id, root_id)
    repo.link_task_to_plan(plan_id, child1_id)
    repo.link_task_to_plan(plan_id, child2_id)
    repo.link_task_to_plan(plan_id, grandchild_id)

    order = bfs_schedule(plan_id)
    names = _get_names(order)

    # Expected order:
    # 1. Root (depth 0)
    # 2. Child2 (depth 1, p=5)
    # 3. Child1 (depth 1, p=20)
    # 4. Grandchild (depth 2, p=15)
    assert names == ["Root", "Child2", "Child1", "Grandchild"]


def test_bfs_handles_multiple_roots_correctly(repo: SqliteTaskRepository):
    """Test scheduling with multiple, inter-mingled root tasks."""
    plan_id = repo.create_plan("Complex Multi-Root Plan")

    # Priorities define the order of the root task groups
    root_a_id = repo.create_task("A", status="pending", priority=20)
    root_b_id = repo.create_task("B", status="pending", priority=10)

    # Children of A
    a1_id = repo.create_task("A1", status="pending", priority=1, parent_id=root_a_id)

    # Children of B
    b1_id = repo.create_task("B1", status="pending", priority=1, parent_id=root_b_id)

    # Link all to plan
    repo.link_task_to_plan(plan_id, root_a_id)
    repo.link_task_to_plan(plan_id, a1_id)
    repo.link_task_to_plan(plan_id, root_b_id)
    repo.link_task_to_plan(plan_id, b1_id)

    order = bfs_schedule(plan_id)
    names = _get_names(order)

    # Group B (p=10) comes before Group A (p=20)
    assert names == ["B", "B1", "A", "A1"]
