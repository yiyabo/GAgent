from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from app.llm import _OutboundRateLimiter, _pool_limits, _sync_pool_limits
from app.services.foundation.settings import get_settings
from app.services.llm.llm_service import (
    LLMProviderError,
    LLMService,
    _classify_llm_exception,
)


def test_classify_429_is_retryable_with_delay_hint() -> None:
    exc = RuntimeError("LLM request failed: LLM HTTP 429: Request rate increased too quickly.")
    classified = _classify_llm_exception(exc)
    assert classified is not None
    assert classified.status_code == 429
    assert classified.retryable is True
    assert classified.retry_delay_hint == 5.0


def test_classify_429_honors_retry_after_header_value() -> None:
    exc = RuntimeError("LLM HTTP 429: slow down (retry_after=17)")
    classified = _classify_llm_exception(exc)
    assert classified is not None
    assert classified.retry_delay_hint == 17.0


def test_classify_400_is_not_retryable() -> None:
    exc = RuntimeError("LLM HTTP 400: Input data may contain inappropriate content.")
    classified = _classify_llm_exception(exc)
    assert classified is not None
    assert classified.status_code == 400
    assert classified.retryable is False


def test_classify_quota_is_not_retryable() -> None:
    exc = RuntimeError("LLM HTTP 429: insufficient_quota")
    classified = _classify_llm_exception(exc)
    assert classified is not None
    assert classified.retryable is False


def test_classify_transient_error_stays_on_default_path() -> None:
    assert _classify_llm_exception(RuntimeError("LLM request failed: ")) is None
    assert _classify_llm_exception(ConnectionError("boom")) is None


def test_chat_async_does_not_retry_400() -> None:
    client = MagicMock()
    calls = {"n": 0}

    async def _fail(prompt, **kwargs):
        calls["n"] += 1
        raise RuntimeError("LLM HTTP 400: inappropriate content")

    client.chat_async = _fail
    service = LLMService(client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        asyncio.run(service.chat_async("hi"))
    assert exc_info.value.retryable is False
    assert calls["n"] == 1


def test_chat_async_429_uses_long_backoff() -> None:
    client = MagicMock()
    sleeps: list[float] = []

    async def _fail(prompt, **kwargs):
        raise RuntimeError("LLM HTTP 429: rate increased too quickly")

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    client.chat_async = _fail
    service = LLMService(client=client)
    service._retry_attempts = 2

    original_sleep = asyncio.sleep
    asyncio.sleep = _fake_sleep
    try:
        with pytest.raises(LLMProviderError):
            asyncio.run(service.chat_async("hi", retry_delay=0.5))
    finally:
        asyncio.sleep = original_sleep

    assert sleeps and all(s >= 5.0 for s in sleeps)


def test_pool_limits_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_POOL_MAX_CONNECTIONS", "500")
    monkeypatch.setenv("LLM_POOL_MAX_KEEPALIVE", "100")
    monkeypatch.setenv("LLM_SYNC_POOL_MAX_CONNECTIONS", "77")
    get_settings.cache_clear()
    try:
        assert _pool_limits().max_connections == 500
        assert _pool_limits().max_keepalive_connections == 100
        assert _sync_pool_limits().max_connections == 77
    finally:
        get_settings.cache_clear()


def test_outbound_limiter_disabled_by_default() -> None:
    limiter = _OutboundRateLimiter(window_seconds=0.2)
    started = time.monotonic()
    for _ in range(50):
        limiter.acquire()
    assert time.monotonic() - started < 1.0


def test_outbound_limiter_throttles(monkeypatch) -> None:
    limiter = _OutboundRateLimiter(window_seconds=0.3)
    monkeypatch.setattr(limiter, "_rpm", lambda: 1)

    limiter.acquire()
    started = time.monotonic()
    limiter.acquire()
    waited = time.monotonic() - started
    assert waited >= 0.2

    time.sleep(0.35)
    started = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - started < 0.2
