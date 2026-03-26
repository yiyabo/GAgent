"""Unit tests for DashScope Responses API payload parsing (web_search)."""

import asyncio

import pytest

from app.config import SearchSettings
from tool_box.tools_impl.web_search.exceptions import WebSearchError
from tool_box.tools_impl.web_search.handler import _auto_parallel_queries, web_search_handler
from tool_box.tools_impl.web_search.providers import builtin
from tool_box.tools_impl.web_search.result import WebSearchResult
from tool_box.tools_impl.web_search.providers.builtin import extract_answer_from_responses_payload


def test_extract_answer_from_responses_payload_output_text() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Today is sunny.",
                        "annotations": [
                            {
                                "url": "https://weather.example/now",
                                "title": "Example Weather",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    answer, refs, stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "sunny" in answer
    assert len(refs) == 1
    assert refs[0]["url"] == "https://weather.example/now"
    assert stats["from_annotations"] == 1


def test_extract_answer_falls_back_to_urls_in_text() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "See https://a.example/x for details.", "annotations": []}],
            }
        ]
    }
    answer, refs, stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "a.example" in answer
    assert any("a.example" in r["url"] for r in refs)
    assert stats["from_text"] >= 1


def test_extract_answer_nested_tool_block_url() -> None:
    """Citations sometimes live under non-message output items (tool / search blocks)."""
    data = {
        "output": [
            {
                "type": "custom_web_search",
                "results": [
                    {"title": "News", "url": "https://news.example/a", "snippet": "Line"},
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Brief summary without links.",
                        "annotations": [],
                    }
                ],
            },
        ]
    }
    answer, refs, stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "summary" in answer
    assert len(refs) == 1
    assert refs[0]["url"] == "https://news.example/a"
    assert stats["from_tree"] >= 1


def test_extract_markdown_link_from_text() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Read [Paper](https://paper.example/p.pdf) for details.",
                        "annotations": [],
                    }
                ],
            }
        ]
    }
    answer, refs, _stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "Paper" in answer or "paper.example" in answer
    assert any(r["url"] == "https://paper.example/p.pdf" for r in refs)


def test_builtin_search_wraps_timeout_with_exception_type_and_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomResponse:
        async def __aenter__(self):
            raise TimeoutError()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _BoomSession:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            _ = (args, kwargs)
            return _BoomResponse()

    monkeypatch.setattr(builtin.aiohttp, "ClientSession", _BoomSession)

    settings = SearchSettings(qwen_api_key="test-key", builtin_request_timeout=1.0)

    with pytest.raises(WebSearchError) as exc_info:
        asyncio.run(
            builtin.search(
                query="AnewSampling",
                max_results=5,
                settings=settings,
            )
        )

    err = exc_info.value
    assert err.code == "request_failed"
    assert "TimeoutError after" in err.message
    assert err.meta["exception_type"] == "TimeoutError"


def test_auto_parallel_queries_splits_broad_comparison_query() -> None:
    queries = _auto_parallel_queries(
        "AlphaFold3 RoseTTAFold All-Atom molecular dynamics sampling comparison 2025"
    )
    assert len(queries) >= 2
    assert any("AlphaFold3" in item for item in queries)
    assert any("RoseTTAFold" in item for item in queries)


def test_web_search_handler_aggregates_parallel_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_dispatch(*, query, provider, max_results, settings=None, **kwargs):
        _ = (provider, max_results, settings, kwargs)
        return WebSearchResult(
            query=query,
            provider="builtin",
            response=f"summary for {query}",
            results=[
                {
                    "title": query,
                    "url": f"https://example.com/{query.replace(' ', '_')}",
                    "snippet": "snippet",
                    "source": "example.com",
                }
            ],
            meta={"query": query},
        )

    monkeypatch.setattr("tool_box.tools_impl.web_search.handler.dispatch", _fake_dispatch)

    payload = asyncio.run(
        web_search_handler(
            query="compare broad query",
            queries=["AlphaFold3 2025 paper", "RoseTTAFold 2025 paper"],
            max_results=5,
        )
    )

    assert payload["success"] is True
    assert payload["meta"]["parallel_search"] is True
    assert payload["meta"]["successful_queries"] == [
        "AlphaFold3 2025 paper",
        "RoseTTAFold 2025 paper",
    ]
    assert "summary for AlphaFold3 2025 paper" in payload["response"]
    assert "summary for RoseTTAFold 2025 paper" in payload["response"]
