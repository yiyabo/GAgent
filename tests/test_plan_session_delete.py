import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.services.plan_session import plan_session_manager


@pytest.fixture
def repo(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()
    return SqliteTaskRepository()


def test_plan_session_delete_removes_subtree(repo):
    plan_id = repo.create_plan("Test Plan")

    root_id = repo.create_task("Root Task")
    repo.link_task_to_plan(plan_id, root_id)

    child_id = repo.create_task("Child Task", parent_id=root_id)
    repo.link_task_to_plan(plan_id, child_id)

    grandchild_id = repo.create_task("Grandchild Task", parent_id=child_id)
    repo.link_task_to_plan(plan_id, grandchild_id)

    session = plan_session_manager.activate_plan(plan_id)

    deletion_snapshot = session.delete_task(child_id)

    assert child_id in deletion_snapshot["removed_ids"]
    assert grandchild_id in deletion_snapshot["removed_ids"]
    remaining_ids = {task["id"] for task in session.list_tasks()}
    assert child_id not in remaining_ids
    assert grandchild_id not in remaining_ids

    session.commit()

    assert repo.get_task_info(child_id) is None
    assert repo.get_task_info(grandchild_id) is None

    plan_tasks = repo.get_plan_tasks(plan_id)
    plan_task_ids = {task["id"] for task in plan_tasks}
    assert root_id in plan_task_ids

    plan_session_manager.release_session(plan_id)
