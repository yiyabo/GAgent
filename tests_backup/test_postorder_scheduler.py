import os
import sys

import pytest

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import postorder_schedule


def test_postorder_schedule_hierarchy(tmp_path, monkeypatch):
    """Test that postorder scheduling works correctly with task hierarchy."""
    test_db = tmp_path / "postorder_hierarchy.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create a hierarchy: Root -> Child1, Child2 -> Grandchild1, Grandchild2
    root_id = repo.create_task("Root Task", priority=10)
    child1_id = repo.create_task("Child Task 1", priority=20, parent_id=root_id)
    child2_id = repo.create_task("Child Task 2", priority=30, parent_id=root_id)
    grandchild1_id = repo.create_task("Grandchild Task 1", priority=40, parent_id=child1_id)
    grandchild2_id = repo.create_task("Grandchild Task 2", priority=50, parent_id=child2_id)

    # Get the schedule
    scheduled_tasks = list(postorder_schedule())
    scheduled_ids = [task["id"] for task in scheduled_tasks]

    # Verify post-order: grandchildren first, then children, then root
    assert len(scheduled_tasks) == 5

    # Get positions in the schedule
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}

    # Grandchildren should come before their parents
    assert positions[grandchild1_id] < positions[child1_id]
    assert positions[grandchild2_id] < positions[child2_id]

    # Children should come before root
    assert positions[child1_id] < positions[root_id]
    assert positions[child2_id] < positions[root_id]

    # Verify dependencies are included
    root_task = next(task for task in scheduled_tasks if task["id"] == root_id)
    assert grandchild1_id not in root_task["dependencies"]  # grandchildren are not direct dependencies
    assert child1_id in root_task["dependencies"]  # direct children are dependencies
    assert child2_id in root_task["dependencies"]


def test_postorder_schedule_with_title_filter(tmp_path, monkeypatch):
    """Test postorder scheduling with title filtering."""
    test_db = tmp_path / "postorder_filter.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create tasks with different prefixes
    test_root = repo.create_task("[TEST] Root", priority=10)
    test_child = repo.create_task("[TEST] Child", priority=20, parent_id=test_root)
    other_task = repo.create_task("[OTHER] Task", priority=30)

    # Schedule with title filter
    scheduled_tasks = list(postorder_schedule("TEST"))
    scheduled_ids = [task["id"] for task in scheduled_tasks]

    # Should only include TEST tasks
    assert len(scheduled_tasks) == 2
    assert test_root in scheduled_ids
    assert test_child in scheduled_ids
    assert other_task not in scheduled_ids

    # Verify post-order within filtered tasks
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}
    assert positions[test_child] < positions[test_root]


def test_postorder_schedule_empty_hierarchy(tmp_path, monkeypatch):
    """Test postorder scheduling with no tasks."""
    test_db = tmp_path / "postorder_empty.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()

    scheduled_tasks = list(postorder_schedule())
    assert len(scheduled_tasks) == 0


def test_postorder_schedule_flat_tasks(tmp_path, monkeypatch):
    """Test postorder scheduling with flat (no hierarchy) tasks."""
    test_db = tmp_path / "postorder_flat.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create flat tasks (no parent-child relationships)
    task1_id = repo.create_task("Task 1", priority=30)
    task2_id = repo.create_task("Task 2", priority=10)
    task3_id = repo.create_task("Task 3", priority=20)

    scheduled_tasks = list(postorder_schedule())
    scheduled_ids = [task["id"] for task in scheduled_tasks]

    # Should have all tasks
    assert len(scheduled_tasks) == 3
    assert task1_id in scheduled_ids
    assert task2_id in scheduled_ids
    assert task3_id in scheduled_ids

    # For flat tasks, should be ordered by priority
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}
    assert positions[task2_id] < positions[task3_id] < positions[task1_id]  # priority 10 < 20 < 30


def test_postorder_schedule_deep_hierarchy(tmp_path, monkeypatch):
    """Test postorder scheduling with deeper hierarchy."""
    test_db = tmp_path / "postorder_deep.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create a deeper hierarchy: Root -> Child -> Grandchild -> GreatGrandchild
    root_id = repo.create_task("Root", priority=10)
    child_id = repo.create_task("Child", priority=20, parent_id=root_id)
    grandchild_id = repo.create_task("Grandchild", priority=30, parent_id=child_id)
    great_grandchild_id = repo.create_task("GreatGrandchild", priority=40, parent_id=grandchild_id)

    scheduled_tasks = list(postorder_schedule())
    scheduled_ids = [task["id"] for task in scheduled_tasks]

    assert len(scheduled_tasks) == 4

    # Get positions
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}

    # Verify post-order: deepest first
    assert positions[great_grandchild_id] < positions[grandchild_id]
    assert positions[grandchild_id] < positions[child_id]
    assert positions[child_id] < positions[root_id]


def test_postorder_schedule_multiple_branches(tmp_path, monkeypatch):
    """Test postorder scheduling with multiple complex branches."""
    test_db = tmp_path / "postorder_branches.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create complex hierarchy with multiple branches
    root = repo.create_task("Project", priority=10)

    # Frontend branch
    frontend = repo.create_task("Frontend", priority=20, parent_id=root)
    ui = repo.create_task("UI", priority=30, parent_id=frontend)
    js = repo.create_task("JavaScript", priority=40, parent_id=frontend)

    # Backend branch
    backend = repo.create_task("Backend", priority=50, parent_id=root)
    api = repo.create_task("API", priority=60, parent_id=backend)
    database = repo.create_task("Database", priority=70, parent_id=backend)

    scheduled_tasks = list(postorder_schedule())
    scheduled_ids = [task["id"] for task in scheduled_tasks]
    task_names = [task["name"] for task in scheduled_tasks]

    assert len(scheduled_tasks) == 7

    # Get positions
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}

    # Verify post-order within each branch
    assert positions[ui] < positions[frontend]
    assert positions[js] < positions[frontend]
    assert positions[api] < positions[backend]
    assert positions[database] < positions[backend]

    # Frontend and Backend should come before Project
    assert positions[frontend] < positions[root]
    assert positions[backend] < positions[root]

    # Verify dependencies
    root_task = next(task for task in scheduled_tasks if task["id"] == root)
    frontend_task = next(task for task in scheduled_tasks if task["id"] == frontend)
    backend_task = next(task for task in scheduled_tasks if task["id"] == backend)

    # Root should depend on its direct children
    assert frontend in root_task["dependencies"]
    assert backend in root_task["dependencies"]

    # Frontend should depend on its direct children
    assert ui in frontend_task["dependencies"]
    assert js in frontend_task["dependencies"]

    # Backend should depend on its direct children
    assert api in backend_task["dependencies"]
    assert database in backend_task["dependencies"]
