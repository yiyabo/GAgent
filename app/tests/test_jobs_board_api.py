from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import job_routes
from app.services.request_principal import LEGACY_LOCAL_OWNER_ID


def _create_board_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
  CREATE TABLE chat_sessions (
  id TEXT PRIMARY KEY,
  owner_id TEXT,
  plan_id INTEGER
  );

  CREATE TABLE chat_action_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  owner_id TEXT,
  user_message TEXT,
  plan_id INTEGER,
  status TEXT,
  structured_json TEXT,
  created_at TEXT,
  started_at TEXT,
  finished_at TEXT
  );

  CREATE TABLE plan_decomposition_job_index (
  job_id TEXT PRIMARY KEY,
  owner_id TEXT,
  plan_id INTEGER NOT NULL,
  job_type TEXT NOT NULL DEFAULT 'plan_decompose',
  created_at TEXT
  );
  """
    )
    return conn


def _insert_action_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    session_id: str | None,
    plan_id: int | None,
    status: str,
    action_name: str,
    created_at: str,
    owner_id: str = LEGACY_LOCAL_OWNER_ID,
) -> None:
    structured_json = json.dumps(
        {
            "actions": [
                {
                    "kind": "tool_operation",
                    "name": action_name,
                    "parameters": {"task": f"execute {action_name}"},
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    conn.execute(
        """
  INSERT INTO chat_action_runs (
  id, session_id, owner_id, user_message, plan_id, status, structured_json, created_at, started_at, finished_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
  """,
        (run_id, session_id, owner_id, "run task", plan_id,
          status, structured_json, created_at),
    )
    conn.commit()


def _insert_job_index(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    plan_id: int,
    job_type: str = "plan_decompose",
    created_at: str,
    owner_id: str = LEGACY_LOCAL_OWNER_ID,
) -> None:
    conn.execute(
        """
  INSERT INTO plan_decomposition_job_index (job_id, owner_id, plan_id, job_type, created_at)
  VALUES (?, ?, ?, ?, ?)
  """,
        (job_id, owner_id, plan_id, job_type, created_at),
    )
    conn.commit()


@pytest.fixture
def board_api_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Tuple[sqlite3.Connection, Dict[str, Dict[str, Any]], TestClient]]:
    conn = _create_board_db()

    @contextmanager
    def _get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    payloads: Dict[str, Dict[str, Any]] = {}

    def _fake_get_job_payload(
        job_id: str, include_logs: bool = False
    ) -> Dict[str, Any] | None:
        _ = include_logs
        return payloads.get(job_id)

    monkeypatch.setattr(job_routes, "get_db", _get_db)
    monkeypatch.setattr(
        job_routes.plan_decomposition_jobs, "get_job_payload", _fake_get_job_payload
    )

    app = FastAPI()
    app.include_router(job_routes.job_router)
    client = TestClient(app)

    try:
        yield conn, payloads, client
    finally:
        client.close()
        conn.close()


def test_jobs_board_classifies_items_and_exposes_progress_fields(
    board_api_env: Tuple[sqlite3.Connection, Dict[str, Dict[str, Any]], TestClient],
) -> None:
    conn, payloads, client = board_api_env

    _insert_action_run(
        conn,
        run_id="act_phage_1",
        session_id="sess-a",
        plan_id=None,
        status="pending",
        action_name="phagescope",
        created_at="2026-02-19 10:00:00",
    )
    _insert_action_run(
        conn,
        run_id="act_claude_1",
        session_id="sess-a",
        plan_id=42,
        status="queued",
        action_name="code_executor",
        created_at="2026-02-19 10:01:00",
    )
    _insert_job_index(
        conn,
        job_id="job_decompose_1",
        plan_id=42,
        created_at="2026-02-19 10:02:00",
    )

    payloads.update(
        {
            "act_phage_1": {
                "job_id": "act_phage_1",
                "job_type": "chat_action",
                "status": "running",
                "stats": {
                    "tool_progress": {
                        "taskid": "remote-42",
                        "status": "running",
                        "counts": {"done": 2, "total": 8},
                    }
                },
            },
            "act_claude_1": {
                "job_id": "act_claude_1",
                "job_type": "chat_action",
                "status": "queued",
            },
            "job_decompose_1": {
                "job_id": "job_decompose_1",
                "job_type": "plan_decompose",
                "status": "running",
                "params": {"node_budget": 10},
                "stats": {"consumed_budget": 3},
                "metadata": {"target_task_name": ""},
            },
        }
    )

    response = client.get("/jobs/board", params={"limit": 10})
    assert response.status_code == 200
    payload = response.json()
    groups = payload["groups"]

    assert set(groups.keys()) == {"task_creation", "phagescope", "code_executor"}
    assert payload["total"] == 3
    assert payload["generated_at"]

    phage_item = groups["phagescope"]["items"][0]
    assert phage_item["job_id"] == "act_phage_1"
    assert phage_item["progress_percent"] == 25
    assert phage_item["progress_status"] == "running"
    assert phage_item["progress_text"] == "2/8"
    assert phage_item["done_steps"] == 2
    assert phage_item["total_steps"] == 8
    assert phage_item["taskid"] == "remote-42"
    assert phage_item["remote_status"] == "running"

    claude_item = groups["code_executor"]["items"][0]
    assert claude_item["job_id"] == "act_claude_1"
    assert claude_item["progress_percent"] == 0
    assert claude_item["progress_status"] == "queued"

    creation_item = groups["task_creation"]["items"][0]
    assert creation_item["job_id"] == "job_decompose_1"
    assert creation_item["label"] == "Task Creation/Decomposition"
    assert creation_item["progress_percent"] == 30
    assert creation_item["progress_status"] == "running"
    assert creation_item["progress_text"] == "3/10"


def test_jobs_board_filters_finished_items_when_requested(
    board_api_env: Tuple[sqlite3.Connection, Dict[str, Dict[str, Any]], TestClient],
) -> None:
    conn, payloads, client = board_api_env

    _insert_action_run(
        conn,
        run_id="act_claude_done",
        session_id="sess-b",
        plan_id=7,
        status="pending",
        action_name="code_executor",
        created_at="2026-02-19 11:00:00",
    )
    _insert_action_run(
        conn,
        run_id="act_phage_running",
        session_id="sess-b",
        plan_id=7,
        status="pending",
        action_name="phagescope",
        created_at="2026-02-19 11:01:00",
    )
    _insert_job_index(
        conn,
        job_id="job_decompose_done",
        plan_id=7,
        created_at="2026-02-19 11:02:00",
    )
    _insert_job_index(
        conn,
        job_id="job_decompose_running",
        plan_id=7,
        created_at="2026-02-19 11:03:00",
    )

    payloads.update(
        {
            "act_claude_done": {
                "job_id": "act_claude_done",
                "job_type": "chat_action",
                "status": "succeeded",
            },
            "act_phage_running": {
                "job_id": "act_phage_running",
                "job_type": "chat_action",
                "status": "running",
            },
            "job_decompose_done": {
                "job_id": "job_decompose_done",
                "job_type": "plan_decompose",
                "status": "failed",
            },
            "job_decompose_running": {
                "job_id": "job_decompose_running",
                "job_type": "plan_decompose",
                "status": "running",
            },
        }
    )

    response = client.get(
        "/jobs/board",
        params={"plan_id": 7, "include_finished": "false"},
    )
    assert response.status_code == 200
    groups = response.json()["groups"]

    assert groups["code_executor"]["items"] == []
    assert groups["phagescope"]["items"][0]["job_id"] == "act_phage_running"
    assert groups["task_creation"]["items"][0]["job_id"] == "job_decompose_running"


def test_jobs_board_uses_session_plan_binding_for_task_creation(
    board_api_env: Tuple[sqlite3.Connection, Dict[str, Dict[str, Any]], TestClient],
) -> None:
    conn, payloads, client = board_api_env

    conn.execute(
        "INSERT INTO chat_sessions (id, owner_id, plan_id) VALUES (?, ?, ?)",
        ("sess-z", LEGACY_LOCAL_OWNER_ID, 77),
    )
    conn.commit()

    _insert_action_run(
        conn,
        run_id="act_session_phage",
        session_id="sess-z",
        plan_id=None,
        status="pending",
        action_name="phagescope",
        created_at="2026-02-19 12:00:00",
    )
    _insert_job_index(
        conn,
        job_id="job_plan_77",
        plan_id=77,
        created_at="2026-02-19 12:01:00",
    )
    _insert_job_index(
        conn,
        job_id="job_plan_88",
        plan_id=88,
        created_at="2026-02-19 12:02:00",
    )

    payloads.update(
        {
            "act_session_phage": {
                "job_id": "act_session_phage",
                "job_type": "chat_action",
                "status": "running",
            },
            "job_plan_77": {
                "job_id": "job_plan_77",
                "job_type": "plan_decompose",
                "status": "running",
            },
            "job_plan_88": {
                "job_id": "job_plan_88",
                "job_type": "plan_decompose",
                "status": "running",
            },
        }
    )

    response = client.get("/jobs/board", params={"session_id": "sess-z"})
    assert response.status_code == 200
    groups = response.json()["groups"]

    creation_ids = {item["job_id"]
                    for item in groups["task_creation"]["items"]}
    assert "job_plan_77" in creation_ids
    assert "job_plan_88" not in creation_ids


def test_jobs_board_includes_plan_execute_jobs_in_claude_group(
    board_api_env: Tuple[sqlite3.Connection, Dict[str, Dict[str, Any]], TestClient],
) -> None:
    conn, payloads, client = board_api_env

    _insert_job_index(
        conn,
        job_id="job_exec_1",
        plan_id=42,
        job_type="plan_execute",
        created_at="2026-02-19 13:00:00",
    )
    payloads["job_exec_1"] = {
        "job_id": "job_exec_1",
        "job_type": "plan_execute",
        "status": "running",
        "created_at": "2026-02-19T13:00:00Z",
        "started_at": "2026-02-19T13:00:02Z",
        "metadata": {
            "session_id": "sess-exec",
            "target_task_id": 1,
            "target_task_name": "Run hello world task chain",
        },
        "params": {"steps": 4},
        "stats": {
            "executed": 1,
            "failed": 0,
            "skipped": 0,
            "total_steps": 4,
            "current_step": 2,
            "current_task_id": 9,
            "progress_percent": 25,
        },
    }

    response = client.get("/jobs/board", params={"plan_id": 42})
    assert response.status_code == 200
    groups = response.json()["groups"]

    claude_items = groups["code_executor"]["items"]
    assert len(claude_items) == 1
    exec_item = claude_items[0]
    assert exec_item["job_id"] == "job_exec_1"
    assert exec_item["job_type"] == "plan_execute"
    assert exec_item["label"] == "Run hello world task chain"
    assert exec_item["progress_percent"] == 25
    assert exec_item["progress_status"] == "running"
    assert exec_item["progress_text"] == "1/4"
    assert exec_item["current_step"] == 2
    assert exec_item["current_task_id"] == 9
    assert exec_item["done_steps"] == 1
    assert exec_item["total_steps"] == 4


def test_jobs_board_collapses_full_plan_runs_per_plan(
    board_api_env: Tuple[sqlite3.Connection, Dict[str, Dict[str, Any]], TestClient],
) -> None:
    conn, payloads, client = board_api_env

    _insert_job_index(
        conn,
        job_id="job_full_failed",
        plan_id=42,
        job_type="plan_execute",
        created_at="2026-02-19 13:00:00",
    )
    _insert_job_index(
        conn,
        job_id="job_full_running",
        plan_id=42,
        job_type="plan_execute",
        created_at="2026-02-19 13:05:00",
    )
    _insert_job_index(
        conn,
        job_id="job_task_chain",
        plan_id=42,
        job_type="plan_execute",
        created_at="2026-02-19 13:06:00",
    )

    payloads.update(
        {
            "job_full_failed": {
                "job_id": "job_full_failed",
                "job_type": "plan_execute",
                "mode": "full_plan",
                "status": "failed",
                "created_at": "2026-02-19T13:00:00Z",
                "finished_at": "2026-02-19T13:04:00Z",
                "metadata": {
                    "session_id": "sess-exec",
                    "target_task_id": None,
                    "plan_id": 42,
                },
                "params": {"steps": 20},
                "stats": {
                    "executed": 6,
                    "failed": 1,
                    "skipped": 0,
                    "total_steps": 20,
                    "current_step": 7,
                    "current_task_id": 18,
                },
                "error": "Job interrupted by server restart",
            },
            "job_full_running": {
                "job_id": "job_full_running",
                "job_type": "plan_execute",
                "mode": "full_plan",
                "status": "running",
                "created_at": "2026-02-19T13:05:00Z",
                "started_at": "2026-02-19T13:05:03Z",
                "metadata": {
                    "session_id": "sess-exec",
                    "target_task_id": None,
                    "plan_id": 42,
                },
                "params": {
                    "steps": 17,
                    "overall_total_steps": 25,
                    "initial_completed_steps": 8,
                },
                "stats": {
                    "executed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "total_steps": 17,
                    "current_step": 2,
                    "current_task_id": 17,
                    "overall_done_steps": 8,
                    "overall_total_steps": 25,
                },
            },
            "job_task_chain": {
                "job_id": "job_task_chain",
                "job_type": "plan_execute",
                "mode": "task_chain",
                "status": "completed",
                "created_at": "2026-02-19T13:06:00Z",
                "started_at": "2026-02-19T13:06:02Z",
                "finished_at": "2026-02-19T13:06:30Z",
                "metadata": {
                    "session_id": "sess-exec",
                    "target_task_id": 9,
                    "target_task_name": "Execute task #9",
                },
                "params": {"steps": 3},
                "stats": {
                    "executed": 3,
                    "failed": 0,
                    "skipped": 0,
                    "total_steps": 3,
                    "current_step": 3,
                    "current_task_id": 9,
                },
            },
        }
    )

    response = client.get("/jobs/board", params={"plan_id": 42})
    assert response.status_code == 200
    groups = response.json()["groups"]

    claude_items = groups["code_executor"]["items"]
    assert len(claude_items) == 2
    assert response.json()["total"] == 2

    assert claude_items[0]["job_id"] == "job_task_chain"
    assert claude_items[0]["label"] == "Execute task #9"

    full_plan_items = [item for item in claude_items if item["label"] == "Plan Task Execution"]
    assert len(full_plan_items) == 1
    full_plan_item = full_plan_items[0]
    assert full_plan_item["job_id"] == "job_full_running"
    assert full_plan_item["mode"] == "full_plan"
    assert full_plan_item["status"] == "running"
    assert full_plan_item["progress_percent"] == 32
    assert full_plan_item["progress_text"] == "8/25"
    assert full_plan_item["current_step"] == 2
    assert full_plan_item["total_steps"] == 17
    assert full_plan_item["current_task_id"] == 17
