import os

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / f"test_{os.urandom(4).hex()}.db"
    monkeypatch.setattr("app.database.DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_MOCK", "1")
    init_db()
    with TestClient(app) as c:
        yield c


def test_api_snapshot_lifecycle(client):
    # 1. Create a task
    r = client.post("/tasks", json={"name": "A", "task_type": "atomic"})
    assert r.status_code == 200
    task_id = r.json()["id"]

    # 2. Verify non-existent snapshot returns 404
    r_get_none = client.get(f"/tasks/{task_id}/context/snapshots/L2")
    assert r_get_none.status_code == 404

    # 3. Create snapshot using POST
    r_post = client.post(
        f"/tasks/{task_id}/context/snapshots", json={"label": "L2", "content": "hello"}
    )
    assert r_post.status_code == 200
    assert r_post.json().get("context_created") is True

    # 4. Verify creation with GET
    r_get = client.get(f"/tasks/{task_id}/context/snapshots/L2")
    assert r_get.status_code == 200
    assert r_get.json().get("combined") == "hello"
    assert r_get.json().get("label") == "L2"

    # 5. Update the snapshot using PUT
    r_put = client.put(
        f"/tasks/{task_id}/context/snapshots/L2",
        json={"content": "world", "meta": {"source": "update"}},
    )
    assert r_put.status_code == 200
    assert r_put.json().get("context_updated") is True

    # 6. Verify update with GET
    r_get_updated = client.get(f"/tasks/{task_id}/context/snapshots/L2")
    assert r_get_updated.status_code == 200
    assert r_get_updated.json().get("combined") == "world"
    assert r_get_updated.json().get("meta") == {"source": "update"}

    # 7. Delete the snapshot
    r_del = client.delete(f"/tasks/{task_id}/context/snapshots/L2")
    assert r_del.status_code == 200
    assert r_del.json().get("context_deleted") is True

    # 8. Verify deletion returns 404
    r_get_after_del = client.get(f"/tasks/{task_id}/context/snapshots/L2")
    assert r_get_after_del.status_code == 404
