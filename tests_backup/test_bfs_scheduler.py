import os
from typing import Any, Dict, List

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import bfs_schedule
from app.utils import split_prefix


def _short_names(rows: List[Dict[str, Any]]) -> List[str]:
    return [split_prefix(r.get("name", ""))[1] for r in rows]


def test_bfs_schedule_global_hierarchy_ordering(tmp_path, monkeypatch):
    """Test BFS scheduler global ordering with hierarchy-aware sorting."""
    test_db = tmp_path / "bfs_global.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create hierarchical tasks with mixed priorities
    # Root tasks: A(priority=5), B(priority=1)
    # A's children: A1(priority=10), A2(priority=2)
    # B's children: B1(priority=3), B2(priority=8)

    a = repo.create_task("[TEST] A", status="pending", priority=5)
    b = repo.create_task("[TEST] B", status="pending", priority=1)

    a1 = repo.create_task("[TEST] A1", status="pending", priority=10, parent_id=a)
    a2 = repo.create_task("[TEST] A2", status="pending", priority=2, parent_id=a)

    b1 = repo.create_task("[TEST] B1", status="pending", priority=3, parent_id=b)
    b2 = repo.create_task("[TEST] B2", status="pending", priority=8, parent_id=b)

    # BFS should order by: priority ASC, root_id ASC, depth ASC, path ASC, id ASC
    # Expected: B(1) -> A(5) -> B1(3) -> B2(8) -> A2(2) -> A1(10)
    # (roots first by priority, then children grouped by root)

    order = list(bfs_schedule())
    names = _short_names(order)

    # Verify roots come before children and are grouped by subtree
    assert names.index("B") < names.index("B1")
    assert names.index("B") < names.index("B2")
    assert names.index("A") < names.index("A1")
    assert names.index("A") < names.index("A2")

    # Verify B subtree comes before A subtree (B has lower priority)
    assert names.index("B") < names.index("A")
    assert names.index("B1") < names.index("A1")
    assert names.index("B2") < names.index("A2")


def test_bfs_schedule_plan_scoped_ordering(tmp_path, monkeypatch):
    """Test BFS scheduler with plan title scoping."""
    test_db = tmp_path / "bfs_scoped.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create tasks in different plans
    x1 = repo.create_task("[X] Task1", status="pending", priority=5)
    x2 = repo.create_task("[X] Task2", status="pending", priority=1)

    y1 = repo.create_task("[Y] Task1", status="pending", priority=3)
    y2 = repo.create_task("[Y] Task2", status="pending", priority=7)

    # Test global scope (no title filter)
    global_order = list(bfs_schedule())
    global_names = _short_names(global_order)
    assert len(global_names) == 4
    assert "Task2" in global_names  # X Task2 (priority=1)

    # Test X plan scope
    x_order = list(bfs_schedule(title="X"))
    x_names = _short_names(x_order)
    assert len(x_names) == 2
    assert set(x_names) == {"Task1", "Task2"}
    # Within X plan: Task2(1) should come before Task1(5)
    assert x_names.index("Task2") < x_names.index("Task1")

    # Test Y plan scope
    y_order = list(bfs_schedule(title="Y"))
    y_names = _short_names(y_order)
    assert len(y_names) == 2
    assert set(y_names) == {"Task1", "Task2"}
    # Within Y plan: Task1(3) should come before Task2(7)
    assert y_names.index("Task1") < y_names.index("Task2")


def test_bfs_schedule_mixed_hierarchy_priorities(tmp_path, monkeypatch):
    """Test BFS scheduler with complex hierarchy and priority interactions."""
    test_db = tmp_path / "bfs_complex.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create complex hierarchy:
    # Root1(priority=10) -> Child1A(priority=1), Child1B(priority=5)
    # Root2(priority=2) -> Child2A(priority=8), Child2B(priority=3)

    root1 = repo.create_task("[COMPLEX] Root1", status="pending", priority=10)
    root2 = repo.create_task("[COMPLEX] Root2", status="pending", priority=2)

    child1a = repo.create_task("[COMPLEX] Child1A", status="pending", priority=1, parent_id=root1)
    child1b = repo.create_task("[COMPLEX] Child1B", status="pending", priority=5, parent_id=root1)

    child2a = repo.create_task("[COMPLEX] Child2A", status="pending", priority=8, parent_id=root2)
    child2b = repo.create_task("[COMPLEX] Child2B", status="pending", priority=3, parent_id=root2)

    order = list(bfs_schedule(title="COMPLEX"))
    names = _short_names(order)

    # Root2 should come first (priority=2 < priority=10)
    assert names.index("Root2") < names.index("Root1")

    # All Root2 subtree should come before Root1 subtree
    assert names.index("Root2") < names.index("Root1")
    assert names.index("Child2A") < names.index("Root1")
    assert names.index("Child2B") < names.index("Root1")

    # Within Root2 subtree: Root2 -> Child2B(3) -> Child2A(8)
    assert names.index("Root2") < names.index("Child2B")
    assert names.index("Root2") < names.index("Child2A")
    assert names.index("Child2B") < names.index("Child2A")

    # Within Root1 subtree: Root1 -> Child1A(1) -> Child1B(5)
    assert names.index("Root1") < names.index("Child1A")
    assert names.index("Root1") < names.index("Child1B")
    assert names.index("Child1A") < names.index("Child1B")


def test_bfs_schedule_empty_and_edge_cases(tmp_path, monkeypatch):
    """Test BFS scheduler edge cases: no tasks, non-existent plan, etc."""
    test_db = tmp_path / "bfs_edge.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Empty database
    empty_order = list(bfs_schedule())
    assert len(empty_order) == 0

    # Non-existent plan
    nonexistent_order = list(bfs_schedule(title="NONEXISTENT"))
    assert len(nonexistent_order) == 0

    # Add some tasks but filter by non-matching plan
    repo.create_task("[REAL] Task1", status="pending", priority=1)
    repo.create_task("[REAL] Task2", status="pending", priority=2)

    # Global should find tasks
    global_order = list(bfs_schedule())
    assert len(global_order) == 2

    # Non-matching plan should find nothing
    filtered_order = list(bfs_schedule(title="FAKE"))
    assert len(filtered_order) == 0


def test_bfs_schedule_done_tasks_excluded(tmp_path, monkeypatch):
    """Test that BFS scheduler only includes pending tasks."""
    test_db = tmp_path / "bfs_status.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create tasks with different statuses
    pending1 = repo.create_task("[STATUS] Pending1", status="pending", priority=1)
    pending2 = repo.create_task("[STATUS] Pending2", status="pending", priority=2)
    done1 = repo.create_task("[STATUS] Done1", status="done", priority=1)
    failed1 = repo.create_task("[STATUS] Failed1", status="failed", priority=1)

    # Only pending tasks should be returned
    order = list(bfs_schedule(title="STATUS"))
    names = _short_names(order)

    assert len(names) == 2
    assert set(names) == {"Pending1", "Pending2"}
    assert "Done1" not in names
    assert "Failed1" not in names

    # Verify ordering by priority
    assert names.index("Pending1") < names.index("Pending2")
