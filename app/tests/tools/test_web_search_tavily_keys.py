"""Tavily provider with several free API keys: rotation, failover, and key hygiene."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from app.config import get_search_settings
from tool_box import web_search_text
from tool_box.tools_impl.web_search import handler as search_handler
from tool_box.tools_impl.web_search.exceptions import WebSearchError
from tool_box.tools_impl.web_search.providers import tavily_keys
from tool_box.tools_impl.web_search.providers import tavily_rest

KEYS = ("tvly-test-a", "tvly-test-b", "tvly-test-c")
_OK = json.dumps({"results": [{"title": "T", "url": "https://example.org/x", "content": "snippet"}]})


class _Resp:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._body


class _Session:
    calls: list = []
    responder = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        _Session.calls.append(key)
        status, body = _Session.responder(key)
        return _Resp(status, body)


class _Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def _settings(keys=KEYS, single=None):
    return SimpleNamespace(
        tavily_api_keys=tuple(keys),
        tavily_api_key=single,
        tavily_api_url="https://tavily.example/search",
        tavily_timeout=5.0,
        tavily_search_depth="basic",
        tavily_topic="general",
        tavily_include_answer=False,
        tavily_include_raw_content=False,
        tavily_time_range=None,
        tavily_auto_parameters=False,
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clk = _Clock()
    monkeypatch.setattr(tavily_keys.POOL, "_clock", clk)
    tavily_keys.POOL.reset()
    monkeypatch.setattr(tavily_rest.aiohttp, "ClientSession", _Session)
    _Session.calls = []
    _Session.responder = lambda key: (200, _OK)
    yield clk
    tavily_keys.POOL.reset()


def _search(settings=None):
    return asyncio.run(tavily_rest.search(query="q", max_results=3, settings=settings or _settings()))


def test_calls_rotate_across_keys(clock) -> None:
    for _ in range(6):
        assert _search().success is True
    assert _Session.calls == list(KEYS) * 2


@pytest.mark.parametrize("status", [432, 433])
def test_a_key_out_of_quota_is_skipped_and_the_call_still_succeeds(clock, status) -> None:
    _Session.responder = lambda key: (status, "limit") if key == KEYS[0] else (200, _OK)

    assert _search().success is True
    assert _Session.calls == [KEYS[0], KEYS[1]]

    _Session.calls = []
    for _ in range(4):
        assert _search().success is True
    assert KEYS[0] not in _Session.calls

    clock.now += 3601
    _Session.calls = []
    _Session.responder = lambda key: (200, _OK)
    for _ in range(3):
        _search()
    assert KEYS[0] in _Session.calls


def test_a_rate_limited_key_rests_for_a_minute(clock) -> None:
    _Session.responder = lambda key: (429, "slow down") if key == KEYS[0] else (200, _OK)
    _search()
    _Session.calls = []
    for _ in range(3):
        _search()
    assert KEYS[0] not in _Session.calls

    clock.now += 61
    _Session.responder = lambda key: (200, _OK)
    _Session.calls = []
    for _ in range(3):
        _search()
    assert KEYS[0] in _Session.calls


def test_every_key_exhausted_says_so_and_then_fails_without_calling_the_api(clock) -> None:
    _Session.responder = lambda key: (432, "limit")
    with pytest.raises(WebSearchError) as first:
        _search()
    err = first.value
    assert err.code == "quota_exhausted"
    assert err.meta["keys"] == 3 and err.meta["status"] == 432
    assert "provider='builtin'" in err.message
    assert len(_Session.calls) == 3

    with pytest.raises(WebSearchError) as second:
        _search()
    assert second.value.code == "quota_exhausted"
    assert len(_Session.calls) == 3, "keys that are resting must not be tried again"


def test_other_http_errors_do_not_rest_a_key_or_fail_over(clock) -> None:
    _Session.responder = lambda key: (500, "boom") if key == KEYS[0] else (200, _OK)
    with pytest.raises(WebSearchError) as excinfo:
        _search()
    assert excinfo.value.code == "http_error"
    assert _Session.calls == [KEYS[0]]
    assert tavily_keys.POOL.seconds_until_available([KEYS[0]]) == 0


def test_a_single_legacy_key_still_works(clock) -> None:
    assert _search(_settings(keys=(), single="tvly-only")).success is True
    assert _Session.calls == ["tvly-only"]


def test_no_key_is_reported_as_missing(clock) -> None:
    with pytest.raises(WebSearchError) as excinfo:
        _search(_settings(keys=(), single=None))
    assert excinfo.value.code == "missing_api_key"


def test_keys_never_reach_errors_or_logs(clock, caplog: pytest.LogCaptureFixture) -> None:
    _Session.responder = lambda key: (401, "unauthorized")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(WebSearchError) as excinfo:
            _search()
    shown = f"{excinfo.value.message} {excinfo.value.meta} {caplog.text}"
    assert not any(key in shown for key in KEYS)
    assert "key #1 of 3" in caplog.text


def test_the_handler_reports_exhaustion_as_a_failed_search(clock, monkeypatch: pytest.MonkeyPatch) -> None:
    _Session.responder = lambda key: (432, "limit")
    monkeypatch.setattr(search_handler, "get_search_settings", lambda: _settings())
    result = asyncio.run(search_handler.web_search_handler(query="phage depolymerase", provider="tavily"))
    assert result["success"] is False
    assert result["code"] == "quota_exhausted"
    assert result["provider"] == "tavily"


def test_settings_collect_keys_from_both_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "k1")
    monkeypatch.setenv("TAVILY_API_KEYS", "k2, k3;k1\nk4")
    get_search_settings.cache_clear()
    try:
        settings = get_search_settings()
        assert settings.tavily_api_keys == ("k1", "k2", "k3", "k4")
        assert settings.tavily_api_key == "k1"
    finally:
        get_search_settings.cache_clear()


def test_settings_without_keys_have_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
    get_search_settings.cache_clear()
    try:
        settings = get_search_settings()
        assert settings.tavily_api_keys == ()
        assert settings.tavily_api_key is None
    finally:
        get_search_settings.cache_clear()


def test_tool_text_follows_the_default_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFAULT_WEB_SEARCH_PROVIDER", raising=False)
    assert web_search_text.search_description("kept") == "kept"
    assert web_search_text.provider_param_description("kept") == "kept"

    monkeypatch.setenv("DEFAULT_WEB_SEARCH_PROVIDER", "tavily")
    text = web_search_text.search_description("kept")
    assert "`tavily`" in text and "a few seconds" in text and "two minutes" not in text
    assert "builtin" in web_search_text.provider_param_description("kept")
