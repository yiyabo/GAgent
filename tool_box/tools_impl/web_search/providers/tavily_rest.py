import json
import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

import aiohttp

from app.config import SearchSettings

from ..exceptions import WebSearchError
from ..result import WebSearchResult

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


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    api_key = settings.tavily_api_key
    api_url = settings.tavily_api_url
    timeout = aiohttp.ClientTimeout(total=settings.tavily_timeout)

    if not api_key:
        raise WebSearchError(
            code="missing_api_key",
            message="Tavily API key is not configured",
            provider="tavily",
        )

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
                if response.status != 200:
                    raise WebSearchError(
                        code="http_error",
                        message=f"HTTP {response.status}: {raw_text}",
                        provider="tavily",
                        meta={"status": response.status},
                    )
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise WebSearchError(
                        code="invalid_response",
                        message=f"Invalid JSON response: {exc}",
                        provider="tavily",
                    ) from exc
    except WebSearchError:
        raise
    except Exception as exc:  # pragma: no cover - network/runtime
        logger.error("Tavily REST search failed: %s", exc, exc_info=True)
        message = str(exc).strip() or f"{type(exc).__name__}"
        raise WebSearchError(
            code="request_failed",
            message=message,
            provider="tavily",
        ) from exc

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
