import pytest
from unittest.mock import patch, Mock

from app.database import init_db
from app.executor import execute_task_with_evaluation
from app.repository.tasks import SqliteTaskRepository

@pytest.fixture
def repo(tmp_path, monkeypatch):
    db_path = tmp_path / "test_integration.db"
    monkeypatch.setattr("app.database.DB_PATH", str(db_path))
    init_db()
    return SqliteTaskRepository()

@patch("app.executor.execute_task")
def test_complete_evaluation_system(mock_execute_task, repo):
    mock_execute_task.return_value = "done"

    task_id = repo.create_task(name="Test Task", status="pending", task_type="atomic")
    task = repo.get_task_info(task_id)

    # Ensure there is an output before asserting
    repo.upsert_task_output(task_id, "mock output")

    result = execute_task_with_evaluation(task=task, repo=repo)

    assert result.status == "done"
    assert result.iterations == 1

    task_output = repo.get_task_output_content(task_id)
    assert task_output == "mock output"
