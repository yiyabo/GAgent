from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services import tool_schemas
from tool_box.tools_impl import phage_kg as kg


class _FakeClient:
    calls: list[dict] = []
    replies: list = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        reply = type(self).replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _rpc(structured, *, is_error=False, text="ok"):
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}], "isError": is_error, "structuredContent": structured}}
    return httpx.Response(200, json=body, headers={"content-type": "application/json"})


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("PHAGE_KG_ENABLED", "1")
    monkeypatch.setenv("PHAGE_KG_MCP_URL", "http://kg.test/mcp")
    monkeypatch.delenv("PHAGE_KG_MCP_TOKEN", raising=False)
    monkeypatch.setattr(kg.httpx, "AsyncClient", _FakeClient)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(kg.asyncio, "sleep", lambda *_: real_sleep(0))
    _FakeClient.calls = []
    _FakeClient.replies = []
    return _FakeClient


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PHAGE_KG_ENABLED", raising=False)
    result = asyncio.run(kg.phage_kg_handler(operation="topology"))
    assert result["success"] is False and result["code"] == "disabled"
    assert not tool_schemas.phage_kg_enabled()


def test_search_entities_maps_tool_and_compacts(fake, monkeypatch):
    monkeypatch.setenv("PHAGE_KG_MCP_TOKEN", "t0k")
    fake.replies = [
        _rpc(
            {
                "graph_version": "full-00-07-clean-v1",
                "total": 1,
                "items": [{"id": "e:1", "name": "endolysin", "entity_type": "artifact", "description": "x" * 900, "degree": 5, "match_reason": "exact_id"}],
            }
        )
    ]
    result = asyncio.run(kg.phage_kg_handler(operation="search_entities", query="  endolysin ", limit=999))
    call = fake.calls[0]
    assert call["url"] == "http://kg.test/mcp"
    assert call["headers"]["Authorization"] == "Bearer t0k"
    assert call["json"]["method"] == "tools/call"
    assert call["json"]["params"] == {"name": "domain_kg_phagerag_search_entities", "arguments": {"query": "endolysin", "limit": 50}}
    assert result["success"] is True
    assert result["graph_version"] == "full-00-07-clean-v1"
    entity = result["entities"][0]
    assert entity["id"] == "e:1" and "match_reason" not in entity
    assert len(entity["description"]) == 400


def test_subgraph_edges_carry_node_names(fake):
    fake.replies = [
        _rpc(
            {
                "graph_version": "g1",
                "nodes": [{"id": "e:a", "name": "phage therapy"}, {"id": "e:b", "name": "endolysin"}],
                "edges": [{"edge_key": "r:1", "start_id": "e:a", "end_id": "e:b", "description": "uses"}],
                "truncated": True,
            }
        )
    ]
    result = asyncio.run(kg.phage_kg_handler(operation="subgraph", query="endolysin", max_hops=5))
    assert fake.calls[0]["json"]["params"]["arguments"]["max_hops"] == 2
    edge = result["edges"][0]
    assert (edge["source_name"], edge["target_name"]) == ("phage therapy", "endolysin")
    assert result["truncated"] is True


def test_evidence_requires_identifier_without_calling(fake):
    result = asyncio.run(kg.phage_kg_handler(operation="evidence"))
    assert result["code"] == "invalid_arguments"
    assert fake.calls == []


def test_document_reads_sse_reply(fake):
    body = {"jsonrpc": "2.0", "id": 1, "result": {"isError": False, "content": [], "structuredContent": {"graph_version": "g1", "document_id": "doc-1", "content_markdown": "# Title\n" + "a" * 2000}}}
    fake.replies = [httpx.Response(200, text="event: message\ndata: " + json.dumps(body) + "\n\n", headers={"content-type": "text/event-stream"})]
    result = asyncio.run(kg.phage_kg_handler(operation="document", document_id="doc-1", max_chars=600))
    assert result["success"] is True
    assert len(result["content"]) == 600 and result["content_truncated"] is True


def test_retryable_tool_error_is_retried_then_succeeds(fake):
    fake.replies = [
        _rpc({"code": "UPSTREAM_BUSY", "message": "busy", "retryable": "True", "request_id": "r1"}, is_error=True),
        _rpc({"graph_version": "g1", "nodes": 10, "edges": 20}),
    ]
    result = asyncio.run(kg.phage_kg_handler(operation="topology"))
    assert result["success"] is True and result["nodes"] == 10
    assert len(fake.calls) == 2


def test_non_retryable_error_is_reported(fake):
    fake.replies = [_rpc({"code": "VALIDATION_ERROR", "message": "bad args", "retryable": "False", "request_id": "r2"}, is_error=True)]
    result = asyncio.run(kg.phage_kg_handler(operation="search_entities", query="x"))
    assert result == {"tool": "phage_kg", "operation": "search_entities", "success": False, "code": "VALIDATION_ERROR", "error": "bad args", "request_id": "r2"}
    assert len(fake.calls) == 1


def test_unreachable_server_gives_up_after_retries(fake):
    fake.replies = [httpx.ConnectError("down")] * 3
    result = asyncio.run(kg.phage_kg_handler(operation="topology"))
    assert result["code"] == "unreachable"
    assert len(fake.calls) == 3


