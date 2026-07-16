from __future__ import annotations

import asyncio

import pytest

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
