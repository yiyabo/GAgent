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


def _capturing_client(sent: list):
    class _CapturingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json)
            return _FakeResponse(
                200,
                {
                    "status": "success",
                    "data": {"chunks": [{"content": "LysK SH3b binds pentaglycine."}], "context_items": []},
                    "metadata": {"shards_ok": 8, "shards_total": 8},
                },
            )

    return _CapturingClient


def _run_with_capture(monkeypatch, **kwargs):
    monkeypatch.setenv("LIGHTRAG_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("LIGHTRAG_GATEWAY_URL", "http://127.0.0.1:9660")
    reset_lightrag_gateway_settings_cache()
    sent: list = []
    monkeypatch.setattr(lightrag_mod.httpx, "AsyncClient", _capturing_client(sent))
    try:
        result = asyncio.run(lightrag_mod.lightrag_query_handler(query="endolysin SH3b binding", **kwargs))
    finally:
        reset_lightrag_gateway_settings_cache()
    return result, sent[-1]


def test_agent_keywords_are_sent_so_lightrag_skips_extraction(monkeypatch) -> None:
    result, payload = _run_with_capture(
        monkeypatch,
        ll_keywords=["LysK", " SH3b  domain ", "lysk", ""],
        hl_keywords=["cell wall binding"],
    )
    assert payload["ll_keywords"] == ["LysK", "SH3b domain"]
    assert payload["hl_keywords"] == ["cell wall binding"]
    assert result["keywords"] == {
        "source": "agent",
        "high_level": ["cell wall binding"],
        "low_level": ["LysK", "SH3b domain"],
    }


def test_missing_keyword_level_mirrors_the_other(monkeypatch) -> None:
    _, payload = _run_with_capture(monkeypatch, ll_keywords="LysK, SH3b；Staphylococcus aureus")
    assert payload["ll_keywords"] == ["LysK", "SH3b", "Staphylococcus aureus"]
    assert payload["hl_keywords"] == payload["ll_keywords"]


def test_without_keywords_lightrag_extracts_them(monkeypatch) -> None:
    result, payload = _run_with_capture(monkeypatch, hl_keywords=[], ll_keywords=None)
    assert "hl_keywords" not in payload and "ll_keywords" not in payload
    assert result["keywords"]["source"] == "lightrag"


def test_keywords_are_bounded() -> None:
    many = [f"term {i}" for i in range(30)] + ["x" * 200]
    cleaned = lightrag_mod.normalize_keywords(many)
    assert len(cleaned) == lightrag_mod.KEYWORD_MAX_ITEMS
    assert lightrag_mod.normalize_keywords(["y" * 200])[0] == "y" * lightrag_mod.KEYWORD_MAX_CHARS
    assert lightrag_mod.normalize_keywords(42) == []


def test_chat_bridge_passes_keywords_through() -> None:
    from types import SimpleNamespace

    from app.routers.chat.action_tool_params import _normalize_lightrag_query_params
    from app.services.llm.structured_response import LLMAction

    raw = {"query": "endolysin", "hl_keywords": ["cell wall binding"], "ll_keywords": ["LysK"], "junk": 1}
    params = _normalize_lightrag_query_params(
        SimpleNamespace(),
        LLMAction(kind="tool_operation", name="lightrag_query", parameters=raw),
        "lightrag_query",
        raw,
    )
    assert params["hl_keywords"] == ["cell wall binding"]
    assert params["ll_keywords"] == ["LysK"]
    assert "junk" not in params


def test_native_schema_declares_keyword_parameters() -> None:
    from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT

    entry = NATIVE_TOOL_CONTENT["lightrag_query"]
    assert entry["parameters"] is lightrag_mod.PARAMETERS_SCHEMA
    props = entry["parameters"]["properties"]
    assert props["ll_keywords"]["type"] == "array" and props["hl_keywords"]["type"] == "array"
    assert "ll_keywords" in entry["description"] and "hl_keywords" in entry["description"]


def test_sanitized_result_keeps_keyword_provenance() -> None:
    from app.routers.chat.tool_results import sanitize_tool_result

    sanitized = sanitize_tool_result(
        "lightrag_query",
        _handler_result(keywords={"source": "agent", "high_level": ["cell wall binding"], "low_level": ["LysK"]}),
    )
    assert sanitized["keywords"] == {"source": "agent", "high_level": ["cell wall binding"], "low_level": ["LysK"]}
