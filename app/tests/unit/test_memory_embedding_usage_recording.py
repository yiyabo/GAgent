"""Embedding usage recording: llm_usage rows with billing attribution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app import llm as llm_mod
from app.repository import llm_usage as llm_usage_mod
from app.services.embeddings.glm_api_client import GLMApiClient
from app.services.embeddings.local_embedding_client import LocalEmbeddingClient
from app.services.embeddings.qwen_embedding_client import QwenEmbeddingClient
from app.services.embeddings.usage_recorder import (
    estimate_embedding_tokens,
    record_embedding_usage,
)

ADMIN_EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        llm_usage_mod,
        "log_llm_usage",
        lambda **kwargs: rows.append(kwargs),
    )
    return rows


# --- recorder unit -----------------------------------------------------------


def test_response_usage_preferred_over_estimate(recorded: List[Dict[str, Any]]) -> None:
    record_embedding_usage(
        provider="qwen_embedding",
        model="text-embedding-v4",
        texts=["a" * 400],
        response_usage={"prompt_tokens": 123},
    )
    assert len(recorded) == 1
    row = recorded[0]
    assert row["provider"] == "qwen_embedding"
    assert row["model"] == "text-embedding-v4"
    assert row["prompt_tokens"] == 123
    assert row["completion_tokens"] == 0
    assert row["billing_key"] == "internal.memory_embedding"
    assert row["call_status"] == "ok"


def test_estimate_fallback_when_usage_missing(recorded: List[Dict[str, Any]]) -> None:
    record_embedding_usage(provider="glm_embedding", model="glm-embedding", texts=["abcd" * 100])
    assert recorded[0]["prompt_tokens"] == estimate_embedding_tokens(["abcd" * 100]) == 100


def test_attribution_inherited_and_tool_not_leaked(recorded: List[Dict[str, Any]]) -> None:
    token = llm_mod.set_usage_context(
        session_id="sess_x",
        plan_id=7,
        task_id=9,
        call_purpose="deep_think_iteration",
        run_id="run_x",
        tool_name="code_executor",
    )
    try:
        record_embedding_usage(provider="glm_embedding", model="m", texts=["hi"])
        # the ambient context is restored immediately after recording
        assert (llm_mod._usage_context.get() or {}).get("call_purpose") == "deep_think_iteration"
    finally:
        llm_mod._usage_context.reset(token)
    row = recorded[0]
    assert row["session_id"] == "sess_x"
    assert row["plan_id"] == 7
    assert row["task_id"] == 9
    assert row["run_id"] == "run_x"
    # billing key pinned to the embedding purpose, not the ambient tool
    assert row["billing_key"] == "internal.memory_embedding"
    assert row["tool_name"] is None


def test_record_never_raises_on_bad_input(recorded: List[Dict[str, Any]]) -> None:
    record_embedding_usage(provider="p", model="m", texts=None)  # type: ignore[arg-type]
    record_embedding_usage(provider="p", model="m", texts=["a"], response_usage={"prompt_tokens": "NaN"})
    assert len(recorded) == 2


# --- client integration -------------------------------------------------------


def _glm_client() -> GLMApiClient:
    config = SimpleNamespace(
        api_key="unit-test-admin-token",
        api_url=ADMIN_EMBED_URL,
        embedding_model="glm-embedding",
        max_retries=1,
        retry_delay=0,
        request_timeout=5,
        mock_mode=False,
    )
    return GLMApiClient(config)


class _FakeResponse:
    def __init__(self, status_code: int = 200, usage: Any = None) -> None:
        self.status_code = status_code
        self.text = "OK" if status_code == 200 else "boom"
        self._usage = usage if usage is not None else {}

    def json(self) -> Dict[str, Any]:
        return {
            "data": [{"embedding": [0.1, 0.2], "index": 0}],
            "model": "glm-embedding",
            "usage": self._usage,
        }


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def post(self, url, headers=None, json=None, timeout=None):
        return self._response


def test_glm_client_records_success(recorded: List[Dict[str, Any]]) -> None:
    client = _glm_client()
    client.session = _FakeSession(_FakeResponse(200, {"prompt_tokens": 42, "total_tokens": 42}))
    embeddings = client.get_embeddings(["hello world"])
    assert embeddings == [[0.1, 0.2]]
    assert len(recorded) == 1
    row = recorded[0]
    assert row["provider"] == "glm_embedding"
    assert row["prompt_tokens"] == 42
    assert row["call_status"] == "ok"


def test_glm_client_records_http_failure(recorded: List[Dict[str, Any]]) -> None:
    client = _glm_client()
    client.session = _FakeSession(_FakeResponse(500))
    with pytest.raises(Exception):
        client.get_embeddings(["hello world"])
    assert len(recorded) == 1
    assert recorded[0]["call_status"] == "http_500"


def _qwen_client() -> QwenEmbeddingClient:
    config = SimpleNamespace(
        qwen_embedding_api_url=ADMIN_EMBED_URL,
        qwen_embedding_model="text-embedding-v4",
        qwen_embedding_dimension=1536,
        qwen_api_key="unit-test-admin-token",
        max_retries=1,
        retry_delay=0,
        request_timeout=5,
    )
    return QwenEmbeddingClient(config)


def test_qwen_client_records_success(recorded: List[Dict[str, Any]]) -> None:
    client = _qwen_client()
    client._sync_session = _FakeSession(_FakeResponse(200, {"total_tokens": 55}))
    embeddings = client.get_embeddings(["hello world"])
    assert embeddings == [[0.1, 0.2]]
    assert len(recorded) == 1
    row = recorded[0]
    assert row["provider"] == "qwen_embedding"
    assert row["prompt_tokens"] == 55


def test_local_client_mock_mode_not_recorded(recorded: List[Dict[str, Any]]) -> None:
    config = SimpleNamespace(
        local_embedding_model="mock-model",
        embedding_dimension=8,
        mock_mode=True,
    )
    client = LocalEmbeddingClient(config)
    embeddings = client.get_embeddings(["hello"])
    assert embeddings and not recorded
