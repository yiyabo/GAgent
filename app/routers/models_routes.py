"""
Available LLM models and providers discovery endpoint.

Exposes the dynamic list of selectable (provider, model) pairs that the
frontend can offer in a model-switcher UI. The list is derived from the
whitelists in `app.routers.chat.session_helpers` so it stays in sync with
the validation layer.
"""

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.foundation.settings import get_settings
from .chat.session_helpers import VALID_BASE_MODELS, VALID_LLM_PROVIDERS
from . import register_router

router = APIRouter(prefix="/api/models", tags=["models"])

register_router(
    namespace="models",
    version="v1",
    path="/api/models",
    router=router,
    tags=["models"],
    description="Available LLM models and providers",
)


class ModelEntry(BaseModel):
    id: str
    name: str
    provider: str
    description: Optional[str] = None
    available: bool = True


class ProviderEntry(BaseModel):
    id: str
    name: str
    available: bool


class AvailableModelsResponse(BaseModel):
    providers: List[ProviderEntry]
    models: List[ModelEntry]
    current_provider: str
    current_model: str


_PROVIDER_DISPLAY = {
    "qwen": "Qwen (DashScope)",
    "mimo": "Xiaomi MiMo",
    "kimi": "Kimi",
    "openai": "OpenAI",
    "perplexity": "Perplexity",
}

_MODEL_METADATA = {
    "qwen3.7-max": {
        "provider": "qwen",
        "name": "Qwen 3.7 Max",
        "description": "Default flagship Qwen model (1M context).",
    },
    "mimo-v2.5-pro-ultraspeed": {
        "provider": "mimo",
        "name": "MiMo v2.5 Pro UltraSpeed",
        "description": "Xiaomi MiMo flagship with reasoning, ~500 tok/s decode.",
    },
    "kimi/kimi-k2.7-code-highspeed": {
        "provider": "kimi",
        "name": "Kimi K2.7 Code Highspeed",
        "description": "Kimi coding model hosted on Bailian via Qwen-compatible endpoint.",
    },
}


def _provider_available(provider_id: str) -> bool:
    settings = get_settings()
    key_attr = f"{provider_id}_api_key"
    api_key = getattr(settings, key_attr, None)
    if api_key:
        return True
    # kimi provider can fallback to qwen credentials on the same Bailian platform
    if provider_id == "kimi":
        return bool(getattr(settings, "qwen_api_key", None))
    return False


@router.get("", response_model=AvailableModelsResponse)
async def list_available_models() -> AvailableModelsResponse:
    settings = get_settings()

    providers: List[ProviderEntry] = []
    for provider_id in sorted(VALID_LLM_PROVIDERS):
        providers.append(
            ProviderEntry(
                id=provider_id,
                name=_PROVIDER_DISPLAY.get(provider_id, provider_id),
                available=_provider_available(provider_id),
            )
        )

    models: List[ModelEntry] = []
    for model_id in sorted(VALID_BASE_MODELS):
        meta = _MODEL_METADATA.get(model_id, {})
        provider_id = meta.get("provider", "qwen")
        models.append(
            ModelEntry(
                id=model_id,
                name=meta.get("name", model_id),
                provider=provider_id,
                description=meta.get("description"),
                available=_provider_available(provider_id),
            )
        )

    return AvailableModelsResponse(
        providers=providers,
        models=models,
        current_provider=getattr(settings, "llm_provider", "qwen") or "qwen",
        current_model=getattr(settings, "qwen_model", "qwen3.7-max") or "qwen3.7-max",
    )
