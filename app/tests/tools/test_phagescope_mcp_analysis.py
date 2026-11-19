"""MCP analysis tasks: scope gating, submit/status/result, quotas and the tool guard."""

from __future__ import annotations

import json

import pytest

from app.database import get_db
from app.services.phagescope_mcp import analysis, limits, store

from .test_phagescope_mcp import _call, _rpc


@pytest.fixture
def analysis_client(app_client_factory, monkeypatch):
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("PHAGESCOPE_MCP_ENABLED", "1")
    monkeypatch.setenv("PHAGESCOPE_MCP_ANALYSIS_ENABLED", "1")
    monkeypatch.setenv("PHAGESCOPE_MCP_ALLOWED_USER_IDS", "alice,bob")
    monkeypatch.setenv("PHAGESCOPE_MCP_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
    monkeypatch.setenv("PHAGESCOPE_MCP_ALLOWED_ORIGINS", "http://testserver,http://localhost,http://127.0.0.1")
    get_settings.cache_clear()
    limits.limiter.reset()
    monkeypatch.setattr(store, "_schema_ready", False)
    spawned: list[str] = []
    monkeypatch.setattr("app.routers.chat.run_routes._spawn_chat_run_worker", spawned.append)
    with app_client_factory() as client:
        with get_db() as conn:
            for user_id in ("alice", "bob"):
                conn.execute(
                    "INSERT OR IGNORE INTO users (id, email, password_hash, role, is_active) VALUES (?, ?, ?, 'user', 1)",
                    (user_id, f"{user_id}@example.org", "synthetic-test-hash"),
                )
            conn.commit()
        yield client, spawned
    get_settings.cache_clear()


def _token(scopes=(store.SCOPE_DATA_READ, store.SCOPE_ANALYSIS_SUBMIT), user="alice"):
    plaintext, record = store.issue_token(user, name="t", scopes=scopes)
    return plaintext, record


def _finish(run_id: str, status: str, response: str = "") -> None:
    from app.repository.chat_runs import append_chat_run_event, mark_chat_run_finished, mark_chat_run_started

    mark_chat_run_started(run_id)
    append_chat_run_event(run_id, {"type": "final", "payload": {"response": response, "metadata": {}}})
    mark_chat_run_finished(run_id, status)


def _names(client, token):
    return {t["name"] for t in _rpc(client, token, "tools/list").json()["result"]["tools"]}


def test_analysis_tools_need_scope_and_flag(analysis_client, monkeypatch):
    client, _ = analysis_client
    read_only, _ = _token(scopes=(store.SCOPE_DATA_READ,))
    full, _ = _token()
    assert "submit_analysis" not in _names(client, read_only)
    assert {"submit_analysis", "get_analysis_status", "get_analysis_result", "search_phages"} <= _names(client, full)
    result, body = _call(client, read_only, "submit_analysis", {"instructions": "x"})
    assert result["isError"] and body["error"]["code"] == "insufficient_scope"
    analysis_only, _ = _token(scopes=(store.SCOPE_ANALYSIS_SUBMIT,))
    names = _names(client, analysis_only)
    assert "submit_analysis" in names and "search_phages" not in names


def test_submit_status_result_round_trip(analysis_client):
    client, spawned = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "Plot genome length", "phage_accessions": ["PS1"], "idempotency_key": "k1"})
    task_id = body["task_id"]
    assert task_id.startswith("gpat_") and body["status"] == "working" and len(spawned) == 1
    _, again = _call(client, token, "submit_analysis", {"instructions": "Plot genome length", "idempotency_key": "k1"})
    assert again["task_id"] == task_id and again["deduplicated"] and len(spawned) == 1

    task = store.get_analysis_task(task_id)
    from app.repository.chat_runs import get_chat_run

    run = get_chat_run(task.run_id)
    assert run["owner_id"] == "alice"
    assert "Plot genome length" in json.loads(run["request_json"])["message"]

    from app.repository.chat_runs import append_chat_run_event

    append_chat_run_event(task.run_id, {"type": "thinking_step", "step": {"display_text": "Querying hosts", "action": {"tool": "phagescope_data"}}})
    batch = json.dumps([{"tools": [{"tool": "phagescope_data", "params": {}}, {"tool": "bio_tools"}]}])
    append_chat_run_event(task.run_id, {"type": "thinking_step", "step": {"display_text": "", "action": batch}})
    append_chat_run_event(task.run_id, {"type": "reasoning_delta", "delta": "x"})
    _, status = _call(client, token, "get_analysis_status", {"task_id": task_id})
    assert status["status"] == "working" and status["poll_after_seconds"] == 15
    assert status["progress"] == ["[phagescope_data, bio_tools]", "[phagescope_data] Querying hosts"]
    _, pending = _call(client, token, "get_analysis_result", {"task_id": task_id})
    assert pending["not_ready"]

    _finish(task.run_id, "succeeded", "Median length 45 kb.")
    _, status = _call(client, token, "get_analysis_status", {"task_id": task_id})
    assert status["status"] == "completed"
    _, result = _call(client, token, "get_analysis_result", {"task_id": task_id})
    assert result["summary"] == "Median length 45 kb." and result["deliverables"] == []
    assert result["provenance"]["tools_used"] == {"phagescope_data": 2, "bio_tools": 1}

    other, _ = _token(user="bob")
    res, err = _call(client, other, "get_analysis_status", {"task_id": task_id})
    assert res["isError"] and err["error"]["code"] == "not_found"
    audit = store.recent_audit(record.id)
    assert {"submit_analysis", "get_analysis_result"} <= {row["tool"] for row in audit}


