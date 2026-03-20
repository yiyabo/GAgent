"""
DashScope OpenAI-compatible Responses API with built-in ``web_search`` tool only.

See: https://help.aliyun.com/zh/model-studio/web-search
"""

import json
import logging
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import aiohttp

from app.config import SearchSettings

from ..exceptions import WebSearchError
from ..result import WebSearchResult

logger = logging.getLogger(__name__)

_EXTRACT_URL_RE = re.compile(r"(https?://[^\s\]\)]+)")


def _extract_links(text: str) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    for match in _EXTRACT_URL_RE.findall(text):
        url = match.strip().rstrip(".,)")
        source = urlparse(url).netloc or "web"
        links.append(
            {
                "title": source,
                "url": url,
                "snippet": "",
                "source": source,
            }
        )
    return links


def extract_answer_from_responses_payload(data: Dict[str, Any], max_results: int) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Parse DashScope / OpenAI-compatible Responses JSON: output[].content[] (output_text + annotations).
    Exported for unit tests.
    """
    texts: List[str] = []
    refs: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        if item.get("role") not in (None, "assistant"):
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "output_text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    texts.append(t.strip())
                for ann in block.get("annotations") or []:
                    if not isinstance(ann, dict):
                        continue
                    url = str(
                        ann.get("url")
                        or ann.get("source_url")
                        or ann.get("link")
                        or ""
                    ).strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = str(ann.get("title") or ann.get("name") or "").strip() or urlparse(url).netloc or "web"
                    snippet = str(
                        ann.get("snippet") or ann.get("summary") or ann.get("content") or ""
                    ).strip()
                    refs.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": urlparse(url).netloc or "web",
                        }
                    )
            elif isinstance(block.get("text"), str) and block.get("text", "").strip():
                # Some payloads omit type but include text
                texts.append(str(block["text"]).strip())

    answer = "\n\n".join(texts) if texts else ""
    if len(refs) < max_results and answer:
        for link in _extract_links(answer):
            u = link["url"]
            if u in seen_urls:
                continue
            seen_urls.add(u)
            refs.append(
                {
                    "title": link["title"],
                    "url": u,
                    "snippet": link.get("snippet", ""),
                    "source": link["source"],
                }
            )
            if len(refs) >= max_results:
                break

    return answer, refs[:max_results]


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    provider_name = (settings.builtin_provider or "qwen").lower()
    if provider_name != "qwen":
        raise WebSearchError(
            code="unsupported_builtin",
            message="Web search uses DashScope Responses API (web_search tool) and requires BUILTIN_SEARCH_PROVIDER=qwen.",
            provider="builtin",
            meta={"requested": provider_name},
        )

    api_key = settings.qwen_api_key
    api_url = settings.qwen_responses_api_url
    model = (settings.qwen_responses_model or settings.qwen_model or "qwen3.5-plus").strip()

    if not api_key:
        raise WebSearchError(
            code="missing_api_key",
            message="QWEN_API_KEY is not configured",
            provider="builtin",
            meta={"provider": "qwen"},
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "input": query.strip(),
        "tools": [{"type": "web_search"}],
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(total=settings.builtin_request_timeout)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                raw_text = await response.text()
                if response.status != 200:
                    raise WebSearchError(
                        code="http_error",
                        message=f"HTTP {response.status}: {raw_text[:2000]}",
                        provider="builtin",
                        meta={"status": response.status, "url": api_url},
                    )
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise WebSearchError(
                        code="invalid_response",
                        message=f"Invalid JSON response: {exc}",
                        provider="builtin",
                    ) from exc

    except WebSearchError:
        raise
    except Exception as exc:  # pragma: no cover - network/runtime
        logger.error("DashScope Responses web_search request failed: %s", exc)
        raise WebSearchError(
            code="request_failed",
            message=str(exc),
            provider="builtin",
        ) from exc

    if not isinstance(data, dict):
        raise WebSearchError(
            code="invalid_response",
            message="Top-level response is not a JSON object",
            provider="builtin",
        )

    answer, references = extract_answer_from_responses_payload(data, max_results)
    if not answer.strip() and not references:
        raise WebSearchError(
            code="empty_answer",
            message="Responses API returned no assistant text; check model supports web_search.",
            provider="builtin",
            meta={"raw_keys": list(data.keys())},
        )

    return WebSearchResult(
        query=query,
        provider="builtin",
        response=answer.strip(),
        results=references,
        success=True,
        error=None,
        raw=data,
        meta={
            "source_provider": "qwen",
            "api": "responses",
            "tool": "web_search",
            "endpoint": api_url,
            "model": model,
        },
    )
