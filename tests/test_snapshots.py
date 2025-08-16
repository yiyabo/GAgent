import os
from typing import Dict, Any

from fastapi.testclient import TestClient


def test_api_snapshots_list_get_via_run(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "snap.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app

    with TestClient(app) as client:
        # Approve a simple plan
        plan = {
            "title": "SN",
            "tasks": [
                {"name": "A", "prompt": "Do A"},
                {"name": "B", "prompt": "Do B"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        # Run with context and save snapshot under label L1
        payload_run = {
            "title": plan["title"],
            "use_context": True,
            "context_options": {"save_snapshot": True, "label": "L1"},
        }
        r = client.post("/run", json=payload_run)
        assert r.status_code == 200

        # Get task IDs
        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        a: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "A")

        # List snapshots for task A
        r = client.get(f"/tasks/{a['id']}/context/snapshots")
        assert r.status_code == 200
        listing = r.json()
        assert listing["task_id"] == a["id"]
        labels = [s.get("label") for s in listing.get("snapshots", [])]
        assert "L1" in labels

        # Get snapshot by label
        r = client.get(f"/tasks/{a['id']}/context/snapshots/L1")
        assert r.status_code == 200
        snap = r.json()
        assert snap.get("label") == "L1"
        assert isinstance(snap.get("combined", ""), str)
        assert isinstance(snap.get("sections", []), list)


def test_api_snapshot_get_404(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "snap2.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app

    with TestClient(app) as client:
        plan = {"title": "S2", "tasks": [{"name": "A", "prompt": "x"}]}
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        a = next(t for t in tasks if t["short_name"] == "A")

        r = client.get(f"/tasks/{a['id']}/context/snapshots/NOPE")
        assert r.status_code == 404