def test_submit_quotas(analysis_client, monkeypatch):
    client, _ = analysis_client
    token, _ = _token()
    _call(client, token, "submit_analysis", {"instructions": "first"})
    res, err = _call(client, token, "submit_analysis", {"instructions": "second"})
    assert res["isError"] and err["error"]["code"] == "too_many_tasks"
    res, err = _call(client, token, "submit_analysis", {"instructions": ""})
    assert err["error"]["code"] in {"invalid_argument", "too_many_tasks"}


def test_replies_count_toward_daily_analysis_quota(analysis_client, monkeypatch):
    client, spawned = analysis_client
    monkeypatch.setenv("PHAGESCOPE_MCP_ANALYSIS_DAILY_SUBMISSIONS", "2")
    from app.services.foundation.settings import get_settings

    get_settings.cache_clear()
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "first"})
    task_id = body["task_id"]
    assert store.analysis_counts(record)["user_today"] == 1
    empty_result, empty_error = _call(client, token, "reply_analysis", {"task_id": task_id, "message": ""})
    assert empty_result["isError"] and empty_error["error"]["code"] == "invalid_argument"
    assert store.analysis_counts(record)["user_today"] == 1 and len(spawned) == 1
    _finish(store.get_analysis_task(task_id).run_id, "succeeded")
    reply_args = {"task_id": task_id, "message": "follow-up", "idempotency_key": "same-reply"}
    _, replied = _call(client, token, "reply_analysis", reply_args)
    assert replied["status"] == "working"
    assert store.analysis_counts(record)["user_today"] == 2
    second_run = store.get_analysis_task(task_id).run_id
    _finish(second_run, "succeeded")
    before = len(spawned)
    _, duplicate = _call(client, token, "reply_analysis", reply_args)
    assert duplicate["deduplicated"] and store.analysis_counts(record)["user_today"] == 2
    result, error = _call(client, token, "reply_analysis", {"task_id": task_id, "message": "over quota"})
    assert result["isError"] and error["error"]["code"] == "daily_task_quota_exceeded"
    other_token, _ = _token(user="alice")
    result, error = _call(client, other_token, "submit_analysis", {"instructions": "over quota on another token"})
    assert result["isError"] and error["error"]["code"] == "daily_task_quota_exceeded"
    assert len(spawned) == before


def test_reply_quota_and_idempotency_survive_session_deletion(analysis_client, monkeypatch):
    from app.repository.chat_runs import get_chat_run
    from app.services.foundation.settings import get_settings

    client, spawned = analysis_client
    monkeypatch.setenv("PHAGESCOPE_MCP_ANALYSIS_DAILY_SUBMISSIONS", "3")
    get_settings.cache_clear()
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "durable quota"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")
    for key in ("one", "two"):
        _, body = _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "next", "idempotency_key": key})
        assert body["status"] == "working"
        _finish(store.get_analysis_task(task.task_id).run_id, "succeeded")
    run_id = store.get_analysis_task(task.task_id).run_id
    assert store.analysis_counts(record)["user_today"] == 3
    with get_db() as conn:
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (task.session_id,))
        conn.commit()
    assert get_chat_run(run_id) is None
    assert store.analysis_counts(record)["user_today"] == 3
    before = len(spawned)
    _, duplicate = _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "next", "idempotency_key": "one"})
    assert duplicate["deduplicated"]
    result, error = _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "over quota"})
    assert result["isError"] and error["error"]["code"] == "daily_task_quota_exceeded"
    assert len(spawned) == before and store.analysis_counts(record)["user_today"] == 3


