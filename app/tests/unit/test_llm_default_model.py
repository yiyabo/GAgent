"""chat_default_model() follows LLM_PROVIDER so plan review and fallbacks use the active chat model."""

import pytest

from app.services.foundation.settings import chat_default_model, get_settings


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_openai_provider_uses_openai_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "CS/DeepSeek-V4-Flash-FP8")
    assert chat_default_model() == "CS/DeepSeek-V4-Flash-FP8"


def test_openai_provider_without_model_fails_loudly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_MODEL"):
        chat_default_model()


def test_qwen_provider_keeps_previous_behaviour(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_MODEL", "qwen3.8-flash")
    assert chat_default_model() == "qwen3.8-flash"


def test_unset_provider_defaults_to_qwen(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("QWEN_MODEL", raising=False)
    assert chat_default_model() == "qwen3.8-flash"