def test_offered_only_when_enabled(monkeypatch):
    from app.routers.chat.request_routing import get_all_tools
    from app.services.deep_think.plan_mode import plan_mode_tool_allowed

    monkeypatch.delenv("PHAGE_KG_ENABLED", raising=False)
    assert "phage_kg" not in get_all_tools()
    monkeypatch.setenv("PHAGE_KG_ENABLED", "1")
    assert "phage_kg" in get_all_tools()
    assert plan_mode_tool_allowed("phage_kg", {"operation": "subgraph"})


RAG_REPLY = {
    "query": "q",
    "mode": "mix",
    "answer": "LysK has CHAP, amidase and SH3b domains.",
    "references": [{"reference_id": "1", "file_path": "paper.md", "shard": "00", "extra": "x"}],
    "related_entities": [{"id": "e:lysk", "name": "LysK", "entity_type": "artifact", "evidence_count": 99}],
    "shards_ok": 8,
    "shards_total": 8,
    "partial": False,
    "elapsed_seconds": 14.8,
}


def test_rag_query_disabled_unless_flagged(fake, monkeypatch):
    monkeypatch.delenv("PHAGE_KG_RAG_ENABLED", raising=False)
    result = asyncio.run(kg.phage_kg_handler(operation="rag_query", query="LysK domains?"))
    assert result["code"] == "disabled" and fake.calls == []
    assert "rag_query" not in kg.parameters_schema()["properties"]["operation"]["enum"]
    monkeypatch.setenv("PHAGE_KG_RAG_ENABLED", "1")
    assert "rag_query" in kg.parameters_schema()["properties"]["operation"]["enum"]
    assert "rag_query" not in kg.PARAMETERS_SCHEMA["properties"]["operation"]["enum"]


def test_rag_query_wraps_command_and_shapes_answer(fake, monkeypatch):
    monkeypatch.setenv("PHAGE_KG_RAG_ENABLED", "1")
    fake.replies = [_rpc(RAG_REPLY)]
    result = asyncio.run(kg.phage_kg_handler(operation="rag_query", query="LysK domains?", mode="LOCAL", limit=50))
    sent = fake.calls[0]["json"]["params"]
    assert sent["name"] == "domain_kg_phagerag_rag_query"
    assert sent["arguments"]["command"]["query"] == "LysK domains?"
    assert sent["arguments"]["command"]["mode"] == "local"
    assert sent["arguments"]["command"]["top_k"] == 20
    assert result["success"] and result["answer"].startswith("LysK")
    assert result["references"] == [{"reference_id": "1", "file_path": "paper.md", "shard": "00"}]
    assert result["related_entities"][0]["id"] == "e:lysk"
    assert result["shards_ok"] == 8 and "check key claims" in result["note"]


def test_rag_query_rejects_bad_mode_and_is_not_retried(fake, monkeypatch):
    monkeypatch.setenv("PHAGE_KG_RAG_ENABLED", "1")
    bad = asyncio.run(kg.phage_kg_handler(operation="rag_query", query="q", mode="deep"))
    assert bad["code"] == "invalid_arguments" and fake.calls == []
    fake.replies = [httpx.Response(503)]
    failed = asyncio.run(kg.phage_kg_handler(operation="rag_query", query="q"))
    assert failed["success"] is False and len(fake.calls) == 1


def test_rag_query_uses_its_own_timeout(monkeypatch):
    monkeypatch.delenv("PHAGE_KG_RAG_TIMEOUT", raising=False)
    monkeypatch.delenv("PHAGE_KG_TIMEOUT", raising=False)
    assert kg._timeout_seconds("domain_kg_phagerag_rag_query") == 90.0
    assert kg._timeout_seconds("domain_kg_phagerag_search_entities") == 30.0


def test_offered_schema_lists_rag_query_only_when_enabled(monkeypatch):
    monkeypatch.setenv("PHAGE_KG_ENABLED", "1")
    monkeypatch.setenv("PHAGE_KG_RAG_ENABLED", "1")
    schema = next(s for s in tool_schemas.build_tool_schemas(["phage_kg"]) if s["function"]["name"] == "phage_kg")
    assert "rag_query" in schema["function"]["parameters"]["properties"]["operation"]["enum"]


def test_document_accepts_chunk_id(fake):
    doc = "doc-33e9af421bcc12d462ea04162032168c"
    fake.replies = [_rpc({"graph_version": "g1", "document_id": doc, "content_markdown": "# T"})]
    result = asyncio.run(kg.phage_kg_handler(operation="document", document_id=f"{doc}-chunk-003"))
    assert result["success"] is True
    assert fake.calls[0]["json"]["params"]["arguments"]["document_id"] == doc


def test_evidence_for_cited_paper_reads_document(fake):
    doc = "doc-33e9af421bcc12d462ea04162032168c"
    fake.replies = [_rpc({"graph_version": "g1", "document_id": doc, "content_markdown": "# T"})]
    result = asyncio.run(kg.phage_kg_handler(operation="evidence", identifier=f"{doc}-chunk-1"))
    assert result["success"] is True
    assert result["operation"] == "document" and result["redirected_from"] == "evidence"
    call = fake.calls[0]["json"]["params"]
    assert call["name"] == "domain_kg_phagerag_get_document"
    assert call["arguments"]["document_id"] == doc