def test_reply_start_failure_releases_unused_quota_reservation(analysis_client, monkeypatch):
    client, spawned = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "failed reply"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")

    def fail_start(*args, **kwargs):
        raise RuntimeError("synthetic startup failure")

    with monkeypatch.context() as patch:
        patch.setattr("app.routers.chat.run_routes.start_background_chat_run", fail_start)
        result, _ = _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "next", "idempotency_key": "retry"})
        assert result["isError"]
    assert store.analysis_counts(record)["user_today"] == 1 and len(spawned) == 1
    assert store.get_analysis_reply(task.task_id, f"{task.task_id}:reply:retry") is None
    _, reply = _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "next", "idempotency_key": "retry"})
    assert reply["status"] == "working" and store.analysis_counts(record)["user_today"] == 2


def test_reply_reservation_reclaimed_after_restart_before_run(analysis_client, monkeypatch):
    from datetime import timedelta
    from app.services.foundation.settings import get_settings

    client, spawned = analysis_client
    monkeypatch.setenv("PHAGESCOPE_MCP_ANALYSIS_DAILY_SUBMISSIONS", "2")
    get_settings.cache_clear()
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "restart before run"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")
    client_message_id = f"{task.task_id}:reply:restart"
    assert store.reserve_analysis_reply(task, client_message_id, 2)
    monkeypatch.setattr(store, "_schema_ready", False)
    args = {"task_id": task.task_id, "message": "recover", "idempotency_key": "restart"}
    _, live = _call(client, token, "reply_analysis", args)
    assert live["deduplicated"] and live["status"] == "working" and len(spawned) == 1
    now = store._now()
    monkeypatch.setattr(store, "_now", lambda: now + timedelta(seconds=store.REPLY_RESERVATION_TTL_SECONDS + 1))
    _, recovered = _call(client, token, "reply_analysis", args)
    assert recovered["status"] == "working" and not recovered.get("deduplicated")
    assert len(spawned) == 2 and store.analysis_counts(record)["user_today"] == 2
    assert store.get_analysis_reply(task.task_id, client_message_id)["run_id"] == spawned[-1]


def test_reply_reservation_attaches_existing_run_after_restart(analysis_client, monkeypatch):
    from app.routers.chat.models import ChatRequest
    from app.routers.chat.run_routes import start_background_chat_run

    client, spawned = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "restart after run"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")
    client_message_id = f"{task.task_id}:reply:restart"
    assert store.reserve_analysis_reply(task, client_message_id, 2)
    async def start_run():
        return start_background_chat_run(ChatRequest(message="recover", session_id=task.session_id,
                                                     client_message_id=client_message_id),
                                         session_id=task.session_id, owner_id=record.user_id)

    run_id = client.portal.call(start_run)
    monkeypatch.setattr(store, "_schema_ready", False)
    _, recovered = _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "recover", "idempotency_key": "restart"})
    assert recovered["deduplicated"] and recovered["status"] == "working"
    assert len(spawned) == 2 and store.analysis_counts(record)["user_today"] == 2
    assert store.get_analysis_reply(task.task_id, client_message_id)["run_id"] == run_id
    assert store.get_analysis_task(task.task_id).run_id == run_id


def test_only_one_concurrent_reply_reclaim_wins(analysis_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Barrier

    client, _ = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "race reclaim"})
    task = store.get_analysis_task(body["task_id"])
    client_message_id = f"{task.task_id}:reply:race"
    assert store.reserve_analysis_reply(task, client_message_id, 2)
    now = store._now()
    monkeypatch.setattr(store, "_now", lambda: now + timedelta(seconds=store.REPLY_RESERVATION_TTL_SECONDS + 1))
    barrier = Barrier(2)

    def reclaim():
        barrier.wait(timeout=5)
        return store.reserve_analysis_reply(task, client_message_id, 2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reclaim(), range(2)))
    assert sorted(results) == [False, True]
    assert store.analysis_counts(record)["user_today"] == 2


