"""External PhageScope MCP endpoint (/mcp): auth, tools, limits, audit.

The data service is mocked; nothing here reaches a real PhageScope service.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.database import get_db
from app.services.phagescope_mcp import limits, local_tools, store, tools

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PROTOCOL = "2025-06-18"

KNOWN_PROTEINS = {"NP_040645.1", "NP_040587.1"}
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
            if body.get("accession") == "bad-param":
                raise tools.PhageScopeDataError("invalid_parameter", "accession is malformed.", 400)
            return {"id": 7, "Acession_ID": "PS00007", "lifestyle": None}
        if path == "v2/structures/resolve/":
            rows = [dict(STRUCTURE_ROWS.get(pid, {"protein_id": pid, "available": False})) for pid in body["protein_ids"]]
            return {"count": len(rows), "results": rows}
        if path == "v2/proteins/query/" and body.get("mode") == "by_ids":
            rows = [{"Protein_id": pid, "Product": "p"} for pid in body["protein_ids"] if pid in KNOWN_PROTEINS]
            return {"count": len(rows), "results": rows}
        return {"results": []}


STRUCTURE_ROWS = {
    "NP_040587.1": {
        "protein_id": "NP_040587.1", "available": True, "asset_id": "PDB:7vi9_A", "confidence_label": "experimental",
        "match_type": "exact_external", "identity": 100.0, "qcov": 100.0, "tcov": 100.0, "source": "PDB",
        "method": "experimental", "plddt_mean": None, "resolution_a": None, "file_format": "cif", "pdb_rel_path": "",
    },
    "MetaVR_jumbo_01_1": {
        "protein_id": "MetaVR_jumbo_01_1", "available": True, "asset_id": "ESMFOLD:MetaVR_jumbo_01_1",
        "confidence_label": "predicted", "match_type": "exact_internal", "identity": 100.0, "qcov": 100.0,
        "tcov": 100.0, "source": "ESMFold", "method": "ESMFold", "plddt_mean": 88.4, "resolution_a": None,
        "file_format": "pdb", "pdb_rel_path": "5/split_1/MetaVR_jumbo_01_1.pdb",
    },
}


def _structure_notes(client, token, ids):
    result, payload = _call(client, token, "get_structures", {"protein_ids": ids})
    assert result["isError"] is False
    return payload["data"]["results"], " ".join(payload["notes"])


def test_get_structures_notes_follow_each_record_source_and_method(mcp_client):
    client, _ = mcp_client
    token, _ = store.issue_token("u1", name="t")

    rows, notes = _structure_notes(client, token, ["NP_040587.1"])
    assert rows[0]["evidence"] == "experimental"
    assert rows[0]["source_url"] == "https://www.rcsb.org/structure/7VI9"
    assert "experimentally determined" in notes and "PDB:7vi9_A" in notes
    assert "ESMFold" not in notes and "computational" not in notes

    rows, notes = _structure_notes(client, token, ["MetaVR_jumbo_01_1"])
    assert rows[0]["evidence"] == "predicted" and "source_url" not in rows[0]
    assert "computational models" in notes and "ESMFold ESMFold (1)" in notes
    assert "experimentally determined" not in notes

    rows, notes = _structure_notes(client, token, ["NP_999999999.1"])
    assert rows == [{"protein_id": "NP_999999999.1", "available": False}]
    assert "No structure record in PhageScope for 1 id(s): NP_999999999.1" in notes
    assert "experimentally" not in notes and "computational" not in notes

    rows, notes = _structure_notes(client, token, ["NP_040587.1", "NP_999999999.1", "MetaVR_jumbo_01_1"])
    assert [r.get("evidence") for r in rows] == ["experimental", None, "predicted"]
    assert "1 record(s) are experimentally determined" in notes
    assert "1 record(s) are computational models" in notes
    assert "NP_999999999.1" in notes

    description = tools.TOOLS_BY_NAME["get_structures"].description
    assert "experimental (PDB)" in description and "predicted" in description


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
    assert init.json()["result"]["serverInfo"]["name"] == "phageagent"
    listed = _rpc(client, token, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in listed} == set(tools.TOOLS_BY_NAME) | set(local_tools.LOCAL_TOOLS_BY_NAME)
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in listed)
    resources = _rpc(client, token, "resources/list").json()["result"]["resources"]
    assert resources[0]["uri"] == "phageagent://reference/query-guide"


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


def test_anonymous_discovery_lists_tools_but_never_calls_them(mcp_client, monkeypatch):
    client, fake = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANON_DISCOVERY", "1")
    init = _rpc(
        client,
        None,
        "initialize",
        {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    )
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "phageagent"
    assert _rpc(client, None, "ping").status_code == 200
    listed = _rpc(client, None, "tools/list").json()["result"]["tools"]
    assert {"search_phages", "get_phage"} <= {tool["name"] for tool in listed}
    for tool in listed:
        assert tool["description"] and tool["annotations"]["readOnlyHint"] is not None
        for name, prop in tool["inputSchema"].get("properties", {}).items():
            assert prop.get("description"), (tool["name"], name)
    call = _rpc(client, None, "tools/call", {"name": "catalog_overview", "arguments": {}})
    assert call.status_code == 401 and call.headers["www-authenticate"].startswith("Bearer")
    assert _rpc(client, None, "resources/list").status_code == 401
    assert _rpc(client, "gpsk_not-a-real-token", "tools/list").status_code == 401
    assert client.get("/mcp", headers=ACCEPT).status_code == 401
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "catalog_overview", "arguments": {}}},
    ]
    headers = dict(ACCEPT, **{"MCP-Protocol-Version": PROTOCOL})
    assert client.post("/mcp", headers=headers, content=json.dumps(batch)).status_code == 401
    assert fake.calls == []


def test_anonymous_discovery_is_off_by_default_and_rate_limited_per_ip(mcp_client, monkeypatch):
    client, _ = mcp_client
    assert _rpc(client, None, "tools/list").status_code == 401
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANON_DISCOVERY", "1")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANON_REQUESTS_PER_MINUTE", "2")
    ip_a = {"X-Real-IP": "203.0.113.5"}
    assert _rpc(client, None, "tools/list", extra_headers=ip_a).status_code == 200
    assert _rpc(client, None, "tools/list", extra_headers=ip_a).status_code == 200
    resp = _rpc(client, None, "tools/list", extra_headers=ip_a)
    assert resp.status_code == 429 and resp.headers["retry-after"]
    assert _rpc(client, None, "tools/list", extra_headers={"X-Real-IP": "203.0.113.6"}).status_code == 200
    token, _ = store.issue_token("u1", name="t")
    assert _rpc(client, token, "tools/list", extra_headers=ip_a).status_code == 200


def test_anonymous_tool_list_includes_analysis_only_under_open_access(mcp_client, monkeypatch):
    client, _ = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANON_DISCOVERY", "1")
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANALYSIS_ENABLED", "1")
    names = {tool["name"] for tool in _rpc(client, None, "tools/list").json()["result"]["tools"]}
    assert "submit_analysis" not in names
    _setenv(monkeypatch, "PHAGESCOPE_MCP_OPEN_ACCESS", "1")
    names = {tool["name"] for tool in _rpc(client, None, "tools/list").json()["result"]["tools"]}
    assert "submit_analysis" in names


@pytest.mark.parametrize(
    ("ids", "found", "missing"),
    [
        (["NP_040645.1", "NP_999999999.1", "NP_040587.1"], ["NP_040645.1", "NP_040587.1"], ["NP_999999999.1"]),
        (["NP_040587.1", "NP_040645.1"], ["NP_040587.1", "NP_040645.1"], []),
        (["NP_1.1", "NP_2.1"], [], ["NP_1.1", "NP_2.1"]),
        (["NP_040587.1", " NP_040587.1 ", "NP_X.1", "NP_040645.1", "NP_X.1"], ["NP_040587.1", "NP_040645.1"], ["NP_X.1"]),
    ],
    ids=["mixed", "all_found", "all_missing", "duplicates"],
)
def test_search_proteins_by_ids_reports_missing_ids(mcp_client, ids, found, missing):
    client, fake = mcp_client
    token, _ = store.issue_token("u1", name="t")
    result, payload = _call(client, token, "search_proteins", {"protein_ids": ids})
    assert result["isError"] is False
    data = payload["data"]
    assert [row["Protein_id"] for row in data["results"]] == found
    assert data["missing_ids"] == missing
    assert data["count"] == len(found)
    assert data["requested_count"] == len(found) + len(missing)
    sent = [body for method, path, body in fake.calls if path == "v2/proteins/query/"][-1]
    assert sent["protein_ids"] == list(dict.fromkeys(i.strip() for i in ids))
    assert any("missing_ids" in note for note in payload["notes"])


def test_search_proteins_rejects_blank_ids_and_keeps_by_phage_shape(mcp_client):
    client, _ = mcp_client
    token, _ = store.issue_token("u1", name="t")
    result, payload = _call(client, token, "search_proteins", {"protein_ids": [" ", ""]})
    assert result["isError"] is True and payload["error"]["code"] == "invalid_arguments"
    result, payload = _call(client, token, "search_proteins", {"accession": "NC_001416.1"})
    assert result["isError"] is False
    assert "missing_ids" not in payload["data"]


PHAGE_REF_TOOLS = [
    ("get_phage", {}),
    ("get_sequence", {}),
    ("search_proteins", {}),
    ("query_features", {"feature_type": "trna"}),
]


@pytest.mark.parametrize("tool,extra", PHAGE_REF_TOOLS, ids=[t for t, _ in PHAGE_REF_TOOLS])
@pytest.mark.parametrize("bad_id", ["abc", "PS00007", "12x", "", 0, -3, 1.5, True, [7]])
def test_non_numeric_phage_id_is_invalid_arguments(mcp_client, tool, extra, bad_id):
    client, fake = mcp_client
    token, record = store.issue_token("u1", name="t")
    result, payload = _call(client, token, tool, {"phage_id": bad_id, **extra})
    assert result["isError"] is True
    assert payload["error"]["code"] == "invalid_arguments"
    assert "phage_id must be a positive integer" in payload["error"]["message"]
    assert fake.calls == []
    assert store.recent_audit(record.id)[0]["error_code"] == "invalid_arguments"


@pytest.mark.parametrize("tool,extra", PHAGE_REF_TOOLS, ids=[t for t, _ in PHAGE_REF_TOOLS])
@pytest.mark.parametrize("good_id", [7, "7", " 7 "])
def test_numeric_phage_id_still_accepted(mcp_client, tool, extra, good_id):
    client, fake = mcp_client
    token, _ = store.issue_token("u1", name="t")
    result, _ = _call(client, token, tool, {"phage_id": good_id, **extra})
    assert result["isError"] is False
    method, path, sent = fake.calls[0]
    assert (sent or {}).get("phage_id") == 7 or path == "phages/7/sequence/"


def test_data_service_parameter_errors_use_the_mcp_argument_code(mcp_client):
    client, _ = mcp_client
    token, record = store.issue_token("u1", name="t")
    result, payload = _call(client, token, "get_phage", {"accession": "bad-param"})
    assert result["isError"] is True
    assert payload["error"] == {"code": "invalid_arguments", "message": "accession is malformed."}
    assert store.recent_audit(record.id)[0]["error_code"] == "invalid_arguments"


def test_unauthorized_responses_say_how_to_get_a_token(mcp_client, monkeypatch):
    client, _ = mcp_client
    for token in (None, "gpsk_not-a-real-token"):
        resp = _rpc(client, token, "tools/list")
        error = resp.json()["error"]
        assert resp.status_code == 401 and error["code"] == "unauthorized"
        assert error["help_url"] == "https://github.com/yiyabo/phageagent-mcp#1-get-a-token"
        assert "Authorization: Bearer gpsk_" in error["message"] and error["help_url"] in error["message"]
    _setenv(monkeypatch, "PHAGESCOPE_MCP_TOKEN_HELP_URL", "https://example.org/tokens")
    assert _rpc(client, None, "tools/list").json()["error"]["help_url"] == "https://example.org/tokens"


def test_requests_without_a_valid_token_are_rate_limited_per_ip(mcp_client, monkeypatch):
    client, _ = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_ANON_REQUESTS_PER_MINUTE", "2")
    ip_a = {"X-Real-IP": "203.0.113.7"}
    assert _rpc(client, None, "tools/list", extra_headers=ip_a).status_code == 401
    assert _rpc(client, "gpsk_not-a-real-token", "tools/list", extra_headers=ip_a).status_code == 401
    resp = _rpc(client, "gpsk_other-guess", "tools/list", extra_headers=ip_a)
    assert resp.status_code == 429 and resp.headers["retry-after"]
    assert _rpc(client, None, "tools/list", extra_headers={"X-Real-IP": "203.0.113.8"}).status_code == 401
    token, _ = store.issue_token("u1", name="t")
    assert _rpc(client, token, "tools/list", extra_headers=ip_a).status_code == 200


def test_every_request_counts_against_the_per_ip_limit(mcp_client, monkeypatch):
    client, _ = mcp_client
    _setenv(monkeypatch, "PHAGESCOPE_MCP_IP_REQUESTS_PER_MINUTE", "2")
    ip_a = {"X-Real-IP": "203.0.113.9"}
    first, _ = store.issue_token("u1", name="a")
    second, _ = store.issue_token("u1", name="b")
    assert _rpc(client, first, "tools/list", extra_headers=ip_a).status_code == 200
    assert _rpc(client, second, "tools/list", extra_headers=ip_a).status_code == 200
    resp = _rpc(client, second, "tools/list", extra_headers=ip_a)
    assert resp.status_code == 429 and resp.json()["error"]["code"] == "rate_limited"
    assert _rpc(client, first, "tools/list", extra_headers={"X-Real-IP": "203.0.113.10"}).status_code == 200


def test_limiter_prunes_idle_keys(monkeypatch):
    lim = limits.TokenLimiter()
    monkeypatch.setattr(limits, "_PRUNE_ABOVE", 3)
    clock = iter([0.0, 0.0, 0.0, 0.0, 100.0])
    monkeypatch.setattr(limits.time, "monotonic", lambda: next(clock))
    for key in ("a", "b", "c", "d"):
        assert lim.acquire(key, per_minute=5, max_concurrency=0) is None
        lim.release(key)
    assert lim.acquire("e", per_minute=5, max_concurrency=0) is None
    assert set(lim._calls) == {"e"}
