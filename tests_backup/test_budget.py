import os
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from app.services.context_budget import apply_budget


def test_apply_budget_sentence_strategy():
    # Prepare a dummy bundle with one section
    bundle = {
        "task_id": 1,
        "sections": [
            {
                "task_id": 2,
                "name": "S",
                "short_name": "S",
                "kind": "dep:requires",
                "content": "Hello world. Second sentence here.",
            }
        ],
        "combined": "",
    }
    # Limit within first sentence; should cut at sentence boundary when strategy='sentence'
    out = apply_budget(bundle, max_chars=13, strategy="sentence")
    sec = out["sections"][0]
    assert sec["budget"]["strategy"] == "sentence"
    assert sec["budget"]["truncated"] is True
    # Expect to end with a period by picking the boundary
    assert sec["content"].endswith(".")
    assert len(sec["content"]) <= 13


def test_apply_budget_priority_sorting():
    # Prepare mixed kinds in random order; applying any budget should sort by priority
    sections: List[Dict[str, Any]] = [
        {"task_id": 4, "name": "M", "short_name": "M", "kind": "manual", "content": "mmmm"},
        {"task_id": 3, "name": "S", "short_name": "S", "kind": "sibling", "content": "ssss"},
        {"task_id": 1, "name": "R", "short_name": "R", "kind": "dep:requires", "content": "rrrr"},
        {"task_id": 2, "name": "F", "short_name": "F", "kind": "dep:refers", "content": "ffff"},
    ]
    bundle = {"task_id": 99, "sections": sections, "combined": ""}
    # Use a large per-section cap to avoid truncation but still trigger budgeting (and sorting)
    out = apply_budget(bundle, per_section_max=10**6)
    kinds = [s["kind"] for s in out["sections"]]
    assert kinds == [
        "dep:requires",
        "dep:refers",
        "sibling",
        "manual",
    ]


def test_executor_run_saves_context_snapshot_with_budget_info(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    from app.database import init_db
    from app.main import app
    from app.repository.tasks import SqliteTaskRepository

    # Isolate DB
    test_db = tmp_path / "budget_exec.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()

    repo = SqliteTaskRepository()

    with TestClient(app) as client:
        # Approve a simple plan with two tasks
        plan = {
            "title": "BUD",
            "tasks": [
                {"name": "One", "prompt": "Do one"},
                {"name": "Two", "prompt": "Do two"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        # Run with context enabled and snapshot saving
        payload = {
            "title": plan["title"],
            "use_context": True,
            "context_options": {
                "include_plan": False,
                "save_snapshot": True,
                "label": "t1",
                "max_chars": 8,
            },
        }
        r = client.post("/run", json=payload)
        assert r.status_code == 200
        results = r.json()
        assert isinstance(results, list) and len(results) >= 1

        # Pick one task id and verify snapshot exists with budget_info
        tid = results[0]["id"]
        snap = repo.get_task_context(tid, label="t1")
        assert snap is not None
        meta = snap.get("meta") or {}
        # options echoed
        assert (meta.get("options") or {}).get("max_chars") in (8, None)  # may be normalized to None if not int
        # budget_info exists when max_chars provided
        bi = meta.get("budget_info")
        assert isinstance(bi, dict)
        assert bi.get("max_chars") == 8


def test_apply_budget_zero_max_chars_makes_all_empty():
    bundle = {
        "task_id": 1,
        "sections": [
            {"task_id": 2, "name": "A", "short_name": "A", "kind": "dep:requires", "content": "ABCDE"},
            {"task_id": 3, "name": "B", "short_name": "B", "kind": "dep:refers", "content": "12345"},
        ],
        "combined": "",
    }
    out = apply_budget(bundle, max_chars=0)
    for s in out["sections"]:
        assert s["content"] == ""
        assert s["budget"]["truncated"] is True
    assert out["budget_info"]["total_new_chars"] == 0


def test_apply_budget_negative_caps_treated_as_zero():
    bundle = {
        "task_id": 1,
        "sections": [
            {"task_id": 2, "name": "A", "short_name": "A", "kind": "dep:requires", "content": "hello"},
            {"task_id": 3, "name": "B", "short_name": "B", "kind": "dep:refers", "content": "world"},
        ],
        "combined": "",
    }
    out = apply_budget(bundle, max_chars=-10)
    for s in out["sections"]:
        assert s["content"] == ""
        assert s["budget"]["truncated"] is True
    assert out["budget_info"]["total_new_chars"] == 0


def test_apply_budget_per_section_zero_caps():
    bundle = {
        "task_id": 1,
        "sections": [
            {"task_id": 2, "name": "A", "short_name": "A", "kind": "dep:requires", "content": "foo"},
            {"task_id": 3, "name": "B", "short_name": "B", "kind": "dep:refers", "content": "bar"},
        ],
        "combined": "",
    }
    out = apply_budget(bundle, per_section_max=0)
    for s in out["sections"]:
        assert s["content"] == ""
        assert s["budget"]["truncated"] is True
    assert out["budget_info"]["total_new_chars"] == 0


def test_apply_budget_both_caps_interplay_and_ordering():
    # Two equal-length sections -> ordering/priorities decide distribution
    sections = [
        {"task_id": 10, "name": "R", "short_name": "R", "kind": "dep:requires", "content": "abcd"},
        {"task_id": 11, "name": "F", "short_name": "F", "kind": "dep:refers", "content": "efgh"},
    ]
    bundle = {"task_id": 9, "sections": sections, "combined": ""}
    out = apply_budget(bundle, max_chars=5, per_section_max=3)
    s0, s1 = out["sections"][0], out["sections"][1]
    assert s0["content"] == "abc"  # first takes up to per_section_max=3
    assert s1["content"] == "ef"  # remaining=2
    assert out["budget_info"]["total_new_chars"] == 5


def test_apply_budget_handles_empty_sections_stably():
    sections = [
        {"task_id": 1, "name": "A", "short_name": "A", "kind": "dep:requires", "content": ""},
        {"task_id": 2, "name": "B", "short_name": "B", "kind": "dep:refers", "content": "hello"},
    ]
    bundle = {"task_id": 0, "sections": sections, "combined": ""}
    out = apply_budget(bundle, max_chars=3)
    s0, s1 = out["sections"][0], out["sections"][1]
    assert s0["content"] == "" and s0["budget"]["truncated"] is False
    assert s1["content"] == "hel" and s1["budget"]["truncated"] is True
    assert out["budget_info"]["total_new_chars"] == 3