@pytest.mark.parametrize("recovery", ["retry", "reserve"])
@pytest.mark.parametrize("same_timestamp", [False, True])
def test_recovering_older_reply_preserves_newer_current_run(analysis_client, recovery, same_timestamp):
    from datetime import timedelta
    from app.repository.chat_runs import fetch_unconsumed_chat_run_signals, mark_chat_run_started

    client, spawned = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "recover older reply"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")
    args = {"task_id": task.task_id, "message": "reply A", "idempotency_key": "A"}
    _call(client, token, "reply_analysis", args)
    run_a = store.get_analysis_task(task.task_id).run_id
    _finish(run_a, "succeeded")
    _call(client, token, "reply_analysis", {**args, "message": "reply B", "idempotency_key": "B"})
    run_b = store.get_analysis_task(task.task_id).run_id
    mark_chat_run_started(run_b)
    now = store._now()
    with get_db() as conn:
        conn.execute(
            "UPDATE phagescope_mcp_analysis_replies SET run_id = NULL, created_at = ? WHERE task_id = ? AND client_message_id = ?",
            (store._iso(now - timedelta(seconds=0 if same_timestamp else 1)), task.task_id, f"{task.task_id}:reply:A"),
        )
        conn.execute(
            "UPDATE phagescope_mcp_analysis_replies SET created_at = ? WHERE task_id = ? AND client_message_id = ?",
            (store._iso(now), task.task_id, f"{task.task_id}:reply:B"),
        )
        conn.commit()
    if recovery == "retry":
        _, recovered = _call(client, token, "reply_analysis", args)
        assert recovered["deduplicated"] and recovered["status"] == "completed"
    else:
        assert not store.reserve_analysis_reply(task, f"{task.task_id}:reply:A", 10)
    assert store.get_analysis_reply(task.task_id, f"{task.task_id}:reply:A")["run_id"] == run_a
    assert store.get_analysis_task(task.task_id).run_id == run_b
    assert store.analysis_counts(record)["user_today"] == 3 and len(spawned) == 3
    _, status = _call(client, token, "get_analysis_status", {"task_id": task.task_id})
    assert status["status"] == "working"
    _, cancelled = _call(client, token, "cancel_analysis", {"task_id": task.task_id})
    assert cancelled["status"] == "cancel_requested"
    assert [signal["kind"] for signal in fetch_unconsumed_chat_run_signals(run_b)] == ["cancel"]
    assert fetch_unconsumed_chat_run_signals(run_a) == []
    assert store.get_analysis_task(task.task_id).run_id == run_b


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_delayed_reply_start_becomes_current_despite_earlier_reservation(analysis_client, same_timestamp):
    from datetime import timedelta
    from app.routers.chat.models import ChatRequest
    from app.routers.chat.run_routes import start_background_chat_run

    client, spawned = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "delayed start"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")
    client_message_id = f"{task.task_id}:reply:A"
    assert store.reserve_analysis_reply(task, client_message_id, 10)
    _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "B first", "idempotency_key": "B"})
    run_b = store.get_analysis_task(task.task_id).run_id
    _finish(run_b, "succeeded")

    async def start_run():
        return start_background_chat_run(ChatRequest(message="A delayed", session_id=task.session_id,
                                                     client_message_id=client_message_id),
                                         session_id=task.session_id, owner_id=record.user_id)

    run_a = client.portal.call(start_run)
    now = store._now()
    with get_db() as conn:
        conn.execute("UPDATE phagescope_mcp_analysis_replies SET created_at = ? WHERE task_id = ? AND client_message_id = ?",
                     (store._iso(now - timedelta(minutes=2)), task.task_id, client_message_id))
        conn.execute("UPDATE phagescope_mcp_analysis_replies SET created_at = ? WHERE task_id = ? AND client_message_id = ?",
                     (store._iso(now - timedelta(minutes=1)), task.task_id, f"{task.task_id}:reply:B"))
        conn.execute("UPDATE chat_runs SET created_at = ? WHERE run_id = ?", (store._iso(now), run_a))
        conn.execute("UPDATE chat_runs SET created_at = ? WHERE run_id = ?",
                     (store._iso(now - timedelta(seconds=0 if same_timestamp else 1)), run_b))
        conn.commit()
    store.finish_analysis_reply(task.task_id, client_message_id, run_a)
    assert store.get_analysis_task(task.task_id).run_id == run_a
    _, status = _call(client, token, "get_analysis_status", {"task_id": task.task_id})
    assert status["status"] == "working"
    assert store.analysis_counts(record)["user_today"] == 3 and len(spawned) == 3


def test_existing_reply_runs_are_backfilled_once(analysis_client, monkeypatch):
    client, _ = analysis_client
    token, record = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "legacy run"})
    task = store.get_analysis_task(body["task_id"])
    _finish(task.run_id, "succeeded")
    _call(client, token, "reply_analysis", {"task_id": task.task_id, "message": "legacy reply", "idempotency_key": "legacy"})
    with get_db() as conn:
        conn.execute("DELETE FROM phagescope_mcp_analysis_replies")
        conn.commit()
    for _ in range(2):
        monkeypatch.setattr(store, "_schema_ready", False)
        assert store.analysis_counts(record)["user_today"] == 2


