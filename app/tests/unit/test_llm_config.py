import os

import pytest

from app.services.foundation.llm_config import (
    LLMConfigurationError,
    dashscope_test_profile,
    platform_profile,
)


def test_platform_profile_rejects_missing_values(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for key in (
        "PLATFORM_LLM_API_URL",
        "PLATFORM_LLM_API_KEY",
        "PLATFORM_LLM_MODEL",
        "PLATFORM_LLM_RESPONSES_API_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(LLMConfigurationError):
        platform_profile()


def test_platform_profile_rejects_dashscope(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PLATFORM_LLM_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    monkeypatch.setenv("PLATFORM_LLM_API_KEY", "test")
    monkeypatch.setenv("PLATFORM_LLM_MODEL", "qwen/test")
    with pytest.raises(LLMConfigurationError):
        platform_profile()


def test_platform_profile_derives_responses_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PLATFORM_LLM_ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("PLATFORM_LLM_API_URL", "https://example.com/v1/chat/completions")
    monkeypatch.setenv("PLATFORM_LLM_API_KEY", "test")
    monkeypatch.setenv("PLATFORM_LLM_MODEL", "qwen/test")
    monkeypatch.delenv("PLATFORM_LLM_RESPONSES_API_URL", raising=False)
    profile = platform_profile()
    assert profile.provider == "platform"
    assert profile.responses_api_url.endswith("/v1/responses")


def test_dashscope_profile_is_nonproduction_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DASHSCOPE_TEST_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    monkeypatch.setenv("DASHSCOPE_TEST_API_KEY", "test")
    monkeypatch.setenv("DASHSCOPE_TEST_MODEL", "qwen/test")
    assert dashscope_test_profile().provider == "dashscope_test"
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(LLMConfigurationError):
        dashscope_test_profile()
