"""Run Inspector: read-only projection, repository queries, and endpoint scope."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.repository import run_inspection as repo
from app.routers.chat import run_routes
from app.services.run_inspector import (
    MAX_STEPS,
    build_run_inspection,
    project_steps,
    redact,
    result_success,
    summarize_usage,
)

_SCHEMA = """
CREATE TABLE chat_runs (
    run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    status TEXT, user_message_id INTEGER, assistant_message_id INTEGER, error TEXT,
    request_json TEXT, created_at TEXT, started_at TEXT, finished_at TEXT,
    last_event_seq INTEGER
);
CREATE TABLE chat_run_events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_at TEXT, PRIMARY KEY (run_id, seq)
);
CREATE TABLE llm_usage_log (
    id INTEGER PRIMARY KEY, run_id TEXT, phase TEXT, call_purpose TEXT, tool_name TEXT,
    call_status TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
    total_tokens INTEGER, cache_read_tokens INTEGER, duration_ms REAL,
    estimated_cost REAL, cost_currency TEXT
);
"""


def _step(
    iteration,
    status,
    *,
    kind="tool",
    tools=None,
    result=None,
    started="2026-11-15T10:00:00",
    finished=None,
    label="step",
):
    action = json.dumps({"tools": tools}) if tools else None
    return {
        "type": "thinking_step",
        "step": {
            "iteration": iteration,
            "kind": kind,
            "status": status,
            "display_text": label,
            "action": action,
            "action_result": result,
            "started_at": started,
            "finished_at": finished,
            "timestamp": started,
            "self_correction": None,
        },
    }


TOOL = [
    {
        "tool": "web_search",
        "params": {"query": "endolysin", "api_key": "sk-secret"},
        "tool_call_id": "c1",
    }
]


def _seed_run(conn: sqlite3.Connection) -> None:
    request = {
        "message": "调研裂解酶",
        "context": {"plan_mode": False, "token": "hidden", "plan_offer_accepted": True},
    }
    conn.execute(
        "INSERT INTO chat_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "run_1",
            "sess_1",
            "alice",
            "succeeded",
            10,
            11,
            None,
            json.dumps(request, ensure_ascii=False),
            "2026-11-15 10:00:00",
            "2026-11-15 10:00:00",
            "2026-11-15 10:00:12",
            9,
        ),
    )
    events = [
        {"type": "start"},
        _step(1, "thinking", kind="reasoning", label="plan"),
        {"type": "thinking_delta", "iteration": 1, "delta": "x" * 100},
        {"type": "reasoning_delta", "iteration": 1, "delta": "y"},
        _step(1, "calling_tool", tools=TOOL),
        _step(
            1,
            "analyzing",
            tools=TOOL,
            result='[web_search] {"success": true, "summary": "3 hits"}',
            finished="2026-11-15T10:00:04",
        ),
        {
            "type": "artifact",
            "path": "results/a.png",
            "display_name": "a.png",
            "source_tool": "execute_code",
            "iteration": 2,
        },
        {"type": "delta", "content": "answer"},
        {
            "type": "final",
            "payload": {
                "response": "done",
                "metadata": {
                    "status": "completed",
                    "plan_id": None,
                    "termination_reason": None,
                    "iterations": 2,
                    "tools_used": ["web_search"],
                    "secret_token": "x",
                    "plan_offer_gate": "llm",
                },
            },
        },
    ]
    for seq, payload in enumerate(events):
        conn.execute(
            "INSERT INTO chat_run_events VALUES (?,?,?,?,?)",
            (
                "run_1",
                seq,
                payload["type"],
                json.dumps(payload, ensure_ascii=False),
                "2026-11-15 10:00:0" + str(min(seq, 9)),
            ),
        )
    conn.executemany(
        "INSERT INTO llm_usage_log (run_id, phase, call_purpose, tool_name, call_status, model, prompt_tokens,"
        " completion_tokens, total_tokens, cache_read_tokens, duration_ms, estimated_cost, cost_currency)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "run_1",
                "deep_think",
                "deep_think_iteration",
                "deep_think",
                "ok",
                "m1",
                100,
                20,
                120,
                50,
                900.0,
                0.01,
                "CNY",
            ),
            (
                "run_1",
                "deep_think",
                "deep_think_iteration",
                "deep_think",
                "error",
                "m1",
                10,
                0,
                10,
                0,
                100.0,
                0.0,
                "CNY",
            ),
            (
                "other",
                "chat",
                "chat_main",
                None,
                "ok",
                "m1",
                999,
                999,
                1998,
                0,
                1.0,
                9.0,
                "CNY",
            ),
        ],
    )
    conn.commit()


@pytest.fixture()
def inspection_db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    # The endpoint reads in asyncio.to_thread; production uses the pooled get_db.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _seed_run(conn)

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(repo, "get_db", fake_get_db)
    return conn


def test_streaming_filter_matches_declared_types() -> None:
    import inspect

    source = inspect.getsource(repo.fetch_inspection_events)
    listed = set(
        re.findall(r"'([a-z_]+)'", source.split("NOT IN", 1)[1].split(")", 1)[0])
    )
    assert listed == set(repo.STREAMING_EVENT_TYPES)


def test_repository_skips_streaming_rows_but_counts_them(inspection_db) -> None:
    events = repo.fetch_inspection_events("run_1")
    types = [payload["type"] for _seq, _at, payload in events]
    assert (
        "thinking_delta" not in types
        and "delta" not in types
        and "reasoning_delta" not in types
    )
    assert types.count("thinking_step") == 3
    counts = repo.count_run_event_types("run_1")
    assert (
        counts["thinking_delta"] == 1
        and counts["delta"] == 1
        and counts["thinking_step"] == 3
    )
    assert len(repo.fetch_run_usage("run_1")) == 2
    by_message = repo.get_run_by_assistant_message("sess_1", 11)
    assert by_message is not None and by_message["run_id"] == "run_1"
    assert repo.get_run_by_assistant_message("sess_1", 99) is None


def test_usage_query_tolerates_missing_table(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    @contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(repo, "get_db", fake_get_db)
    assert repo.fetch_run_usage("run_1") == []


def test_build_inspection_projects_steps_usage_and_redacts(inspection_db) -> None:
    run = repo.get_run_for_inspection("run_1")
    assert run is not None
    report = build_run_inspection(
        run,
        repo.fetch_inspection_events("run_1"),
        repo.count_run_event_types("run_1"),
        repo.fetch_run_usage("run_1"),
    )

    assert report["run"]["status"] == "succeeded"
    assert report["run"]["duration_ms"] == 12000
    assert report["run"]["message"] == "调研裂解酶"
    assert report["run"]["context_flags"] == {
        "plan_mode": False,
        "plan_offer_accepted": True,
    }

    steps = report["steps"]
    assert [step["kind"] for step in steps] == ["reasoning", "tool"]
    tool_step = steps[1]
    assert tool_step["status"] == "analyzing"
    assert tool_step["success"] is True
    assert tool_step["duration_ms"] == 4000
    assert tool_step["tools"][0]["tool"] == "web_search"
    assert "sk-secret" not in tool_step["tools"][0]["params"]
    assert "[redacted]" in tool_step["tools"][0]["params"]

    summary = report["summary"]
    assert summary["tools"] == ["web_search"]
    assert summary["tool_failures"] == 0
    assert summary["artifacts"] == 1
    assert summary["streaming_events"]["thinking_delta"] == 1

    assert report["final"]["plan_offer_gate"] == "llm"
    assert "secret_token" not in report["final"]
    assert report["final"]["response_preview"] == "done"

    usage = report["usage"]
    assert usage["calls"] == 2 and usage["failed_calls"] == 1
    assert usage["total_tokens"] == 130 and usage["cache_read_tokens"] == 50
    assert usage["by_purpose"][0]["purpose"] == "deep_think_iteration"
    assert [item["type"] for item in report["events"]] == ["artifact"]
    assert report["truncated"] == {"steps": False, "artifacts": False}


def test_error_status_marks_step_failed_and_success_flag_parsing() -> None:
    events = [(1, "", _step(3, "error", tools=TOOL, result="boom"))]
    steps, truncated = project_steps(events)
    assert steps[0]["success"] is False and truncated is False
    assert result_success('[x] {"success": false}') is False
    assert result_success({"success": True}) is True
    assert result_success("no flag here") is None


def test_step_cap_reports_truncation() -> None:
    events = [(i, "", _step(i, "done", kind="reasoning")) for i in range(MAX_STEPS + 5)]
    steps, truncated = project_steps(events)
    assert len(steps) == MAX_STEPS
    assert truncated is True


def test_redact_masks_nested_secrets_and_long_strings() -> None:
    value = redact(
        {"auth": {"Authorization": "Bearer x", "items": ["a" * 1000]}, "Cookie": "c"}
    )
    assert value["Cookie"] == "[redacted]"
    assert value["auth"]["Authorization"] == "[redacted]"
    assert len(value["auth"]["items"][0]) < 500


def test_summarize_usage_handles_empty() -> None:
    usage = summarize_usage([])
    assert (
        usage["calls"] == 0 and usage["total_tokens"] == 0 and usage["by_purpose"] == []
    )


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _call(monkeypatch, *, owner="alice", session="sess_1", **query):
    monkeypatch.setattr(run_routes, "ensure_owner_access", _fake_owner_check(owner))
    return asyncio.run(run_routes.inspect_session_run(session, _request(), **query))


def _fake_owner_check(owner: str):
    def check(_request, resource_owner, *, detail="Forbidden"):
        if resource_owner != owner:
            raise HTTPException(status_code=403, detail=detail)
        return owner

    return check


def test_endpoint_resolves_by_run_or_message(inspection_db, monkeypatch) -> None:
    by_run = _call(monkeypatch, run_id="run_1", message_id=None)
    by_message = _call(monkeypatch, run_id=None, message_id=11)
    assert by_run["run"]["run_id"] == by_message["run"]["run_id"] == "run_1"


def test_endpoint_enforces_owner_session_and_input(inspection_db, monkeypatch) -> None:
    with pytest.raises(HTTPException) as owner_error:
        _call(monkeypatch, owner="mallory", run_id="run_1", message_id=None)
    assert owner_error.value.status_code == 403

    with pytest.raises(HTTPException) as session_error:
        _call(monkeypatch, session="sess_other", run_id="run_1", message_id=None)
    assert session_error.value.status_code == 403

    with pytest.raises(HTTPException) as missing:
        _call(monkeypatch, run_id="nope", message_id=None)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as bad:
        _call(monkeypatch, run_id=None, message_id=None)
    assert bad.value.status_code == 400


def test_endpoint_is_mounted() -> None:
    router = APIRouter()
    run_routes.mount_run_routes(router)
    paths = {
        (route.path, tuple(sorted(route.methods or ())))
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    assert ("/sessions/{session_id}/run-inspection", ("GET",)) in paths
