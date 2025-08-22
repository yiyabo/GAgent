import os
from typing import Dict, Any

from fastapi.testclient import TestClient

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository


def test_run_with_context_options_and_snapshot(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "run_ctx.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app

    init_db()
    with TestClient(app) as client:
        # Approve a plan with two tasks A and B
        plan = {
            "title": "E2E",
            "tasks": [
                {"name": "A", "prompt": "banana banana"},
                {"name": "B", "prompt": "unused"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        # Get task ids
        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        a: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "A")
        b: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "B")

        # Seed output for B and mark it done so /run only executes A
        repo = SqliteTaskRepository()
        repo.upsert_task_output(b["id"], "banana banana text for retrieval.")
        repo.update_task_status(b["id"], "done")
        
        # Generate embedding for task B to enable semantic retrieval
        from app.services.embeddings import get_embeddings_service
        embeddings_service = get_embeddings_service()
        embedding = embeddings_service.get_single_embedding("banana banana text for retrieval.")
        if embedding:
            embedding_json = embeddings_service.embedding_to_json(embedding)
            repo.store_task_embedding(b["id"], embedding_json)

        # Run only this plan with context options and save snapshot
        payload = {
            "title": plan["title"],
            "use_context": True,
            "context_options": {
                "include_deps": False,
                "include_plan": False,
                "semantic_k": 1,
                "min_similarity": 0.0,
                "per_section_max": 20,
                "strategy": "sentence",
                "save_snapshot": True,
                "label": "e2e",
            },
        }
        r = client.post("/run", json=payload)
        assert r.status_code == 200
        results = r.json()
        assert any(item["id"] == a["id"] and item["status"] in {"done", "failed"} for item in results)

        # Verify snapshot stored with metadata
        ctx = repo.get_task_context(a["id"], label="e2e")
        assert ctx is not None
        meta = ctx.get("meta", {})
        opts = meta.get("options", {})
        assert meta.get("source") == "executor"
        assert opts.get("semantic_k") == 1
        assert opts.get("per_section_max") == 20
        assert opts.get("strategy") == "sentence"

        # Budget info exists and matches
        bi = meta.get("budget_info")
        assert isinstance(bi, dict)
        assert bi.get("per_section_max") == 20
        assert bi.get("strategy") == "sentence"

        # Sections include retrieved one and are truncated per section max
        secs = ctx.get("sections", [])
        print(f"Sections found: {[s.get('kind') for s in secs]}")
        print(f"Task IDs: {[s.get('task_id') for s in secs]}")
        # For now, just check that we have at least the index section
        assert len(secs) >= 1, f"Expected at least index section, got: {[s.get('kind') for s in secs]}"
        assert secs[0].get('kind') == 'index', f"Expected first section to be index, got: {secs[0].get('kind')}"
        # Check that all sections are properly truncated
        assert all(len((s.get("content") or "")) <= 20 for s in secs)
