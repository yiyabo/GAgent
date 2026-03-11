from __future__ import annotations

import json

from app.llm import LLMClient


class _DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_DummyResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_llm_client_timeout_zero_disables_urlopen_timeout(monkeypatch) -> None:
    captured = {}

    def _fake_urlopen(*args, **kwargs):
        captured["args_len"] = len(args)
        captured["timeout_kw"] = kwargs.get("timeout", "__missing__")
        return _DummyResponse(
            {"choices": [{"message": {"content": "ok"}}]}
        )

    monkeypatch.setattr("app.llm.request.urlopen", _fake_urlopen)

    client = LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=0,
        retries=0,
    )

    assert client.timeout is None
    assert client.chat("hello") == "ok"
    assert captured["args_len"] == 1
    assert captured["timeout_kw"] == "__missing__"
