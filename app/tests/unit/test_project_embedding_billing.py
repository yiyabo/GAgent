"""Tests for opt-in per-project billing in the embedding clients.

EMBEDDING_PROJECT_BILLING gates the GLM/Qwen embedding HTTP clients onto the
session's sub2api project credential (chat_url with /chat/completions swapped
to /embeddings). Default (switch off) must stay on the configured admin
channel, and every degradation fails open back to admin with a warning.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app import llm as llm_mod
from app.services.embeddings import glm_api_client as glm_client_mod
from app.services.embeddings import qwen_embedding_client as qwen_client_mod
from app.services.embeddings.glm_api_client import GLMApiClient
from app.services.embeddings.qwen_embedding_client import QwenEmbeddingClient
from app.services.foundation import llm_config

# Fake stand-in tokens for assertions (not real credentials).
PROJ_KEY = "unit-test-project-token"
ADMIN_KEY = "unit-test-admin-token"
ADMIN_EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
PROJECT_EMBED_URL = "https://sub2api.medicalheart.cn/v1/embeddings"


@pytest.fixture(autouse=True)
def _clean_cred_state():
    llm_mod._SESSION_LLM_CRED_REGISTRY.clear()
    token = llm_mod._project_llm_creds.set(None)
    llm_config.validate_project_gateway_base_url.cache_clear()
    yield
    llm_mod._project_llm_creds.reset(token)
    llm_mod._SESSION_LLM_CRED_REGISTRY.clear()
    llm_config.validate_project_gateway_base_url.cache_clear()


def _allow_sub2api_host(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PLATFORM_LLM_ALLOWED_HOSTS", "sub2api.medicalheart.cn")
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


def _register_project_creds(monkeypatch) -> None:
    _allow_sub2api_host(monkeypatch)
    ok = llm_mod.register_project_llm_credentials(
        session_id="s1",
        api_key=PROJ_KEY,
        base_url="https://sub2api.medicalheart.cn",
    )
    assert ok is True


def _inject_malformed_creds() -> None:
    """Bypass register validation to simulate a chat_url of unexpected shape."""
    llm_mod._project_llm_creds.set(
        {"api_key": PROJ_KEY, "chat_url": "https://sub2api.medicalheart.cn/v1/responses"}
    )


def _glm_client() -> GLMApiClient:
    config = SimpleNamespace(
        api_key=ADMIN_KEY,
        api_url=ADMIN_EMBED_URL,
        embedding_model="glm-embedding",
        max_retries=1,
        retry_delay=0,
        request_timeout=5,
        mock_mode=False,
    )
    return GLMApiClient(config)


def _qwen_client() -> QwenEmbeddingClient:
    config = SimpleNamespace(
        qwen_embedding_api_url=ADMIN_EMBED_URL,
        qwen_embedding_model="text-embedding-v4",
        qwen_embedding_dimension=1536,
        qwen_api_key=ADMIN_KEY,
        max_retries=1,
        retry_delay=0,
        request_timeout=5,
    )
    return QwenEmbeddingClient(config)


class _FakeSyncResponse:
    status_code = 200
    text = "OK"

    def json(self):
        return {"data": [{"embedding": [0.1, 0.2], "index": 0}], "model": "m", "usage": {}}


class _RecordedSyncPost:
    """Monkeypatchable stand-in for requests.Session.post."""

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeSyncResponse()


class _FakeAsyncResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return {"data": [{"embedding": [0.1, 0.2], "index": 0}]}

    async def text(self):
        return "OK"


class _RecordedAsyncSession:
    """Stand-in for aiohttp.ClientSession (post returns an async CM)."""

    def __init__(self) -> None:
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeAsyncResponse()


def _assert_admin_channel(call: dict) -> None:
    assert call["url"] == ADMIN_EMBED_URL
    assert call["headers"]["Authorization"] == f"Bearer {ADMIN_KEY}"


def _assert_project_channel(call: dict) -> None:
    assert call["url"] == PROJECT_EMBED_URL
    assert call["headers"]["Authorization"] == f"Bearer {PROJ_KEY}"


def _assert_billing_fallback_warning(caplog, logger_name: str) -> None:
    warnings = [
        r for r in caplog.records
        if r.name == logger_name and r.levelno == logging.WARNING and "[BILLING]" in r.message
    ]
    assert warnings, f"expected a [BILLING] fallback warning from {logger_name}"
    assert "falling back to admin embedding credential" in warnings[0].message
    assert ADMIN_KEY not in warnings[0].message  # key logged only as a prefix


class TestBillingToggle:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_truthy_values_enable(self, monkeypatch, value) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", value)
        assert glm_client_mod._embedding_project_billing_enabled() is True
        assert qwen_client_mod._embedding_project_billing_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "on", "enabled", ""])
    def test_other_values_disable(self, monkeypatch, value) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", value)
        assert glm_client_mod._embedding_project_billing_enabled() is False
        assert qwen_client_mod._embedding_project_billing_enabled() is False

    def test_default_off(self, monkeypatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROJECT_BILLING", raising=False)
        assert glm_client_mod._embedding_project_billing_enabled() is False
        assert qwen_client_mod._embedding_project_billing_enabled() is False


class TestGLMClientBilling:
    LOGGER = "app.services.embeddings.glm_api_client"

    def test_switch_off_keeps_admin_channel_with_project_creds(self, monkeypatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROJECT_BILLING", raising=False)
        _register_project_creds(monkeypatch)
        client = _glm_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client.session, "post", post)
        client.get_embeddings_from_api(["hello"])
        assert len(post.calls) == 1
        _assert_admin_channel(post.calls[0])

    def test_switch_on_uses_project_key_and_derived_url(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        _register_project_creds(monkeypatch)
        client = _glm_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client.session, "post", post)
        client.get_embeddings_from_api(["hello"])
        assert len(post.calls) == 1
        _assert_project_channel(post.calls[0])

    def test_switch_on_without_creds_falls_back_with_warning(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        client = _glm_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client.session, "post", post)
        with caplog.at_level(logging.WARNING):
            client.get_embeddings_from_api(["hello"])
        assert len(post.calls) == 1
        _assert_admin_channel(post.calls[0])
        _assert_billing_fallback_warning(caplog, self.LOGGER)

    def test_switch_on_malformed_chat_url_falls_back(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        _inject_malformed_creds()
        client = _glm_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client.session, "post", post)
        with caplog.at_level(logging.WARNING):
            client.get_embeddings_from_api(["hello"])
        assert len(post.calls) == 1
        _assert_admin_channel(post.calls[0])
        _assert_billing_fallback_warning(caplog, self.LOGGER)


class TestQwenClientBilling:
    LOGGER = "app.services.embeddings.qwen_embedding_client"

    def test_switch_off_keeps_admin_channel_with_project_creds(self, monkeypatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROJECT_BILLING", raising=False)
        _register_project_creds(monkeypatch)
        client = _qwen_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client._sync_session, "post", post)
        client.get_embeddings(["hello"])
        assert len(post.calls) == 1
        _assert_admin_channel(post.calls[0])

    def test_switch_on_uses_project_key_and_derived_url(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        _register_project_creds(monkeypatch)
        client = _qwen_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client._sync_session, "post", post)
        client.get_embeddings(["hello"])
        assert len(post.calls) == 1
        _assert_project_channel(post.calls[0])

    async def test_switch_on_uses_project_key_and_derived_url_async(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        _register_project_creds(monkeypatch)
        client = _qwen_client()
        session = _RecordedAsyncSession()
        monkeypatch.setattr(client, "_get_async_session", lambda: session)
        await client.get_embeddings_async(["hello"])
        assert len(session.calls) == 1
        _assert_project_channel(session.calls[0])

    def test_switch_on_without_creds_falls_back_with_warning(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        client = _qwen_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client._sync_session, "post", post)
        with caplog.at_level(logging.WARNING):
            client.get_embeddings(["hello"])
        assert len(post.calls) == 1
        _assert_admin_channel(post.calls[0])
        _assert_billing_fallback_warning(caplog, self.LOGGER)

    def test_switch_on_malformed_chat_url_falls_back(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("EMBEDDING_PROJECT_BILLING", "1")
        _inject_malformed_creds()
        client = _qwen_client()
        post = _RecordedSyncPost()
        monkeypatch.setattr(client._sync_session, "post", post)
        with caplog.at_level(logging.WARNING):
            client.get_embeddings(["hello"])
        assert len(post.calls) == 1
        _assert_admin_channel(post.calls[0])
        _assert_billing_fallback_warning(caplog, self.LOGGER)
