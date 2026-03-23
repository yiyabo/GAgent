from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from app.llm import LLMClient, _get_shared_sync_client
from app.services.foundation.settings import get_settings


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
