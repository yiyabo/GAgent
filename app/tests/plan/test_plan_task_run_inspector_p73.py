"""p73: read-only Run Inspector for plan tasks (repository + route)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.repository import run_inspection as repo
from app.routers import plan_routes

PLAN_ID = 7
TASK_ID = 3

_USAGE_SCHEMA = """
CREATE TABLE llm_usage_log (
    id INTEGER PRIMARY KEY, run_id TEXT, phase TEXT, call_purpose TEXT, tool_name TEXT,
    call_status TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
    total_tokens INTEGER, cache_read_tokens INTEGER, duration_ms REAL,
    estimated_cost REAL, cost_currency TEXT
);
"""


def _step(iteration: int, status: str, tools: list[dict] | None = None, result: str | None = None) -> dict:
    return {
        "iteration": iteration,
        "status": status,
        "display_text": f"iter {iteration}",
        "action": json.dumps({"tools": tools}) if tools else None,
        "action_result": result,
        "started_at": "2026-11-18T01:00:00",
        "finished_at": "2026-11-18T01:00:02" if status == "done" else None,
    }


def _shard(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT,
            instruction TEXT, execution_result TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE decomposition_jobs (job_id TEXT PRIMARY KEY, job_type TEXT, mode TEXT,
            target_task_id INTEGER, session_id TEXT, status TEXT, error TEXT, stats_json TEXT,
            metadata_json TEXT, created_at TEXT, started_at TEXT, finished_at TEXT);
        CREATE TABLE decomposition_job_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
            timestamp TEXT, level TEXT, message TEXT, metadata_json TEXT);
        """
    )
    return conn


def _log(conn: sqlite3.Connection, job: str, at: str, meta: dict, level: str = "info") -> None:
    conn.execute(
        "INSERT INTO decomposition_job_logs (job_id, timestamp, level, message, metadata_json) VALUES (?,?,?,?,?)",
        (job, at, level, "m", json.dumps(meta)),
    )


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    shard_path = tmp_path / f"plan_{PLAN_ID}.sqlite"
    shard = _shard(shard_path)
    main = sqlite3.connect(":memory:", check_same_thread=False)
    main.row_factory = sqlite3.Row
    main.executescript(_USAGE_SCHEMA)

    @contextmanager
    def fake_plan_db(path: Path) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield main

    import app.database as database
    import app.repository.plan_storage as plan_storage

    monkeypatch.setattr(database, "plan_db_connection", fake_plan_db)
    monkeypatch.setattr(plan_storage, "get_plan_db_path", lambda _pid: shard_path)
    monkeypatch.setattr(repo, "get_db", fake_get_db)
    monkeypatch.setattr(plan_routes, "_ensure_plan_access", lambda _pid, _req: None)
    return shard, main


def _seed_executed(shard: sqlite3.Connection, main: sqlite3.Connection) -> None:
    result = {"status": "completed", "content": "done", "metadata": {"artifact_paths": ["results/a.csv"]}}
    shard.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
        (TASK_ID, "Align reads", "completed", "align", json.dumps(result), "t0", "t1"),
    )
    shard.execute(
        "INSERT INTO decomposition_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job_old", "plan_execute", "x", TASK_ID, None, "done", None, "{}", "{}", "2026-11-17", "2026-11-17", None),
    )
    shard.execute(
        "INSERT INTO decomposition_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job_new", "plan_execute", "x", None, "s1", "done", None, "{}", "{}", "2026-11-18", "2026-11-18", None),
    )
    tool = [{"tool": "bash", "params": {"cmd": "ls", "api_key": "sk-secret"}}]
    _log(shard, "job_new", "2026-11-18T01:00:00", {"task_id": TASK_ID, "sub_type": "thinking_step", "step": _step(1, "thinking")})
    for _ in range(5):
        _log(shard, "job_new", "2026-11-18T01:00:01", {"task_id": TASK_ID, "sub_type": "thinking_delta", "delta": "x"})
    _log(shard, "job_new", "2026-11-18T01:00:02", {"task_id": TASK_ID, "sub_type": "thinking_step", "step": _step(1, "done", tool, '{"success": true}')})
    # Toolless follow-up update merges into the open tool step (no empty row).
    _log(shard, "job_new", "2026-11-18T01:00:02", {"task_id": TASK_ID, "sub_type": "thinking_step", "step": _step(1, "done", None, '{"success": true}')})
    _log(shard, "job_new", "2026-11-18T01:00:03", {"task_id": 99, "sub_type": "thinking_step", "step": _step(1, "done")})
    _log(shard, "job_new", "2026-11-18T01:00:04", {"task_id": TASK_ID, "sub_type": "llm_error"}, level="error")
    shard.commit()
    rows = [
        (f"plan_{PLAN_ID}_task_{TASK_ID}", 100, 50.0),
        (f"plan_{PLAN_ID}_task_{TASK_ID}:attempt2", 40, 20.0),
        (f"plan_{PLAN_ID}_task_{TASK_ID}0", 999, 1.0),
    ]
    for run_id, tokens, duration in rows:
        main.execute(
            "INSERT INTO llm_usage_log (run_id, phase, call_purpose, call_status, model, total_tokens, duration_ms)"
            " VALUES (?,?,?,?,?,?,?)",
            (run_id, "execute", "task", "ok", "m", tokens, duration),
        )


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _call(job_id: str | None = None) -> dict:
    return asyncio.run(plan_routes.inspect_plan_task_run(PLAN_ID, TASK_ID, _request(), job_id=job_id))


