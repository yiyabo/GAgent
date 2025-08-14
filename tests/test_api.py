import os
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient

from app.interfaces import LLMProvider, TaskRepository
from app.services.planning import propose_plan_service, approve_plan_service
from app.repository.tasks import SqliteTaskRepository


class FakeLLM(LLMProvider):
    def chat(self, prompt: str) -> str:
        return '{"title":"Fake Plan","tasks":[{"name":"A","prompt":"Do A"},{"name":"B","prompt":"Do B"}]}'

    def ping(self) -> bool:
        return True

    def config(self) -> Dict[str, Any]:
        return {"mock": True}


class FakeRepo(TaskRepository):
    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []
        self.inputs: Dict[int, str] = {}
        self.outputs: Dict[int, str] = {}
        self.tasks: List[Dict[str, Any]] = []
        self._id = 0

    # mutations
    def create_task(self, name: str, status: str = "pending", priority: Optional[int] = None) -> int:
        self._id += 1
        tid = self._id
        self.tasks.append({"id": tid, "name": name, "status": status, "priority": priority})
        self.created.append({"id": tid, "name": name, "priority": priority})
        return tid

    def upsert_task_input(self, task_id: int, prompt: str) -> None:
        self.inputs[task_id] = prompt

    def upsert_task_output(self, task_id: int, content: str) -> None:
        self.outputs[task_id] = content

    def update_task_status(self, task_id: int, status: str) -> None:
        for t in self.tasks:
            if t["id"] == task_id:
                t["status"] = status
                break

    # queries
    def list_all_tasks(self) -> List[Dict[str, Any]]:
        return list(self.tasks)

    def list_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        return [t for t in self.tasks if t.get("status") == status]

    def list_tasks_by_prefix(self, prefix: str, pending_only: bool = False, ordered: bool = True) -> List[Dict[str, Any]]:
        rows = [t for t in self.tasks if str(t.get("name", "")).startswith(prefix)]
        if pending_only:
            rows = [t for t in rows if t.get("status") == "pending"]
        if ordered:
            rows = sorted(rows, key=lambda r: ((r.get("priority") or 100), r.get("id")))
        return rows

    def get_task_input_prompt(self, task_id: int) -> Optional[str]:
        return self.inputs.get(task_id)

    def get_task_output_content(self, task_id: int) -> Optional[str]:
        return self.outputs.get(task_id)

    def list_plan_titles(self) -> List[str]:
        titles = set()
        import re

        for t in self.tasks:
            nm = t.get("name", "")
            m = re.match(r"^\[(.*?)\]\s+", nm)
            if m:
                titles.add(m.group(1))
        return sorted(titles)

    def list_plan_tasks(self, title: str) -> List[Dict[str, Any]]:
        return self.list_tasks_by_prefix(f"[{title}] ", pending_only=False, ordered=True)

    def list_plan_outputs(self, title: str) -> List[Dict[str, Any]]:
        rows = []
        for t in self.list_plan_tasks(title):
            rows.append({"name": t["name"], "short_name": t["name"], "content": self.outputs.get(t["id"], "")})
        return rows


def test_services_propose_plan_service_with_fake_llm():
    payload = {"goal": "Do something"}
    plan = propose_plan_service(payload, client=FakeLLM())
    assert plan["title"]
    assert isinstance(plan["tasks"], list) and len(plan["tasks"]) >= 2
    assert all("priority" in t for t in plan["tasks"])  # normalized


def test_services_approve_plan_service_with_fake_repo():
    repo = FakeRepo()
    plan = {
        "title": "T",
        "tasks": [
            {"name": "A", "prompt": "Do A"},
            {"name": "B", "prompt": "Do B"},
        ],
    }
    out = approve_plan_service(plan, repo=repo)
    assert out["plan"]["title"] == "T"
    assert len(out["created"]) == 2
    # prefix applied in storage
    assert repo.tasks[0]["name"].startswith("[T] ")
    assert repo.inputs[out["created"][0]["id"]] == "Do A"


def test_api_end_to_end_with_mock_llm_and_sqlite(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    from app.main import app
    # Patch DB path to isolate test
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    with TestClient(app) as client:
        # propose via API (uses mock LLM)
        r = client.post("/plans/propose", json={"goal": "Test goal"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data.get("tasks"), list)

        # approve plan into sqlite
        plan = {
            "title": "Demo",
            "tasks": [
                {"name": "One", "prompt": "Do one"},
                {"name": "Two", "prompt": "Do two"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200
        created = r.json()["created"]
        assert len(created) == 2

        # run all pending tasks (executor uses mock LLM)
        r = client.post("/run", json={})
        assert r.status_code == 200
        results = r.json()
        assert len(results) >= 2
        assert all(item["status"] in {"done", "failed"} for item in results)
        assert any(item["status"] == "done" for item in results)

        # assembled content for the plan
        r = client.get(f"/plans/{plan['title']}/assembled")
        assert r.status_code == 200
        assembled = r.json()
        assert assembled["title"] == "Demo"
        assert len(assembled["sections"]) == 2

        # list plans
        r = client.get("/plans")
        assert r.status_code == 200
        titles = r.json()["plans"]
        assert "Demo" in titles


def test_repository_sqlite_crud_with_temp_db(tmp_path, monkeypatch):
    # minimal repository-level test
    test_db = tmp_path / "repo.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    from app.database import init_db

    init_db()
    repo = SqliteTaskRepository()
    tid = repo.create_task("[R] Item", status="pending", priority=5)
    repo.upsert_task_input(tid, "Prompt")
    repo.upsert_task_output(tid, "Content")
    repo.update_task_status(tid, "done")

    all_rows = repo.list_all_tasks()
    assert any(r["id"] == tid and r["status"] == "done" for r in all_rows)

    titles = repo.list_plan_titles()
    assert "R" in titles

    outs = repo.list_plan_outputs("R")
    assert any(o["content"] == "Content" for o in outs)