def test_tool_guard_blocks_only_mcp_sessions(analysis_client):
    client, _ = analysis_client
    token, _ = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "guard"})
    session_id = store.get_analysis_task(body["task_id"]).session_id
    assert analysis.tool_refusal("phagescope", {"action": "submit"}, session_id)["success"] is False
    assert analysis.tool_refusal("mcp__github__create_issue", {}, session_id) is not None
    assert analysis.tool_refusal("phagescope", {"action": "query"}, session_id) is None
    assert analysis.tool_refusal("phagescope", {"action": "submit"}, "session_web_1") is None


def test_signed_links_and_scope_admin_route(analysis_client):
    client, _ = analysis_client
    url, expires = analysis.sign_link("gpat_x", "report.md")
    sig = url.rsplit("sig=", 1)[1]
    assert analysis.verify_link("gpat_x", "report.md", expires, sig)
    assert not analysis.verify_link("gpat_x", "other.md", expires, sig)
    assert not analysis.verify_link("gpat_x", "report.md", 1, sig)
    resp = client.get(f"/phagescope-mcp/analysis-files/gpat_x?path=report.md&expires={expires}&sig={sig}")
    assert resp.status_code == 404
    _, record = _token(scopes=(store.SCOPE_DATA_READ,))
    with pytest.raises(ValueError):
        store.set_token_scopes(record.id, ("admin:all",))
    assert store.set_token_scopes(record.id, (store.SCOPE_ANALYSIS_SUBMIT, store.SCOPE_DATA_READ)).scopes == (
        store.SCOPE_DATA_READ,
        store.SCOPE_ANALYSIS_SUBMIT,
    )


def test_execute_tool_refuses_phagescope_submit_in_mcp_session(analysis_client):
    import asyncio

    from tool_box import execute_tool
    from tool_box.context import ToolContext

    client, _ = analysis_client
    token, _ = _token()
    _, body = _call(client, token, "submit_analysis", {"instructions": "guard via toolbox"})
    session_id = store.get_analysis_task(body["task_id"]).session_id
    result = asyncio.run(execute_tool("phagescope", action="submit", tool_context=ToolContext(session_id=session_id)))
    assert result["error"] == "tool_not_allowed_for_mcp_task"


def test_reply_cancel_and_list(analysis_client):
    from app.repository.chat_runs import fetch_unconsumed_chat_run_signals, get_chat_run

    client, spawned = analysis_client
    token, _ = _token()
    assert {"reply_analysis", "cancel_analysis", "list_analyses"} <= _names(client, token)
    _, body = _call(client, token, "submit_analysis", {"instructions": "Phase two"})
    task_id = body["task_id"]
    first_run = store.get_analysis_task(task_id).run_id

    res, err = _call(client, token, "reply_analysis", {"task_id": task_id, "message": "more"})
    assert res["isError"] and err["error"]["code"] == "task_busy"

    _, cancelled = _call(client, token, "cancel_analysis", {"task_id": task_id})
    assert cancelled["status"] == "cancel_requested"
    assert [s["kind"] for s in fetch_unconsumed_chat_run_signals(first_run)] == ["cancel"]

    _finish(first_run, "cancelled")
    _, again = _call(client, token, "cancel_analysis", {"task_id": task_id})
    assert again["already_finished"] and again["status"] == "cancelled"

    args = {"task_id": task_id, "message": "Now add a histogram", "idempotency_key": "r1"}
    _, replied = _call(client, token, "reply_analysis", args)
    assert replied["status"] == "working" and len(spawned) == 2
    second_run = store.get_analysis_task(task_id).run_id
    assert second_run != first_run
    run = get_chat_run(second_run)
    assert run["session_id"] == store.get_analysis_task(task_id).session_id and run["owner_id"] == "alice"
    assert "Now add a histogram" in json.loads(run["request_json"])["message"]
    _, dup = _call(client, token, "reply_analysis", args)
    assert dup["deduplicated"] and len(spawned) == 2

    _, listed = _call(client, token, "list_analyses", {})
    assert [t["task_id"] for t in listed["tasks"]] == [task_id] and listed["tasks"][0]["status"] == "working"
    _, filtered = _call(client, token, "list_analyses", {"status": "completed"})
    assert filtered["tasks"] == []
    other, _ = _token(user="bob")
    _, theirs = _call(client, other, "list_analyses", {})
    assert theirs["tasks"] == []
    res, err = _call(client, other, "cancel_analysis", {"task_id": task_id})
    assert err["error"]["code"] == "not_found"
