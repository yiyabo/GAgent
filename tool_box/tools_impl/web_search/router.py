from typing import Any, Optional

import time

from app.config import SearchSettings, get_search_settings

from ..invocation_meter import record_tool_invocation
from .exceptions import WebSearchError
from .providers import get_provider, init_default_providers
from .result import WebSearchResult

_INITIALISED = False


def _ensure_initialised() -> None:
    global _INITIALISED
    if not _INITIALISED:
        init_default_providers()
        _INITIALISED = True


async def dispatch(
    *,
    query: str,
    provider: Optional[str],
    max_results: int,
    settings: Optional[SearchSettings] = None,
    **kwargs: Any,
) -> WebSearchResult:
    _ensure_initialised()

    settings = settings or get_search_settings()
    provider_name = (provider or settings.default_provider or "builtin").lower()

    func = get_provider(provider_name)
    if not func:
        raise WebSearchError(
            code="unsupported_provider",
            message=f"Unknown provider: {provider_name}",
            provider=provider_name,
        )

    started = time.monotonic()
    call_status = "ok"
    try:
        return await func(
            query=query,
            max_results=max_results,
            settings=settings,
            **kwargs,
        )
    except Exception:
        call_status = "error"
        raise
    finally:
        # 按次计费落行（key tool.web_search）：一次 provider 搜索 = 一次付费调用
        record_tool_invocation(
            tool_name="web_search",
            call_status=call_status,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )
