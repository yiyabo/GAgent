"""The default chat model is qwen3.8-flash; qwen3.7-max stays selectable."""

from __future__ import annotations

import asyncio

from app.routers import models_routes
from app.routers.chat.models import ChatSessionSettings
from app.routers.chat.session_helpers import VALID_BASE_MODELS, _normalize_base_model
from app.services.plans.plan_optimizer import DEFAULT_OPTIMIZER_MODEL

DEFAULT = "qwen3.8-flash"


def test_both_qwen_models_are_selectable_and_the_flash_one_is_listed() -> None:
    assert {DEFAULT, "qwen3.7-max"} <= VALID_BASE_MODELS
    assert _normalize_base_model(DEFAULT) == DEFAULT
    assert _normalize_base_model("qwen3.7-max") == "qwen3.7-max"
    assert ChatSessionSettings(default_base_model=DEFAULT).default_base_model == DEFAULT
    assert models_routes._MODEL_METADATA[DEFAULT]["provider"] == "qwen"


def test_the_unconfigured_default_is_the_flash_model(monkeypatch) -> None:
    monkeypatch.delenv("QWEN_MODEL", raising=False)
    from app.llm import LLMClient
    from app.services.foundation.settings import AppSettings

    assert AppSettings.model_fields["qwen_model"].default == DEFAULT
    assert DEFAULT_OPTIMIZER_MODEL == DEFAULT
    client = LLMClient(provider="qwen", api_key="k", url="http://localhost/v1/chat/completions")
    assert client.model == DEFAULT


def test_the_models_endpoint_reports_the_configured_model(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_MODEL", DEFAULT)
    listing = asyncio.run(models_routes.list_available_models())
    assert any(m.id == DEFAULT for m in listing.models)
