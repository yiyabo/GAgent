import json
import logging
from typing import Any, Dict, List

import aiohttp

from app.config import SearchSettings

from ..exceptions import WebSearchError
from ..result import WebSearchResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an AI search assistant. Provide concise answers based on the latest web "
    "information and list key sources."
)


def _format_results(answer: str) -> List[Dict[str, Any]]:
    if not answer:
        return []
    return [
        {
            "title": "Perplexity Answer",
            "url": "",
            "snippet": answer[:500],
            "source": "Perplexity",
        }
    ]


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    api_key = settings.perplexity_api_key
    api_url = settings.perplexity_api_url
    model = settings.perplexity_model
    timeout = aiohttp.ClientTimeout(total=settings.perplexity_timeout)

    if not api_key:
        raise WebSearchError(
            code="missing_api_key",
            message="Perplexity API key is not configured",
            provider="perplexity",
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "max_tokens": 1000,
        "temperature": 0.1,
        "stream": False,
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                text = await response.text()
                if response.status != 200:
                    raise WebSearchError(
                        code="http_error",
                        message=f"HTTP {response.status}: {text}",
                        provider="perplexity",
                        meta={"status": response.status},
                    )
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise WebSearchError(
                        code="invalid_response",
                        message=f"Invalid JSON payload: {exc}",
                        provider="perplexity",
                    ) from exc
    except WebSearchError:
        raise
    except Exception as exc:  # pragma: no cover - network/runtime
        logger.error("Perplexity search failed: %s", exc)
        raise WebSearchError(
            code="request_failed",
            message=str(exc),
            provider="perplexity",
        ) from exc

    try:
        message = data["choices"][0]["message"]
        answer = message.get("content", "")
    except (KeyError, IndexError, TypeError) as exc:
        raise WebSearchError(
            code="invalid_response",
            message="Unexpected response structure from Perplexity",
            provider="perplexity",
            meta={"raw": data},
        ) from exc

    results = _format_results(answer)[:max_results]

    return WebSearchResult(
        query=query,
        provider="perplexity",
        response=answer,
        results=results,
        success=True,
        error=None,
        raw=data,
    )
