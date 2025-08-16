import os
from typing import Dict, Any

from fastapi.testclient import TestClient

from app.services.context_budget import apply_budget, PRIORITY_ORDER


def test_api_index_get_put(tmp_path, monkeypatch):
    # Isolate DB and index path
    test_db = tmp_path / "index_api.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    idx_path = tmp_path / "INDEX.md"
    monkeypatch.setenv("GLOBAL_INDEX_PATH", str(idx_path))

    from app.main import app

    with TestClient(app) as client:
        # Initially empty (file may not exist)
        r = client.get("/index")
        assert r.status_code == 200
        data = r.json()
        assert data["path"] == str(idx_path)
        assert data["content"] == ""

        # Write content
        payload: Dict[str, Any] = {"content": "Hello INDEX"}
        r = client.put("/index", json=payload)
        assert r.status_code == 200 and r.json().get("ok")
        assert r.json()["path"] == str(idx_path)

        # Verify persisted
        r = client.get("/index")
        assert r.status_code == 200
        data2 = r.json()
        assert data2["content"] == "Hello INDEX"
        assert os.path.exists(idx_path)
        with open(idx_path, "r", encoding="utf-8") as f:
            assert f.read() == "Hello INDEX"


def test_budget_orders_index_first():
    # Build a bundle that includes an index section and others
    sections = [
        {"task_id": 5, "name": "A", "short_name": "A", "kind": "manual", "content": "m"},
        {"task_id": 2, "name": "B", "short_name": "B", "kind": "sibling", "content": "s"},
        {"task_id": 0, "name": "INDEX.md", "short_name": "INDEX", "kind": "index", "content": "idx"},
        {"task_id": 1, "name": "Req", "short_name": "Req", "kind": "dep:requires", "content": "r"},
    ]
    bundle = {"task_id": 999, "sections": sections, "combined": ""}
    out = apply_budget(bundle, per_section_max=1000)

    kinds = [s["kind"] for s in out["sections"]]
    # Expected ordering according to PRIORITY_ORDER
    expected = [k for k in ("index", "dep:requires", "dep:refers", "retrieved", "sibling", "manual") if k in kinds]
    assert kinds == expected

    # Ensure budget metadata group aligns with PRIORITY_ORDER
    first = out["sections"][0]
    assert first["kind"] == "index"
    assert first["budget"]["group"] == PRIORITY_ORDER.index("index")
    assert first["budget"]["index"] == 0
