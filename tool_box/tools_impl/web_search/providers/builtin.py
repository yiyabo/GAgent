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

SYSTEM_PROMPT = (
    "You are an AI assistant with built-in web search capabilities. "
    "Given a user query you must look up the most recent information on the internet, "
    "summarise the findings, and provide concise references. "
    "Respond with a JSON object containing the keys 'answer' (string) and "
    "'references' (array of objects with 'title', 'url', 'snippet'). "
    "Keep the snippets short (<=200 characters) and ensure URLs are valid."
)


def _extract_links(text: str) -> List[Dict[str, str]]:
    pattern = re.compile(r"(https?://[^\s\]\)]+)")
    links: List[Dict[str, str]] = []
    for match in pattern.findall(text):
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


def _parse_content(content: Any, max_results: int) -> Tuple[str, List[Dict[str, Any]]]:
    if isinstance(content, list):
        # OpenAI兼容接口在某些模型下返回列表形式
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)

    content = content.strip()
    if not content:
        return "", []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        answer = content
        links = _extract_links(content)[:max_results]
        return answer, links

    answer = str(data.get("answer") or data.get("summary") or "").strip()
    references = []
    if isinstance(data.get("references"), list):
        for ref in data["references"]:
            if not isinstance(ref, dict):
                continue
            title = str(ref.get("title") or ref.get("name") or "").strip() or "Reference"
            url = str(ref.get("url") or ref.get("link") or "").strip()
            snippet = str(ref.get("snippet") or ref.get("summary") or ref.get("content") or "").strip()
            source = urlparse(url).netloc if url else ref.get("source") or "web"
            references.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet[:200],
                    "source": source,
                }
            )
    if not answer:
        answer = content
    if not references:
        references = _extract_links(content)
    return answer, references[:max_results]


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    provider_name = (settings.builtin_provider or "qwen").lower()

    if provider_name not in {"qwen", "glm"}:
        raise WebSearchError(
            code="unsupported_builtin",
            message=f"Unsupported builtin provider: {provider_name}",
            provider="builtin",
            meta={"requested": provider_name},
        )

    if provider_name == "qwen":
        api_key = settings.qwen_api_key
        api_url = settings.qwen_api_url
        model = settings.qwen_model
    else:  # glm
        api_key = settings.glm_api_key
        api_url = settings.glm_api_url
        model = settings.glm_model

    if not api_key:
        raise WebSearchError(
            code="missing_api_key",
            message=f"{provider_name.upper()} API key is not configured",
            provider="builtin",
            meta={"provider": provider_name},
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"为以下检索任务提供答案：{query}\n请按照要求输出 JSON。",
        },
    ]

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }

    if provider_name == "qwen":
        payload["enable_search"] = True

    timeout = aiohttp.ClientTimeout(total=settings.builtin_request_timeout)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                raw_text = await response.text()
                if response.status != 200:
                    raise WebSearchError(
                        code="http_error",
                        message=f"HTTP {response.status}: {raw_text}",
                        provider="builtin",
                        meta={"status": response.status},
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
        logger.error("Builtin search request failed: %s", exc)
        raise WebSearchError(
            code="request_failed",
            message=str(exc),
            provider="builtin",
        ) from exc

    try:
        choice = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise WebSearchError(
            code="invalid_response",
            message="Unexpected response structure from builtin provider",
            provider="builtin",
            meta={"raw": data},
        ) from exc

    answer, references = _parse_content(choice.get("content"), max_results)

    return WebSearchResult(
        query=query,
        provider="builtin",
        response=answer,
        results=references,
        success=True,
        error=None,
        raw=data,
        meta={"source_provider": provider_name},
    )
