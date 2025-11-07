import logging
from dataclasses import asdict
from typing import Any, Dict, Optional

from app.config import get_search_settings

from .exceptions import WebSearchError
from .result import WebSearchResult
from .router import dispatch

logger = logging.getLogger(__name__)


def _format_success(result: WebSearchResult, include_raw: bool = False) -> Dict[str, Any]:
    payload = asdict(result)
    payload["total_results"] = len(result.results)
    if not include_raw and "raw" in payload:
        payload.pop("raw")
    payload.setdefault("success", True)
    payload.setdefault("error", None)
    return payload


def _failure_payload(query: str, provider: str, code: str, message: str, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "query": query,
        "provider": provider,
        "success": False,
        "error": message,
        "code": code,
    }
    if meta:
        payload["meta"] = meta
    return payload


async def web_search_handler(
    query: str,
    max_results: int = 5,
    provider: Optional[str] = None,
    include_raw: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Web search entry point exposed to toolbox integration.
    """

    settings = get_search_settings()
    requested_provider = (provider or "").strip().lower() or None

    try:
        result = await dispatch(
            query=query,
            provider=requested_provider,
            max_results=max_results,
            settings=settings,
            **kwargs,
        )
        return _format_success(result, include_raw=include_raw)
    except WebSearchError as exc:
        logger.warning(
            "Web search provider error: %s",
            exc.message,
            extra={"provider": exc.provider, "code": exc.code},
        )

        # 自动兜底：builtin 失败时尝试切换到 perplexity
        if exc.provider == "builtin" and (
            requested_provider is None or requested_provider == "builtin"
        ):
            try:
                fallback_result = await dispatch(
                    query=query,
                    provider="perplexity",
                    max_results=max_results,
                    settings=settings,
                    **kwargs,
                )
                payload = _format_success(fallback_result, include_raw=include_raw)
                payload["fallback_from"] = "builtin"
                return payload
            except WebSearchError as fallback_exc:
                logger.error(
                    "Fallback web search failed: %s",
                    fallback_exc.message,
                    extra={"provider": fallback_exc.provider, "code": fallback_exc.code},
                )
                return _failure_payload(
                    query,
                    fallback_exc.provider,
                    fallback_exc.code,
                    fallback_exc.message,
                    meta=fallback_exc.meta,
                )

        return _failure_payload(query, exc.provider, exc.code, exc.message, meta=exc.meta)
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception("Unexpected web search failure")
        provider_name = requested_provider or settings.default_provider
        return _failure_payload(
            query,
            provider_name,
            "unexpected_error",
            str(exc),
        )
