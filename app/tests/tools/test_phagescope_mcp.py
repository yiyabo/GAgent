"""External PhageScope MCP endpoint (/mcp): auth, tools, limits, audit.

The data service is mocked; nothing here reaches a real PhageScope service.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.database import get_db
from app.services.phagescope_mcp import limits, store, tools

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PROTOCOL = "2025-06-18"

FAKE_ROWS = [{"id": i, "Acession_ID": f"PS{i:05d}", "host": "Salmonella", "lifestyle": None} for i in range(1, 41)]


class FakeDataService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def __call__(self, path, *, method="GET", query=None, body=None, timeout=45.0):
        self.calls.append((method, path, body if body is not None else query))
        if path == "stats/overview/":
            return {"phages": 2576926, "proteins": 131509307, "snapshot_id": "snap-test", "checked_on": "2026-09-11"}
        if path == "datasets/":
            return {"results": [{"name": "RefSeq"}, {"name": "MGV"}]}
        if path == "v2/phages/query/":
            size = body["pagesize"]
            return {"count": 41, "page": body["page"], "pagesize": size, "has_more": True, "results": FAKE_ROWS[:size]}
        if path == "v2/phages/detail/":
            if body.get("accession") == "missing":
                raise tools.PhageScopeDataError("not_found", "No such phage.", 404)
            return {"id": 7, "Acession_ID": "PS00007", "lifestyle": None}
        return {"results": []}


@pytest.fixture
def mcp_client(app_client_factory, monkeypatch):
    monkeypatch.setenv("PHAGESCOPE_MCP_ENABLED", "1")
    monkeypatch.setenv("PHAGESCOPE_MCP_ALLOWED_USER_IDS", "u1,alice,bob")
    monkeypatch.setenv("PHAGESCOPE_MCP_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
    monkeypatch.setenv("PHAGESCOPE_MCP_ALLOWED_ORIGINS", "http://testserver,http://localhost,http://127.0.0.1")
    fake = FakeDataService()
    monkeypatch.setattr(tools.client, "call_service", fake)
    tools._overview_cache.update(at=0.0, value=None)
    limits.limiter.reset()
    monkeypatch.setattr(store, "_schema_ready", False)
    with app_client_factory() as client:
        with get_db() as conn:
            for user_id in ("u1", "alice", "bob"):
                conn.execute(
                    "INSERT OR IGNORE INTO users (id, email, password_hash, role, is_active) VALUES (?, ?, ?, 'user', 1)",
                    (user_id, f"{user_id}@example.org", "synthetic-test-hash"),
                )
            conn.commit()
        yield client, fake


def _setenv(monkeypatch, name: str, value: str) -> None:
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _rpc(
    client,
    token: str | None,
    method: str,
    params: dict | None = None,
    rpc_id: int = 1,
    extra_headers: dict[str, str] | None = None,
):
    headers = dict(ACCEPT, **{"MCP-Protocol-Version": PROTOCOL})
    headers.update(extra_headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
    return client.post("/mcp", headers=headers, content=json.dumps(body))


def _call(client, token, name, arguments):
    resp = _rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    return result, json.loads(result["content"][0]["text"])


def test_mcp_rejects_missing_and_bad_tokens(mcp_client):
    client, fake = mcp_client
    assert _rpc(client, None, "tools/list").status_code == 401
    resp = _rpc(client, "gpsk_not-a-real-token", "tools/list")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")
    plaintext, record = store.issue_token("u1", name="t")
    store.revoke_token(record.id)
    assert _rpc(client, plaintext, "tools/list").status_code == 401
    assert fake.calls == []


def test_mcp_disabled_returns_404(app_client_factory, monkeypatch):
    monkeypatch.setenv("PHAGESCOPE_MCP_ENABLED", "0")
    with app_client_factory() as client:
        assert _rpc(client, "gpsk_x", "tools/list").status_code == 404


def test_mcp_initialize_and_lists_read_only_tools(mcp_client):
    client, _ = mcp_client
    token, _ = store.issue_token("u1", name="t")
    init = _rpc(
        client,
        token,
        "initialize",
        {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
    )
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "phagescope"
    listed = _rpc(client, token, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in listed} == set(tools.TOOLS_BY_NAME)
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in listed)
    resources = _rpc(client, token, "resources/list").json()["result"]["resources"]
    assert resources[0]["uri"] == "phagescope://reference/query-guide"


def test_mcp_search_phages_envelope_audit_and_usage(mcp_client):
    client, fake = mcp_client
    token, record = store.issue_token("u1", name="t")
    result, payload = _call(client, token, "search_phages", {"filters": {"host": "Salmonella"}, "pagesize": 5})
    assert result["isError"] is False
    assert payload["limits"]["returned_rows"] == 5
    assert payload["limits"]["truncated"] is False
    assert payload["provenance"]["snapshot"] == {"snapshot_id": "snap-test", "checked_on": "2026-09-11"}
    assert any("unknown" in note for note in payload["notes"])
    assert ("POST", "v2/phages/query/", {"filters": {"host": "Salmonella"}, "page": 1, "pagesize": 5,
            "include_total": False, "sort_by": "id"}) in fake.calls
    assert store.bytes_used_today(record.id) > 0
    audit = store.recent_audit(record.id)
    assert audit[0]["tool"] == "search_phages" and audit[0]["status"] == "ok"
    assert token not in json.dumps(audit)


def test_mcp_tool_errors_are_results_not_crashes(mcp_client):
    client, _ = mcp_client
    token, record = store.issue_token("u1", name="t")
    result, payload = _call(client, token, "search_phages", {"filters": {}})
    assert result["isError"] is True and payload["error"]["code"] == "invalid_arguments"
    result, payload = _call(client, token, "get_phage", {"accession": "missing"})
    assert result["isError"] is True and payload["error"]["code"] == "not_found"
    result, payload = _call(client, token, "search_phages", {"filters": {"sql": "drop"}})
    assert result["isError"] is True
    assert [row["status"] for row in store.recent_audit(record.id)] == ["error", "error", "error"]


def test_mcp_result_trimmed_to_per_call_budget(mcp_client, monkeypatch):
    client, _ = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_MAX_RESULT_BYTES", "2500")
    token, _ = store.issue_token("u1", name="t")
    result, payload = _call(client, token, "search_phages", {"filters": {"host": "Salmonella"}, "pagesize": 40})
    assert result["isError"] is False
    assert payload["limits"]["truncated"] is True
    assert payload["limits"]["returned_rows"] < 40
    assert len(result["content"][0]["text"].encode()) <= 2500


def test_mcp_daily_quota_blocks_after_limit(mcp_client, monkeypatch):
    client, fake = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_DAILY_BYTES", "3000")
    token, record = store.issue_token("u1", name="t")
    store.record_usage(record.id, 3000)
    before = len(fake.calls)
    result, payload = _call(client, token, "catalog_overview", {})
    assert result["isError"] is True and payload["error"]["code"] == "daily_quota_exceeded"
    assert len(fake.calls) == before


def test_mcp_rate_limit_returns_429(mcp_client, monkeypatch):
    client, _ = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_REQUESTS_PER_MINUTE", "2")
    token, _ = store.issue_token("u1", name="t")
    assert _rpc(client, token, "tools/list").status_code == 200
    assert _rpc(client, token, "tools/list").status_code == 200
    resp = _rpc(client, token, "tools/list")
    assert resp.status_code == 429 and resp.headers["retry-after"]


def test_mcp_token_rechecks_user_status_and_current_policy(mcp_client, monkeypatch):
    client, _ = mcp_client
    _setenv(monkeypatch, "AUTH_MODE", "local")
    token, record = store.issue_token("u1", name="lifecycle")
    assert _rpc(client, token, "tools/list").status_code == 200
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "")
    assert _rpc(client, token, "tools/list").status_code == 401
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "u1")
    assert _rpc(client, token, "tools/list").status_code == 200
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", ("u1",))
        conn.commit()
    assert _rpc(client, token, "tools/list").status_code == 401
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", ("u1",))
        conn.commit()
    assert _rpc(client, token, "tools/list").status_code == 200
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", ("u1",))
        conn.commit()
    assert _rpc(client, token, "tools/list").status_code == 401
    assert store.get_token(record.id) is not None


def test_mcp_rejects_foreign_host_and_origin(mcp_client):
    client, _ = mcp_client
    token, _ = store.issue_token("u1", name="transport")
    assert _rpc(client, token, "tools/list").status_code == 200
    assert _rpc(client, token, "tools/list", extra_headers={"Host": "evil.example"}).status_code == 421
    assert _rpc(client, token, "tools/list", extra_headers={"Origin": "https://evil.example"}).status_code == 403


def test_proxy_identity_without_local_user_can_issue_and_use_token(app_client_factory, monkeypatch):
    _setenv(monkeypatch, "AUTH_MODE", "proxy")
    _setenv(monkeypatch, "AUTH_OPEN_SIGNUP", "1")
    _setenv(monkeypatch, "PROXY_AUTH_REQUIRED", "1")
    _setenv(monkeypatch, "PROXY_AUTH_SHARED_SECRET", "synthetic-proxy-secret")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ENABLED", "1")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_HOSTS", "testserver")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "proxy-alice")
    monkeypatch.setattr(store, "_schema_ready", False)
    with app_client_factory() as client:
        identity_headers = {"X-Forwarded-User": "proxy-alice", "X-Gagent-Proxy-Secret": "synthetic-proxy-secret"}
        assert client.post("/phagescope-mcp/tokens", json={"name": "untrusted"},
                           headers={"X-Forwarded-User": "proxy-alice"}).status_code == 401
        created = client.post("/phagescope-mcp/tokens", json={"name": "proxy"}, headers=identity_headers)
        assert created.status_code == 200, created.text
        token = created.json()["token"]
        with get_db() as conn:
            assert conn.execute("SELECT id FROM users WHERE id = 'proxy-alice'").fetchone() is None
        assert _rpc(client, token, "tools/list").status_code == 200
        _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "")
        _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "alice@example.org")
        assert _rpc(client, token, "tools/list").status_code == 401
        assert client.post("/phagescope-mcp/tokens", json={"name": "email-only"},
                           headers={**identity_headers, "X-Forwarded-Email": "alice@example.org"}).status_code == 403
        _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "proxy-alice")
        with get_db() as conn:
            conn.execute("INSERT INTO users (id, email, password_hash, is_active) VALUES (?, ?, ?, 0)",
                         ("proxy-alice", "alice@example.org", "synthetic"))
            conn.commit()
        assert _rpc(client, token, "tools/list").status_code == 401


def test_token_routes_enforce_allowlist(app_client_factory, monkeypatch):
    from app.services.request_principal import RequestPrincipal

    principal = RequestPrincipal(user_id="alice", email="alice@example.org", role="user", auth_source="test",
                                 is_authenticated=True)
    import app.routers.phagescope_mcp_routes as routes

    monkeypatch.setattr(routes, "require_authenticated_principal", lambda request: principal)
    _setenv(monkeypatch, "AUTH_OPEN_SIGNUP", "0")
    with app_client_factory() as client:
        assert client.post("/phagescope-mcp/tokens", json={"name": "codex"}).status_code == 403
        _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "bob, Alice@example.org")
        created = client.post("/phagescope-mcp/tokens", json={"name": "codex"})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["token"].startswith("gpsk_")
        listed = client.get("/phagescope-mcp/tokens").json()
        assert listed["eligible"] is True and listed["daily_bytes_limit"] == 2 * 1024**3
        assert "token" not in listed["tokens"][0]
        assert client.delete(f"/phagescope-mcp/tokens/{body['record']['id']}").status_code == 200
        assert store.authenticate(body["token"]) is None


@pytest.mark.parametrize("open_signup", [False, True])
def test_email_allowlist_requires_user_id_during_open_signup(mcp_client, monkeypatch, open_signup):
    client, _ = mcp_client
    import app.routers.phagescope_mcp_routes as routes
    from app.services.request_principal import RequestPrincipal

    principal = RequestPrincipal(user_id="alice", email="alice@example.org", role="user", auth_source="test",
                                 is_authenticated=True)
    monkeypatch.setattr(routes, "require_authenticated_principal", lambda request: principal)
    _setenv(monkeypatch, "AUTH_OPEN_SIGNUP", "1" if open_signup else "0")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "ALICE@example.org")
    token, _ = store.issue_token("alice", name="legacy")
    expected = 401 if open_signup else 200
    assert _rpc(client, token, "tools/list").status_code == expected
    response = client.post("/phagescope-mcp/tokens", json={"name": "email-only"})
    assert response.status_code == (403 if open_signup else 200)
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "alice")
    assert _rpc(client, token, "tools/list").status_code == 200
    assert client.post("/phagescope-mcp/tokens", json={"name": "id-allowed"}).status_code == 200
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "")
    assert _rpc(client, token, "tools/list").status_code == 200


def test_open_access_issues_read_only_tokens_to_any_signed_in_user(mcp_client, monkeypatch):
    from app.services.phagescope_mcp import analysis

    client, _ = mcp_client
    import app.routers.phagescope_mcp_routes as routes
    from app.services.request_principal import RequestPrincipal

    principal = RequestPrincipal(user_id="carol", email="carol@example.org", role="user", auth_source="test",
                                 is_authenticated=True)
    monkeypatch.setattr(routes, "require_authenticated_principal", lambda request: principal)
    _setenv(monkeypatch, "AUTH_OPEN_SIGNUP", "1")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USERS", "")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ALLOWED_USER_IDS", "")
    assert client.post("/phagescope-mcp/tokens", json={"name": "closed"}).status_code == 403
    _setenv(monkeypatch, "PHAGESCOPE_MCP_OPEN_ACCESS", "1")
    created = client.post("/phagescope-mcp/tokens", json={"name": "open"})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["record"]["scopes"] == ["data:read"]
    listed = _rpc(client, body["token"], "tools/list")
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "catalog_overview" in names and not names & set(analysis.ANALYSIS_TOOLS_BY_NAME)
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANALYSIS_ENABLED", "1")
    with_analysis = client.post("/phagescope-mcp/tokens", json={"name": "open-analysis"}).json()
    assert with_analysis["record"]["scopes"] == ["data:read", "analysis:submit"]
    names = {tool["name"] for tool in _rpc(client, with_analysis["token"], "tools/list").json()["result"]["tools"]}
    assert set(analysis.ANALYSIS_TOOLS_BY_NAME) <= names
    _setenv(monkeypatch, "PHAGESCOPE_MCP_OPEN_ACCESS", "0")
    assert _rpc(client, body["token"], "tools/list").status_code == 401


@pytest.mark.parametrize(("count", "limit", "blocked"), [(9, 10, False), (10, 10, True), (10_000, 0, False), (5, -1, False)])
def test_analysis_limits_of_zero_mean_unlimited(count, limit, blocked):
    from app.services.phagescope_mcp import analysis

    assert analysis._at_limit(count, limit) is blocked
