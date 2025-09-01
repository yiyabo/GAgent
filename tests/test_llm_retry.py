import io
import json

import pytest

from app.llm import LLMClient


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_llm_retry_success_after_transient_500(monkeypatch):
    # Ensure non-mock mode and present API key
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setenv("GLM_API_KEY", "x")

    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            from urllib.error import HTTPError

            raise HTTPError(
                "http://x",
                502,
                "Bad Gateway",
                hdrs=None,
                fp=io.BytesIO(b"server error"),
            )
        return _FakeResp({"choices": [{"message": {"content": "OK"}}]})

    # Speed up backoff
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    monkeypatch.setattr("app.llm.request.urlopen", fake_urlopen)

    client = LLMClient(retries=2, backoff_base=0.0)
    out = client.chat("hello")
    assert out == "OK"
    assert calls["n"] == 2


def test_llm_no_retry_on_4xx(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setenv("GLM_API_KEY", "x")

    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        from urllib.error import HTTPError

        raise HTTPError(
            "http://x", 400, "Bad Request", hdrs=None, fp=io.BytesIO(b"bad")
        )

    monkeypatch.setattr("app.llm.request.urlopen", fake_urlopen)

    client = LLMClient(retries=3, backoff_base=0.0)
    with pytest.raises(RuntimeError) as e:
        client.chat("hello")
    assert "LLM HTTPError: 400" in str(e.value)
    assert calls["n"] == 1  # no retry on 4xx


def test_llm_give_up_after_max_retries_on_network_error(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setenv("GLM_API_KEY", "x")

    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        raise OSError("boom")

    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    monkeypatch.setattr("app.llm.request.urlopen", fake_urlopen)

    client = LLMClient(retries=2, backoff_base=0.0)
    with pytest.raises(RuntimeError) as e:
        client.chat("hello")
    assert "LLM request failed" in str(e.value)
    assert calls["n"] == 3  # initial + 2 retries
