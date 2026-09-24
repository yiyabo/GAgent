"""Resilience tests for the builtin (platform Responses gateway) web_search provider."""

from __future__ import annotations

import asyncio
import json

import pytest

from tool_box.tools_impl.web_search.exceptions import WebSearchError
from tool_box.tools_impl.web_search.providers import builtin as builtin_provider

_OK_PAYLOAD = json.dumps(
    {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer text"}],
            }
        ]
    }
)

_NGINX_504 = (
    '<html>\r\n<head><title>504 Gateway Time-out</title></head>\r\n'
    "<body>\r\n<center><h1>504 Gateway Time-out</h1></center>\r\n"
    "<hr><center>nginx/1.31.2</center>\r\n</body>\r\n</html>\r\n"
)


class _FakeResponse:
    def __init__(self, status: int, body: str, headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._body


class _ScriptedSession:
    """aiohttp.ClientSession stand-in returning scripted outcomes per attempt."""

    outcomes: list = []
    attempts: int = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        _ScriptedSession.attempts += 1
        outcome = _ScriptedSession.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, body, headers = outcome
        return _FakeResponse(status, body, headers)


class _Settings:
    qwen_api_key = "test-key"
    qwen_responses_api_url = "https://gateway.example/v1/responses"
    qwen_responses_model = "qwen-test"
    qwen_model = "qwen-test"
    builtin_provider = "qwen"
    builtin_request_timeout = 5.0
    builtin_connect_timeout = 1.0
    builtin_retries = 2
    builtin_backoff_base = 0.0


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(builtin_provider.aiohttp, "ClientSession", _ScriptedSession)
    monkeypatch.delenv("QWEN_RESPONSES_LOCAL_URL", raising=False)
    _ScriptedSession.attempts = 0
    yield


def _settings(**overrides) -> _Settings:
    settings = _Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


@pytest.mark.usefixtures("_patched")
def test_retries_transient_504_then_succeeds() -> None:
    _ScriptedSession.outcomes = [
        (504, _NGINX_504, {}),
        (504, _NGINX_504, {}),
        (200, _OK_PAYLOAD, {}),
    ]
    result = asyncio.run(
        builtin_provider.search(query="q", max_results=2, settings=_settings())
    )
    assert result.success is True
    assert result.response == "answer text"
    assert _ScriptedSession.attempts == 3


@pytest.mark.usefixtures("_patched")
def test_exhausts_retries_on_persistent_504_with_clear_message() -> None:
    _ScriptedSession.outcomes = [
        (504, _NGINX_504, {}),
        (504, _NGINX_504, {}),
    ]
    with pytest.raises(WebSearchError) as excinfo:
        asyncio.run(
            builtin_provider.search(
                query="q",
                max_results=2,
                settings=_settings(builtin_retries=1),
            )
        )
    err = excinfo.value
    assert err.code == "http_error"
    assert _ScriptedSession.attempts == 2
    assert "HTTP 504" in err.message
    assert "gateway.example" in err.message
    assert "attempt 2/2" in err.message
    # nginx HTML must be sanitized into readable text, not dumped raw
    assert "<html>" not in err.message
    assert "504 Gateway Time-out" in err.message
    assert "overloaded or slow" in err.message
    assert err.meta["status"] == 504
    assert err.meta["attempts"] == 2


@pytest.mark.usefixtures("_patched")
def test_non_retryable_status_is_not_retried() -> None:
    _ScriptedSession.outcomes = [(400, '{"error": "bad request"}', {})]
    with pytest.raises(WebSearchError) as excinfo:
        asyncio.run(
            builtin_provider.search(query="q", max_results=2, settings=_settings())
        )
    assert excinfo.value.code == "http_error"
    assert _ScriptedSession.attempts == 1


@pytest.mark.usefixtures("_patched")
def test_network_timeouts_retry_then_request_failed() -> None:
    _ScriptedSession.outcomes = [
        asyncio.TimeoutError("timed out"),
        asyncio.TimeoutError("timed out"),
    ]
    with pytest.raises(WebSearchError) as excinfo:
        asyncio.run(
            builtin_provider.search(
                query="q",
                max_results=2,
                settings=_settings(builtin_retries=1),
            )
        )
    err = excinfo.value
    assert err.code == "request_failed"
    assert _ScriptedSession.attempts == 2
    assert "attempt 2/2" in err.message
    assert "gateway.example" in err.message
    assert err.meta["attempts"] == 2
    assert err.meta["exception_type"] == "TimeoutError"


@pytest.mark.usefixtures("_patched")
def test_retry_after_header_is_honored() -> None:
    _ScriptedSession.outcomes = [
        (429, "rate limited", {"Retry-After": "0.2"}),
        (200, _OK_PAYLOAD, {}),
    ]
    result = asyncio.run(
        builtin_provider.search(query="q", max_results=2, settings=_settings())
    )
    assert result.success is True
    assert _ScriptedSession.attempts == 2


def test_search_settings_defaults_include_retry_tuning(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.search_config import get_search_settings, reset_search_settings_cache

    for key in (
        "WEB_SEARCH_BUILTIN_RETRIES",
        "WEB_SEARCH_BUILTIN_BACKOFF_BASE",
        "WEB_SEARCH_BUILTIN_CONNECT_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_search_settings_cache()
    settings = get_search_settings()
    assert settings.builtin_retries == 2
    assert settings.builtin_backoff_base == 2.0
    assert settings.builtin_connect_timeout == 20.0
    reset_search_settings_cache()


def test_search_settings_retry_tuning_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.search_config import get_search_settings, reset_search_settings_cache

    monkeypatch.setenv("WEB_SEARCH_BUILTIN_RETRIES", "4")
    monkeypatch.setenv("WEB_SEARCH_BUILTIN_BACKOFF_BASE", "1.5")
    monkeypatch.setenv("WEB_SEARCH_BUILTIN_CONNECT_TIMEOUT", "7")
    reset_search_settings_cache()
    settings = get_search_settings()
    assert settings.builtin_retries == 4
    assert settings.builtin_backoff_base == 1.5
    assert settings.builtin_connect_timeout == 7.0
    reset_search_settings_cache()


def test_duck_typed_settings_without_new_fields_fall_back_to_defaults() -> None:
    retries, backoff_base, connect_timeout = builtin_provider._retry_tuning(_Settings())
    assert (retries, backoff_base, connect_timeout) == (2, 0.0, 1.0)

    class _LegacySettings:
        pass

    retries, backoff_base, connect_timeout = builtin_provider._retry_tuning(_LegacySettings())
    assert (retries, backoff_base, connect_timeout) == (2, 2.0, 20.0)
