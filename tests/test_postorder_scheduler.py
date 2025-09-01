import pytest
import os
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import postorder_schedule


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Fixture to provide a clean database and repository for each test."""
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()
    return SqliteTaskRepository()


def test_postorder_schedule_hierarchy(repo):
    """Test that postorder scheduling works correctly with a task hierarchy."""
    plan_id = repo.create_plan("Test Plan")

    # Create a hierarchy: Root -> Child1, Child2 -> Grandchild1, Grandchild2
    root_id = repo.create_task("Root Task", priority=10)
    repo.link_task_to_plan(plan_id, root_id)

    child1_id = repo.create_task("Child Task 1", priority=20, parent_id=root_id)
    repo.link_task_to_plan(plan_id, child1_id)

    child2_id = repo.create_task("Child Task 2", priority=30, parent_id=root_id)
    repo.link_task_to_plan(plan_id, child2_id)

    grandchild1_id = repo.create_task("Grandchild Task 1", priority=40, parent_id=child1_id)
    repo.link_task_to_plan(plan_id, grandchild1_id)

    grandchild2_id = repo.create_task("Grandchild Task 2", priority=50, parent_id=child2_id)
    repo.link_task_to_plan(plan_id, grandchild2_id)

    # Get the schedule
    scheduled_tasks = postorder_schedule(plan_id)
    scheduled_ids = [task['id'] for task in scheduled_tasks]

    # Verify post-order: grandchildren first, then children, then root
    assert len(scheduled_tasks) == 5
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}
    assert positions[grandchild1_id] < positions[child1_id]
    assert positions[grandchild2_id] < positions[child2_id]
    assert positions[child1_id] < positions[root_id]
    assert positions[child2_id] < positions[root_id]


def test_postorder_schedule_isolates_by_plan_id(repo):
    """Test postorder scheduling correctly isolates tasks by plan_id."""
    plan1_id = repo.create_plan("Plan 1")
    plan2_id = repo.create_plan("Plan 2")

    # Create tasks for both plans
    p1_root = repo.create_task("P1 Root", priority=10)
    repo.link_task_to_plan(plan1_id, p1_root)
    p1_child = repo.create_task("P1 Child", priority=20, parent_id=p1_root)
    repo.link_task_to_plan(plan1_id, p1_child)
    
    p2_task = repo.create_task("P2 Task", priority=30)
    repo.link_task_to_plan(plan2_id, p2_task)

    # Schedule for Plan 1
    scheduled_tasks = postorder_schedule(plan1_id)
    scheduled_ids = [task['id'] for task in scheduled_tasks]

    # Should only include tasks from Plan 1
    assert len(scheduled_tasks) == 2
    assert p1_root in scheduled_ids
    assert p1_child in scheduled_ids
    assert p2_task not in scheduled_ids
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}
    assert positions[p1_child] < positions[p1_root]


def test_postorder_schedule_empty_hierarchy(repo):
    """Test postorder scheduling with no tasks."""
    plan_id = repo.create_plan("Empty Plan")
    scheduled_tasks = postorder_schedule(plan_id)
    assert len(scheduled_tasks) == 0


def test_postorder_schedule_flat_tasks(repo):
    """Test postorder scheduling with flat (no hierarchy) tasks."""
    plan_id = repo.create_plan("Flat Plan")

    # Create flat tasks
    task1_id = repo.create_task("Task 1", priority=30)
    repo.link_task_to_plan(plan_id, task1_id)
    task2_id = repo.create_task("Task 2", priority=10)
    repo.link_task_to_plan(plan_id, task2_id)
    task3_id = repo.create_task("Task 3", priority=20)
    repo.link_task_to_plan(plan_id, task3_id)

    scheduled_tasks = postorder_schedule(plan_id)
    scheduled_ids = [task['id'] for task in scheduled_tasks]

    assert len(scheduled_tasks) == 3
    assert scheduled_ids == [task2_id, task3_id, task1_id]


def test_postorder_schedule_deep_hierarchy(repo):
    """Test postorder scheduling with a deeper hierarchy."""
    plan_id = repo.create_plan("Deep Plan")

    root_id = repo.create_task("Root", priority=10)
    repo.link_task_to_plan(plan_id, root_id)
    child_id = repo.create_task("Child", priority=20, parent_id=root_id)
    repo.link_task_to_plan(plan_id, child_id)
    grandchild_id = repo.create_task("Grandchild", priority=30, parent_id=child_id)
    repo.link_task_to_plan(plan_id, grandchild_id)
    great_grandchild_id = repo.create_task("GreatGrandchild", priority=40, parent_id=grandchild_id)
    repo.link_task_to_plan(plan_id, great_grandchild_id)

    scheduled_tasks = postorder_schedule(plan_id)
    scheduled_ids = [task['id'] for task in scheduled_tasks]

    assert len(scheduled_tasks) == 4
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}
    assert positions[great_grandchild_id] < positions[grandchild_id]
    assert positions[grandchild_id] < positions[child_id]
    assert positions[child_id] < positions[root_id]


def test_postorder_schedule_multiple_branches(repo):
    """Test postorder scheduling with multiple complex branches."""
    plan_id = repo.create_plan("Branching Plan")

    root = repo.create_task("Project", priority=10)
    repo.link_task_to_plan(plan_id, root)
    
    frontend = repo.create_task("Frontend", priority=30, parent_id=root)
    repo.link_task_to_plan(plan_id, frontend)
    ui = repo.create_task("UI", priority=30, parent_id=frontend)
    repo.link_task_to_plan(plan_id, ui)
    js = repo.create_task("JavaScript", priority=20, parent_id=frontend)
    repo.link_task_to_plan(plan_id, js)
    
    backend = repo.create_task("Backend", priority=20, parent_id=root)
    repo.link_task_to_plan(plan_id, backend)
    api = repo.create_task("API", priority=20, parent_id=backend)
    repo.link_task_to_plan(plan_id, api)
    database = repo.create_task("Database", priority=30, parent_id=backend)
    repo.link_task_to_plan(plan_id, database)

    scheduled_tasks = postorder_schedule(plan_id)
    scheduled_ids = [task['id'] for task in scheduled_tasks]

    assert len(scheduled_tasks) == 7
    positions = {task_id: scheduled_ids.index(task_id) for task_id in scheduled_ids}
    assert positions[js] < positions[ui] < positions[frontend]
    assert positions[api] < positions[database] < positions[backend]
    assert positions[backend] < positions[frontend]
    assert positions[frontend] < positions[root]
    assert positions[backend] < positions[root]
