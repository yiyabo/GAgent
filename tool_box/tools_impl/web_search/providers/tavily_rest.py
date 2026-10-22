import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from app.config import SearchSettings

from ..exceptions import WebSearchError
from ..result import WebSearchResult
from .tavily_keys import KEY_REST_SECONDS, POOL

logger = logging.getLogger(__name__)


def _format_results(raw_results: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for item in raw_results[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or "Result"
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        source = urlparse(url).netloc if url else item.get("source") or "tavily"
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": source,
            }
        )
    return results


class _KeyRejected(Exception):
    """The API refused this key (quota, rate limit or authentication); another key may work."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.detail = detail


def _configured_keys(settings: SearchSettings) -> Tuple[str, ...]:
    keys = tuple(getattr(settings, "tavily_api_keys", None) or ())
    if keys:
        return keys
    single = getattr(settings, "tavily_api_key", None)
    return (single,) if single else ()


async def _search_once(
    *,
    api_key: str,
    query: str,
    max_results: int,
    settings: SearchSettings,
) -> Dict[str, Any]:
    api_url = settings.tavily_api_url
    timeout = aiohttp.ClientTimeout(total=settings.tavily_timeout)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload: Dict[str, Any] = {
        "query": query,
        "max_results": max_results,
        "search_depth": settings.tavily_search_depth or "advanced",
        "topic": settings.tavily_topic or "general",
        "include_answer": settings.tavily_include_answer,
        "include_raw_content": settings.tavily_include_raw_content,
        "include_images": False,
        "include_image_descriptions": False,
        "include_favicon": False,
    }

    if settings.tavily_time_range:
        payload["time_range"] = settings.tavily_time_range

    if settings.tavily_auto_parameters:
        payload["auto_parameters"] = True

    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                raw_text = await response.text()
                if response.status in KEY_REST_SECONDS:
                    raise _KeyRejected(response.status, raw_text)
                if response.status != 200:
                    raise WebSearchError(
                        code="http_error",
                        message=f"HTTP {response.status}: {raw_text}",
                        provider="tavily",
                        meta={"status": response.status},
                    )
                try:
                    return json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise WebSearchError(
                        code="invalid_response",
                        message=f"Invalid JSON response: {exc}",
                        provider="tavily",
                    ) from exc
    except (WebSearchError, _KeyRejected):
        raise
    except Exception as exc:  # pragma: no cover - network/runtime
        logger.error("Tavily REST search failed: %s", exc, exc_info=True)
        message = str(exc).strip() or f"{type(exc).__name__}"
        raise WebSearchError(
            code="request_failed",
            message=message,
            provider="tavily",
        ) from exc


def _exhausted(total: int, status: Optional[int], wait_seconds: float) -> WebSearchError:
    noun = "key is" if total == 1 else f"{total} keys are"
    last = f" (last HTTP {status})" if status else ""
    return WebSearchError(
        code="quota_exhausted",
        message=(
            f"Tavily search is unavailable: the {noun} out of quota, rate limited or rejected{last}. "
            f"Retry in about {max(1, round(wait_seconds / 60))} min, or call web_search with "
            "provider='builtin' (slow: 1-8 minutes per call)."
        ),
        provider="tavily",
        meta={"status": status, "keys": total, "retry_after_seconds": round(wait_seconds)},
    )


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    keys = _configured_keys(settings)
    if not keys:
        raise WebSearchError(
            code="missing_api_key",
            message="Tavily API key is not configured",
            provider="tavily",
        )

    candidates = POOL.order(keys)
    if not candidates:
        raise _exhausted(len(keys), None, POOL.seconds_until_available(keys))

    last_status: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    for position, api_key in candidates:
        try:
            data = await _search_once(
                api_key=api_key, query=query, max_results=max_results, settings=settings
            )
            break
        except _KeyRejected as rejected:
            POOL.rest(api_key, rejected.status)
            last_status = rejected.status
            logger.warning(
                "Tavily key #%d of %d rejected (HTTP %d); trying the next key",
                position + 1,
                len(keys),
                rejected.status,
            )
    if data is None:
        raise _exhausted(len(keys), last_status, POOL.seconds_until_available(keys))

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raw_results = []

    results = _format_results(raw_results, max_results)
    answer = str(data.get("answer") or "").strip()

    return WebSearchResult(
        query=query,
        provider="tavily",
        response=answer,
        results=results,
        success=True,
        error=None,
        raw=data,
    )
