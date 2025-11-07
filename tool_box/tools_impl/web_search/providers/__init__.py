from typing import Awaitable, Callable, Dict, Optional

from ..result import WebSearchResult

ProviderFunc = Callable[..., Awaitable[WebSearchResult]]

_PROVIDER_REGISTRY: Dict[str, ProviderFunc] = {}
_INITIALIZED = False


def register_provider(name: str, func: ProviderFunc) -> None:
    _PROVIDER_REGISTRY[name] = func


def get_provider(name: str) -> Optional[ProviderFunc]:
    return _PROVIDER_REGISTRY.get(name)


def list_providers() -> Dict[str, ProviderFunc]:
    return dict(_PROVIDER_REGISTRY)


def init_default_providers() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    from .builtin import search as builtin_search
    from .perplexity import search as perplexity_search

    register_provider("builtin", builtin_search)
    register_provider("perplexity", perplexity_search)
    _INITIALIZED = True
