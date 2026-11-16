from __future__ import annotations

import asyncio

from app.config.rag_config import reset_lightrag_gateway_settings_cache
from tool_box.tools_impl import lightrag_query as lightrag_mod


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        assert url.endswith("/query/data")
        assert json is not None
        assert json["query"] == "phage host interaction"
        return _FakeResponse(
            200,
            {
                "status": "success",
                "data": {
                    "entities": [{"entity_name": "phage", "description": "virus"}],
                    "relationships": [{"description": "infects"}],
                    "chunks": [{"content": "Phages infect bacteria."}],
                    "references": [{"file_path": "paper.md"}],
                    "context_items": [
                        {
                            "shard": "00",
                            "file_path": "paper.md",
                            "content": "Phages infect bacteria.",
                        }
                    ],
                },
                "metadata": {"shards_ok": 8, "shards_total": 8},
            },
        )


def test_lightrag_query_handler_success(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTRAG_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("LIGHTRAG_GATEWAY_URL", "http://127.0.0.1:9660")
    monkeypatch.setenv("LIGHTRAG_GATEWAY_API_KEY", "test-key")
    reset_lightrag_gateway_settings_cache()
    monkeypatch.setattr(lightrag_mod.httpx, "AsyncClient", _FakeAsyncClient)

    result = asyncio.run(
        lightrag_mod.lightrag_query_handler(query="phage host interaction", top_k=3)
    )
    assert result["success"] is True
    assert result["summary"]["entities"] == 1
    assert "Phages infect bacteria" in result["context_preview"]
    reset_lightrag_gateway_settings_cache()


def test_lightrag_query_handler_requires_query() -> None:
    result = asyncio.run(lightrag_mod.lightrag_query_handler(query="  "))
    assert result["success"] is False
    assert result["code"] == "missing_query"


def _handler_result(**overrides):
    result = {
        "success": True,
        "query": "endolysin SH3b",
        "mode": "mix",
        "summary": {"entities": 2, "relationships": 1, "chunks": 1, "references": 1, "shards_ok": 8, "shards_total": 8},
        "context_preview": "[1] shard=00 file=lysk.md\nLysK SH3b binds the pentaglycine cross-bridge.",
        "entities": [
            {"entity_name": "LysK", "entity_type": "protein", "description": "Staphylococcal endolysin " + "x" * 900},
            {"entity_name": "SH3b", "description": "Cell wall binding domain", "vector": [0.1, 0.2]},
        ],
        "relationships": [{"src_id": "LysK", "tgt_id": "SH3b", "description": "contains", "weight": 1.0}],
        "chunks": [{"content": "chunk text"}],
        "references": [{"reference_id": "1", "file_path": "lysk.md", "content": ["full text " * 500]}],
        "metadata": {"shards_ok": 8, "shard_results": [{"shard": "00", "ok": True}]},
        "gateway_url": "http://172.20.0.1:19660",
        "note": "Retrieval-only evidence from LightRAG.",
    }
    result.update(overrides)
    return result


def test_sanitized_lightrag_result_keeps_evidence_for_the_model() -> None:
    from app.routers.chat.tool_results import sanitize_tool_result

    sanitized = sanitize_tool_result("lightrag_query", _handler_result())
    assert sanitized["empty_result"] is False
    assert "pentaglycine" in sanitized["context_preview"]
    assert sanitized["references"] == [{"reference_id": "1", "file_path": "lysk.md"}]
    assert [row["entity_name"] for row in sanitized["entities"]] == ["LysK", "SH3b"]
    assert len(sanitized["entities"][0]["description"]) <= 300
    assert "vector" not in sanitized["entities"][1]
    assert sanitized["relationships"][0] == {"src_id": "LysK", "tgt_id": "SH3b", "description": "contains"}
    assert sanitized["note"].startswith("Retrieval-only")


def test_sanitized_lightrag_counts_alone_are_not_empty() -> None:
    from app.routers.chat.tool_results import sanitize_tool_result

    sanitized = sanitize_tool_result(
        "lightrag_query",
        _handler_result(context_preview="", entities=[], relationships=[], chunks=[], references=[]),
    )
    assert sanitized["empty_result"] is False


def test_sanitized_lightrag_empty_and_failed_results() -> None:
    from app.routers.chat.tool_results import sanitize_tool_result

    empty = sanitize_tool_result(
        "lightrag_query",
        _handler_result(
            summary={"entities": 0, "relationships": 0, "chunks": 0},
            context_preview="",
            entities=[],
            relationships=[],
            chunks=[],
            references=[],
        ),
    )
    assert empty["empty_result"] is True
    assert "context_preview" not in empty
    failed = sanitize_tool_result("lightrag_query", {"success": False, "error": "timed out", "code": "timeout"})
    assert failed["success"] is False and failed["empty_result"] is False


def test_sanitized_lightrag_preview_is_bounded() -> None:
    from app.routers.chat.tool_results import sanitize_tool_result

    sanitized = sanitize_tool_result(
        "lightrag_query",
        _handler_result(context_preview="y" * 50000, references=[{"file_path": f"p{i}.md"} for i in range(40)]),
    )
    assert len(sanitized["context_preview"]) <= 12000
    assert len(sanitized["references"]) == 12
