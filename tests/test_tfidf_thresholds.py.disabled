import os
import sys
from typing import Any, Dict

from fastapi.testclient import TestClient


def test_api_tfidf_min_score_threshold_blocks_retrieval(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "api_tfidf_min.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app
    from app.repository.tasks import SqliteTaskRepository

    with TestClient(app) as client:
        plan = {
            "title": "TH",
            "tasks": [
                {"name": "A", "prompt": "banana banana"},
                {"name": "B", "prompt": "other"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        # Get IDs and attach output to B
        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        a: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "A")
        b: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "B")

        repo = SqliteTaskRepository()
        repo.upsert_task_output(b["id"], "banana banana text for retrieval")

        # Low threshold → retrieval occurs
        payload = {"include_deps": False, "include_plan": False, "tfidf_k": 1, "tfidf_min_score": 0.0}
        r = client.post(f"/tasks/{a['id']}/context/preview", json=payload)
        assert r.status_code == 200
        preview = r.json()
        assert any(s.get("kind") == "retrieved" and s.get("task_id") == b["id"] for s in preview.get("sections", []))

        # Very high threshold → no retrieval
        payload = {"include_deps": False, "include_plan": False, "tfidf_k": 1, "tfidf_min_score": 1e9}
        r = client.post(f"/tasks/{a['id']}/context/preview", json=payload)
        assert r.status_code == 200
        preview = r.json()
        assert not any(s.get("kind") == "retrieved" for s in preview.get("sections", []))


def test_api_tfidf_max_candidates_zero_blocks_retrieval(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "api_tfidf_maxc.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app
    from app.repository.tasks import SqliteTaskRepository

    with TestClient(app) as client:
        plan = {
            "title": "TC",
            "tasks": [
                {"name": "A", "prompt": "banana banana"},
                {"name": "B", "prompt": "other"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        a: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "A")
        b: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "B")

        repo = SqliteTaskRepository()
        repo.upsert_task_output(b["id"], "banana banana text for retrieval")

        # With tfidf_max_candidates=0 we should never retrieve anything
        payload = {
            "include_deps": False,
            "include_plan": False,
            "tfidf_k": 1,
            "tfidf_max_candidates": 0,
        }
        r = client.post(f"/tasks/{a['id']}/context/preview", json=payload)
        assert r.status_code == 200
        preview = r.json()
        assert not any(s.get("kind") == "retrieved" for s in preview.get("sections", []))


def test_cli_tfidf_flags_threaded_into_payload(tmp_path, monkeypatch, capsys):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "cli.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    import agent_cli

    captured = {}

    def fake_run_tasks(payload):
        captured["payload"] = payload
        return []

    def fake_get_plan_assembled(title):
        return {"title": title, "sections": []}

    monkeypatch.setattr(agent_cli, "run_tasks", fake_run_tasks, raising=False)
    monkeypatch.setattr(agent_cli, "get_plan_assembled", fake_get_plan_assembled, raising=False)

    out_md = tmp_path / "out.md"
    argv = [
        "agent_cli.py",
        "--execute-only",
        "--title",
        "TT",
        "--use-context",
        "--tfidf-k",
        "3",
        "--tfidf-min-score",
        "0.123",
        "--tfidf-max-candidates",
        "7",
        "--output",
        str(out_md),
    ]
    monkeypatch.setattr(sys, "argv", argv, raising=False)

    agent_cli.main()

    # Ensure payload captured
    payload = captured.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("title") == "TT"
    assert payload.get("use_context") is True
    co = payload.get("context_options")
    assert isinstance(co, dict)
    assert co.get("tfidf_k") == 3
    assert abs(float(co.get("tfidf_min_score")) - 0.123) < 1e-9
    assert co.get("tfidf_max_candidates") == 7

    # Output file should be created by CLI
    assert out_md.exists(), "CLI should write assembled output"
