import os

import pytest

from app import llm as llm_module
from app.config import decomposer_config, executor_config
from app.services.llm.decomposer_service import PlanDecomposerLLMService
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutorLLMService


class StubLLMClient:
    """Minimal async/sync client for LLMService injection in tests."""

    def __init__(self) -> None:
        self.calls = []
        self.model = "stub-model"

    async def chat_async(self, prompt: str, **kwargs):
        self.calls.append({"mode": "async", "prompt": prompt, "kwargs": kwargs})
        # Return a minimal structured response recognised by StructuredChatAgent
        return '{"llm_reply": {"message": "stub"}, "actions": []}'

    def chat(self, prompt: str, **kwargs):
        self.calls.append({"mode": "sync", "prompt": prompt, "kwargs": kwargs})
        return '{"status": "success", "content": "ok", "notes": [], "metadata": {}}'


class StubLLMService:
    """Simple wrapper exposing chat() for PlanExecutor/Decomposer tests."""

    def __init__(self):
        self.calls = []

    def chat(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return self.response


def test_default_llm_client_uses_env_model(monkeypatch):
    """Verify chat LLM pulls provider/model configuration from environment."""
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_API_KEY", "unit-test-key")
    monkeypatch.setenv("QWEN_API_URL", "https://example.com/llm")
    monkeypatch.setenv("QWEN_MODEL", "unit-test-qwen")

    # Reset cached client so new env variables take effect
    monkeypatch.setattr(llm_module, "_default_client", None)

    client = llm_module.get_default_client()
    assert client.provider.lower() == "qwen"
    assert client.model == "unit-test-qwen"
    assert client.api_key == "unit-test-key"


def test_plan_decomposer_uses_decomp_model_env(monkeypatch):
    """Ensure PlanDecomposer forwards DECOMP_MODEL to the LLM service."""
    monkeypatch.setenv("DECOMP_MODEL", "glm-decomposer-test")
    decomposer_config.get_decomposer_settings.cache_clear()

    try:
        stub_llm = StubLLMService()
        stub_llm.response = '{"target_node_id": null, "mode": "single_node", "should_stop": true, "children": []}'

        service = PlanDecomposerLLMService(
            llm=stub_llm,
            settings=decomposer_config.get_decomposer_settings(),
        )

        service.generate("prompt for decomposition")
        assert stub_llm.calls, "LLM was not invoked"
        assert stub_llm.calls[0]["kwargs"]["model"] == "glm-decomposer-test"
    finally:
        decomposer_config.get_decomposer_settings.cache_clear()


def test_plan_decomposer_custom_provider(monkeypatch):
    captured: dict = {}

    class DummyClient:
        def __init__(self, provider=None, api_key=None, url=None, model=None, **_):
            captured.update(
                {"provider": provider, "api_key": api_key, "url": url, "model": model}
            )

        def chat(self, prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return '{"target_node_id": null, "mode": "single_node", "should_stop": true, "children": []}'

    monkeypatch.setenv("DECOMP_PROVIDER", "perplexity")
    monkeypatch.setenv("DECOMP_API_KEY", "perplexity-key")
    monkeypatch.setenv("DECOMP_API_URL", "https://api.perplexity.example")
    monkeypatch.setenv("DECOMP_MODEL", "decomp-model")
    monkeypatch.setattr("app.services.llm.decomposer_service.LLMClient", DummyClient)
    decomposer_config.get_decomposer_settings.cache_clear()

    try:
        service = PlanDecomposerLLMService(settings=decomposer_config.get_decomposer_settings())
        service.generate("prompt for decomposition")
        assert captured["provider"] == "perplexity"
        assert captured["api_key"] == "perplexity-key"
        assert captured["url"] == "https://api.perplexity.example"
        assert captured["model"] == "decomp-model"
    finally:
        decomposer_config.get_decomposer_settings.cache_clear()


def test_plan_executor_uses_executor_model_env(monkeypatch):
    """Ensure PlanExecutor forwards PLAN_EXECUTOR_MODEL to the LLM service."""
    monkeypatch.setenv("PLAN_EXECUTOR_MODEL", "glm-plan-executor-test")
    executor_config.get_executor_settings.cache_clear()

    try:
        stub_llm = StubLLMService()
        stub_llm.response = '{"status": "success", "content": "done", "notes": [], "metadata": {}}'

        settings = executor_config.get_executor_settings()
        service = PlanExecutorLLMService(
            llm=stub_llm,
            settings=settings,
        )
        config = ExecutionConfig.from_settings(settings)

        service.generate("execute prompt", config)
        assert stub_llm.calls, "LLM was not invoked"
        assert stub_llm.calls[0]["kwargs"]["model"] == "glm-plan-executor-test"
    finally:
        executor_config.get_executor_settings.cache_clear()


def test_plan_executor_custom_provider(monkeypatch):
    captured: dict = {}

    class DummyClient:
        def __init__(self, provider=None, api_key=None, url=None, model=None, **_):
            captured.update(
                {"provider": provider, "api_key": api_key, "url": url, "model": model}
            )

        def chat(self, prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return '{"status": "success", "content": "ok", "notes": [], "metadata": {}}'

    monkeypatch.setenv("PLAN_EXECUTOR_PROVIDER", "qwen")
    monkeypatch.setenv("PLAN_EXECUTOR_API_KEY", "qwen-key")
    monkeypatch.setenv("PLAN_EXECUTOR_API_URL", "https://qwen.example/api")
    monkeypatch.setenv("PLAN_EXECUTOR_MODEL", "qwen-executor")
    monkeypatch.setattr("app.services.plans.plan_executor.LLMClient", DummyClient)
    executor_config.get_executor_settings.cache_clear()

    try:
        settings = executor_config.get_executor_settings()
        service = PlanExecutorLLMService(settings=settings)
        config = ExecutionConfig.from_settings(settings)
        service.generate("execute prompt", config)
        assert captured["provider"] == "qwen"
        assert captured["api_key"] == "qwen-key"
        assert captured["url"] == "https://qwen.example/api"
        assert captured["model"] == "qwen-executor"
    finally:
        executor_config.get_executor_settings.cache_clear()
