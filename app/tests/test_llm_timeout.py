from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from app.llm import LLMClient, reset_default_client, _get_shared_sync_client, _make_request_timeout
from app.services.foundation.settings import get_settings
from app.services.llm.llm_service import get_llm_service_for_provider


def test_llm_client_timeout_none_when_zero(monkeypatch) -> None:
    """When timeout=0 is passed, it should normalise to None (no timeout)."""
    client = LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=0,
        retries=0,
    )
    assert client.timeout is None


def test_llm_client_chat_uses_shared_sync_client(monkeypatch) -> None:
    """Verify chat() dispatches through the shared httpx.Client pool."""
    fake_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "ok"}}]},
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = fake_response

    monkeypatch.setattr("app.llm._get_shared_sync_client", lambda: mock_client)

    client = LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=0,
        retries=0,
    )

    result = client.chat("hello")
    assert result == "ok"
    mock_client.post.assert_called_once()

    # Timeout should be None (since timeout=0 normalises to None)
    call_kwargs = mock_client.post.call_args
    assert call_kwargs.kwargs.get("timeout") is None


def test_llm_client_stream_timeout_defaults_to_longer_window(monkeypatch) -> None:
    monkeypatch.delenv("LLM_STREAM_TIMEOUT", raising=False)
    get_settings.cache_clear()
    try:
        client = LLMClient(
            provider="qwen",
            api_key="test-key",
            url="https://example.com/v1/chat/completions",
            model="qwen-test",
            timeout=60,
            retries=0,
        )
        assert client.timeout == 60
        assert client.stream_timeout == 300
    finally:
        get_settings.cache_clear()


def test_llm_client_stream_timeout_can_be_overridden() -> None:
    client = LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=60,
        stream_timeout=120,
        retries=0,
    )

    assert client.timeout == 60
    assert client.stream_timeout == 120


# -- _make_request_timeout semantics --


def test_make_request_timeout_none_passthrough() -> None:
    assert _make_request_timeout(None) is None


def test_make_request_timeout_connect_capped_by_overall() -> None:
    """When overall < default connect cap, connect must equal overall."""
    t = _make_request_timeout(2.0)
    assert t is not None
    assert t.connect == 2.0
    assert t.read == 2.0


def test_make_request_timeout_connect_uses_default_when_overall_is_large() -> None:
    """When overall > default connect cap, connect stays at the default cap."""
    t = _make_request_timeout(120.0)
    assert t is not None
    assert t.connect == 10.0
    assert t.read == 120.0


def test_chat_passes_capped_connect_timeout(monkeypatch) -> None:
    """chat() with a small timeout must produce connect <= timeout."""
    fake_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "ok"}}]},
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = fake_response
    monkeypatch.setattr("app.llm._get_shared_sync_client", lambda: mock_client)

    client = LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=5,
        retries=0,
    )
    client.chat("hello")

    call_kwargs = mock_client.post.call_args
    timeout_arg = call_kwargs.kwargs.get("timeout")
    assert timeout_arg is not None
    assert timeout_arg.connect == 5.0
    assert timeout_arg.read == 5.0


def test_reset_default_client_clears_provider_service_cache(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "test-key-a")
    monkeypatch.setenv("QWEN_API_URL", "https://example-a.com/v1/chat/completions")
    get_settings.cache_clear()
    reset_default_client()
    first = get_llm_service_for_provider("qwen", "qwen-test")
    first_url = getattr(first.client, "url", None)

    monkeypatch.setenv("QWEN_API_KEY", "test-key-b")
    monkeypatch.setenv("QWEN_API_URL", "https://example-b.com/v1/chat/completions")
    get_settings.cache_clear()
    reset_default_client()
    second = get_llm_service_for_provider("qwen", "qwen-test")

    assert first is not second
    assert first_url == "https://example-a.com/v1/chat/completions"
    assert getattr(second.client, "url", None) == "https://example-b.com/v1/chat/completions"