def test_route_projects_logs_usage_and_artifacts(env) -> None:
    shard, main = env
    _seed_executed(shard, main)
    payload = _call()
    assert set(payload) >= {"run", "summary", "steps", "usage", "artifacts", "truncated"}
    run = payload["run"]
    assert (run["plan_id"], run["task_id"], run["task_name"], run["job_id"]) == (PLAN_ID, TASK_ID, "Align reads", "job_new")
    assert run["run_id"] == f"plan_{PLAN_ID}_task_{TASK_ID}"
    # Reasoning step then the tool step; the foreign task's log is excluded.
    assert [s["kind"] for s in payload["steps"]] == ["reasoning", "tool"]
    step = payload["steps"][1]
    assert step["tools"][0]["tool"] == "bash" and step["success"] is True
    assert "sk-secret" not in json.dumps(payload)
    assert payload["summary"]["streaming_chunks"] == 5
    assert payload["summary"]["errors"] == 1
    # Exact run id and ":suffix" rows only; "..._task_30" excluded.
    assert payload["usage"]["calls"] == 2 and payload["usage"]["total_tokens"] == 140
    assert [item["path"] for item in payload["artifacts"]] == ["results/a.csv"]


def test_repository_never_loads_thinking_delta_rows(env) -> None:
    shard, main = env
    _seed_executed(shard, main)
    inputs = repo.load_plan_task_inspection_inputs(PLAN_ID, TASK_ID)
    assert inputs is not None
    subs = [row["metadata"]["sub_type"] for _seq, _at, row in inputs["logs"]]
    assert "thinking_delta" not in subs and len(subs) == 4
    assert inputs["log_counts"]["thinking_delta"] == 5


def test_explicit_job_id_selects_job_and_unknown_job_404(env) -> None:
    shard, main = env
    _seed_executed(shard, main)
    payload = _call(job_id="job_old")
    assert payload["run"]["job_id"] == "job_old"
    # No task logs in job_old → steps rebuilt from execution_result (none here).
    assert payload["steps"] == []
    with pytest.raises(HTTPException) as missing:
        _call(job_id="job_missing")
    assert missing.value.status_code == 404


def test_falls_back_to_execution_result_without_logs(env) -> None:
    shard, _main = env
    result = {
        "status": "completed",
        "content": "ok",
        "metadata": {"thinking_process": {"steps": [_step(1, "done", [{"tool": "python"}], "fine")]}},
        "artifact_paths": ["out/fig.png"],
    }
    shard.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
        (TASK_ID, "Plot", "completed", "plot", json.dumps(result), "t0", "t1"),
    )
    shard.commit()
    payload = _call()
    assert payload["run"]["job_id"] is None
    assert [call["tool"] for call in payload["steps"][0]["tools"]] == ["python"]
    assert [item["path"] for item in payload["artifacts"]] == ["out/fig.png"]
    assert payload["summary"]["streaming_chunks"] == 0


def test_missing_or_never_executed_task_is_404(env) -> None:
    shard, _main = env
    with pytest.raises(HTTPException) as missing:
        _call()
    assert missing.value.status_code == 404
    shard.execute("INSERT INTO tasks (id, name, status) VALUES (?,?,?)", (TASK_ID, "Later", "pending"))
    shard.commit()
    with pytest.raises(HTTPException) as pending:
        _call()
    assert pending.value.status_code == 404


def test_route_enforces_plan_access(env, monkeypatch) -> None:
    def deny(_pid, _req):
        raise HTTPException(status_code=403, detail="plan owner mismatch")

    monkeypatch.setattr(plan_routes, "_ensure_plan_access", deny)
    with pytest.raises(HTTPException) as denied:
        _call()
    assert denied.value.status_code == 403


def test_route_is_mounted() -> None:
    paths = {(r.path, tuple(sorted(r.methods or ()))) for r in plan_routes.plan_router.routes if isinstance(r, APIRoute)}
    assert ("/plans/{plan_id}/tasks/{task_id}/run-inspection", ("GET",)) in paths
