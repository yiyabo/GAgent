"""Tests for per-project LLM credentials (billing split step 4).

Covers: platform-delivered credential validation, the session registry bridge
for worker threads, dynamic resolution in LLMClient request assembly, and the
delegated code_executor subprocess environment.
"""
from __future__ import annotations

import pytest

from app import llm as llm_mod
from app.services.foundation import llm_config

# Fake stand-in tokens for assertions (not real credentials).
PROJ_KEY = "unit-test-project-token"
PROJ_KEY_BRIDGE = "unit-test-bridge-token"
PROJ_KEY_XYZ = "unit-test-xyz-token"
ADMIN_KEY = "unit-test-admin-token"


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


class TestBaseUrlValidation:
    def test_plain_host_normalized_to_chat_completions(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        url = llm_config.validate_project_gateway_base_url("https://sub2api.medicalheart.cn")
        assert url == "https://sub2api.medicalheart.cn/v1/chat/completions"

    def test_v1_suffix_not_doubled(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        url = llm_config.validate_project_gateway_base_url("https://sub2api.medicalheart.cn/v1/")
        assert url == "https://sub2api.medicalheart.cn/v1/chat/completions"

    def test_disallowed_host_rejected(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        assert llm_config.validate_project_gateway_base_url("https://evil.example.com") is None

    def test_http_rejected_in_production(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        assert llm_config.validate_project_gateway_base_url("http://sub2api.medicalheart.cn") is None

    def test_empty_rejected(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        assert llm_config.validate_project_gateway_base_url("") is None
        assert llm_config.validate_project_gateway_base_url(None) is None


class TestRegisterAndResolve:
    def test_roundtrip(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        ok = llm_mod.register_project_llm_credentials(
            session_id="s1",
            api_key=PROJ_KEY,
            base_url="https://sub2api.medicalheart.cn",
        )
        assert ok is True
        creds = llm_mod.get_project_llm_credentials()
        assert creds is not None
        assert creds["api_key"] == PROJ_KEY
        assert creds["chat_url"] == "https://sub2api.medicalheart.cn/v1/chat/completions"
        assert creds["responses_url"] == "https://sub2api.medicalheart.cn/v1/responses"
        assert creds["embeddings_url"] == "https://sub2api.medicalheart.cn/v1/embeddings"

    def test_rejected_credential_fails_open(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        ok = llm_mod.register_project_llm_credentials(
            session_id="s1",
            api_key=PROJ_KEY,
            base_url="https://evil.example.com",
        )
        assert ok is False
        assert llm_mod.get_project_llm_credentials() is None

    def test_missing_values_resolve_to_none(self) -> None:
        assert llm_mod.get_project_llm_credentials() is None

    def test_registry_bridge_for_worker_threads(self, monkeypatch) -> None:
        """Plan-executor threads rebuild usage context from session id; the
        registry must bridge credentials into them without a ContextVar."""
        _allow_sub2api_host(monkeypatch)
        llm_mod.register_project_llm_credentials(
            session_id="s-thread",
            api_key=PROJ_KEY_BRIDGE,
            base_url="https://sub2api.medicalheart.cn",
        )
        llm_mod._project_llm_creds.set(None)  # simulate a fresh worker thread
        token = llm_mod.set_usage_context(
            session_id="s-thread", call_purpose="plan_task_execution"
        )
        try:
            creds = llm_mod.get_project_llm_credentials()
        finally:
            llm_mod.clear_usage_context(token)
        assert creds is not None
        assert creds["api_key"] == PROJ_KEY_BRIDGE

    def test_registry_not_leaked_across_sessions(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        llm_mod.register_project_llm_credentials(
            session_id="s-a",
            api_key=PROJ_KEY,
            base_url="https://sub2api.medicalheart.cn",
        )
        llm_mod._project_llm_creds.set(None)
        token = llm_mod.set_usage_context(session_id="s-b", call_purpose="chat_main")
        try:
            assert llm_mod.get_project_llm_credentials() is None
        finally:
            llm_mod.clear_usage_context(token)


class TestLLMClientResolution:
    def _client(self, monkeypatch) -> llm_mod.LLMClient:
        monkeypatch.setattr(llm_mod, "is_production", lambda: False)
        monkeypatch.setenv("QWEN_API_KEY", ADMIN_KEY)
        monkeypatch.delenv("QWEN_API_URL", raising=False)
        return llm_mod.LLMClient(provider="qwen")

    def test_headers_prefer_project_key(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        client = self._client(monkeypatch)
        assert client.api_key == ADMIN_KEY
        llm_mod.register_project_llm_credentials(
            session_id="s1",
            api_key=PROJ_KEY,
            base_url="https://sub2api.medicalheart.cn",
        )
        headers = client._build_headers()
        assert headers["Authorization"] == f"Bearer {PROJ_KEY}"
        assert client._effective_url() == "https://sub2api.medicalheart.cn/v1/chat/completions"

    def test_headers_fall_back_to_profile_key(self, monkeypatch) -> None:
        client = self._client(monkeypatch)
        headers = client._build_headers()
        assert headers["Authorization"] == f"Bearer {ADMIN_KEY}"
        assert client._effective_url() == client.url


class TestCodeExecutorEnv:
    def test_subprocess_env_prefers_project_key(self, monkeypatch) -> None:
        _allow_sub2api_host(monkeypatch)
        import tool_box.tools_impl.code_executor as ce

        class _Profile:
            api_key = ADMIN_KEY
            api_url = "https://sub2api.medicalheart.cn/v1/chat/completions"
            model = "qwen3.8-flash"

        monkeypatch.setattr(ce, "is_production", lambda: True)
        monkeypatch.setattr(ce, "platform_profile", lambda: _Profile())
        llm_mod.register_project_llm_credentials(
            session_id="s2",
            api_key=PROJ_KEY_XYZ,
            base_url="https://sub2api.medicalheart.cn",
        )
        env = ce._build_qwen_code_subprocess_env()
        assert env["OPENAI_API_KEY"] == PROJ_KEY_XYZ
        assert env["OPENAI_BASE_URL"] == "https://sub2api.medicalheart.cn/v1"
        assert env["QWEN_CODE_MODEL"] == "qwen3.8-flash"

    def test_subprocess_env_falls_back_to_profile(self, monkeypatch) -> None:
        import tool_box.tools_impl.code_executor as ce

        class _Profile:
            api_key = ADMIN_KEY
            api_url = "https://sub2api.medicalheart.cn/v1/chat/completions"
            model = "qwen3.8-flash"

        monkeypatch.setattr(ce, "is_production", lambda: True)
        monkeypatch.setattr(ce, "platform_profile", lambda: _Profile())
        env = ce._build_qwen_code_subprocess_env()
        assert env["OPENAI_API_KEY"] == ADMIN_KEY
