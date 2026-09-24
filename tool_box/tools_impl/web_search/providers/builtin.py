"""
DashScope OpenAI-compatible Responses API with built-in ``web_search`` tool only.

See: https://help.aliyun.com/zh/model-studio/web-search
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from app.config import SearchSettings
from app.services.foundation.llm_config import is_production

from ..exceptions import WebSearchError
from ..result import WebSearchResult

logger = logging.getLogger(__name__)

_EXTRACT_URL_RE = re.compile(r"(https?://[^\s\]\)]+)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Transient gateway statuses worth another attempt: the production search
# gateway fronts an agentic search backend whose latency routinely exceeds
# intermediate nginx tiers (observed HTML 504 pages from nginx/1.31.2).
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _summarize_error_body(raw: str, limit: int = 300) -> str:
    """Collapse an error body (often an nginx HTML page) into one readable line."""
    text = _HTML_TAG_RE.sub(" ", raw or "")
    text = " ".join(text.split())
    return text[:limit] or "(empty body)"


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _retry_delay(attempt: int, backoff_base: float, retry_after: Optional[float]) -> float:
    delay = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, backoff_base / 4.0)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


def _retry_tuning(settings: SearchSettings) -> Tuple[int, float, float]:
    # getattr defaults keep duck-typed settings fakes in older tests working.
    try:
        retries = int(getattr(settings, "builtin_retries", 2))
    except (TypeError, ValueError):
        retries = 2
    try:
        backoff_base = float(getattr(settings, "builtin_backoff_base", 2.0))
    except (TypeError, ValueError):
        backoff_base = 2.0
    try:
        connect_timeout = float(getattr(settings, "builtin_connect_timeout", 20.0))
    except (TypeError, ValueError):
        connect_timeout = 20.0
    return max(0, retries), max(0.0, backoff_base), max(0.5, connect_timeout)


def _normalize_http_url(raw: str) -> str:
    u = raw.strip().rstrip(".,;)]}\"'")
    return u


def _extract_links_from_text(text: str) -> List[Dict[str, str]]:
    """Plain URLs and markdown [label](url) from assistant text."""
    links: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in _EXTRACT_URL_RE.findall(text):
        url = _normalize_http_url(match)
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        source = urlparse(url).netloc or "web"
        links.append({"title": source, "url": url, "snippet": "", "source": source})
    for m in _MD_LINK_RE.finditer(text):
        label = (m.group(1) or "").strip()
        url = _normalize_http_url(m.group(2))
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        source = urlparse(url).netloc or "web"
        title = label or source
        links.append({"title": title, "url": url, "snippet": "", "source": source})
    return links


def _append_ref(
    refs: List[Dict[str, Any]],
    seen_urls: set[str],
    url: str,
    *,
    title: str,
    snippet: str,
    max_results: int,
) -> bool:
    if len(refs) >= max_results:
        return False
    u = _normalize_http_url(url)
    if not u.startswith("http") or u in seen_urls:
        return False
    seen_urls.add(u)
    t = (title or "").strip() or urlparse(u).netloc or "web"
    sn = (snippet or "").strip()
    if len(sn) > 1200:
        sn = sn[:1200] + "..."
    refs.append(
        {
            "title": t,
            "url": u,
            "snippet": sn,
            "source": urlparse(u).netloc or "web",
        }
    )
    return True


def _collect_urls_from_object(obj: Any, refs: List[Dict[str, Any]], seen_urls: set[str], max_results: int) -> None:
    """Depth-first scan for dicts carrying url / source_url / link / href (API-specific nesting)."""
    if len(refs) >= max_results:
        return
    if isinstance(obj, dict):
        url = obj.get("url") or obj.get("source_url") or obj.get("link") or obj.get("href")
        if isinstance(url, str):
            title = str(
                obj.get("title")
                or obj.get("name")
                or obj.get("headline")
                or obj.get("site_name")
                or ""
            )
            snippet = str(
                obj.get("snippet")
                or obj.get("summary")
                or obj.get("content")
                or obj.get("description")
                or obj.get("text")
                or ""
            )
            _append_ref(refs, seen_urls, url, title=title, snippet=snippet, max_results=max_results)
        for v in obj.values():
            _collect_urls_from_object(v, refs, seen_urls, max_results)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls_from_object(v, refs, seen_urls, max_results)


def extract_answer_from_responses_payload(
    data: Dict[str, Any], max_results: int
) -> Tuple[str, List[Dict[str, Any]], Dict[str, int]]:
    """
    Parse DashScope / OpenAI-compatible Responses JSON: output[].content[] (output_text + annotations),
    plus recursive url-like nodes anywhere in the payload (tool blocks, citations, etc.).
    Exported for unit tests. Third tuple item is extraction counts for observability.
    """
    texts: List[str] = []
    refs: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    stats = {"from_annotations": 0, "from_tree": 0, "from_text": 0}

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
                    if not url:
                        continue
                    title = str(ann.get("title") or ann.get("name") or "").strip() or urlparse(url).netloc or "web"
                    snippet = str(
                        ann.get("snippet") or ann.get("summary") or ann.get("content") or ""
                    ).strip()
                    if _append_ref(refs, seen_urls, url, title=title, snippet=snippet, max_results=max_results):
                        stats["from_annotations"] += 1
            elif isinstance(block.get("text"), str) and block.get("text", "").strip():
                # Some payloads omit type but include text
                texts.append(str(block["text"]).strip())

    answer = "\n\n".join(texts) if texts else ""
    n_before_tree = len(refs)
    _collect_urls_from_object(data, refs, seen_urls, max_results)
    stats["from_tree"] = len(refs) - n_before_tree

    if len(refs) < max_results and answer:
        n_before_text = len(refs)
        for link in _extract_links_from_text(answer):
            _append_ref(
                refs,
                seen_urls,
                link["url"],
                title=link["title"],
                snippet=link.get("snippet", "") or "",
                max_results=max_results,
            )
            if len(refs) >= max_results:
                break
        stats["from_text"] = len(refs) - n_before_text

    return answer, refs[:max_results], stats


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    provider_name = (settings.builtin_provider or "qwen").lower()
    allowed = {"platform"} if is_production() else {"qwen", "platform"}
    if provider_name not in allowed:
        raise WebSearchError(
            code="unsupported_builtin",
            message="Web search must use the configured platform Responses gateway.",
            provider="builtin",
            meta={"requested": provider_name},
        )

    api_key = settings.qwen_api_key
    api_url = settings.qwen_responses_api_url
    model = (settings.qwen_responses_model or settings.qwen_model or "qwen3.7-max").strip()

    if is_production():
        try:
            from app.llm import get_project_llm_credentials
            creds = get_project_llm_credentials()
        except Exception:
            creds = None
        if creds:
            api_key = str(creds["api_key"])
            api_url = str(creds["responses_url"])

    if not api_key:
        raise WebSearchError(
            code="missing_api_key",
            message="QWEN_API_KEY is not configured",
            provider="builtin",
            meta={"provider": "qwen"},
        )

    local_url = (os.getenv("QWEN_RESPONSES_LOCAL_URL") or "").strip()
    if local_url:
        api_url = local_url

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

    retries, backoff_base, connect_timeout = _retry_tuning(settings)
    attempts = retries + 1
    total_timeout = max(1.0, float(settings.builtin_request_timeout or 300.0))
    timeout = aiohttp.ClientTimeout(total=total_timeout, connect=min(connect_timeout, total_timeout))
    host = urlparse(api_url).netloc or api_url

    data: Optional[Dict[str, Any]] = None
    for attempt in range(1, attempts + 1):
        started_at = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    raw_text = await response.text()
                    status = response.status
                    retry_after = _parse_retry_after((getattr(response, "headers", None) or {}).get("Retry-After"))

            if status != 200:
                elapsed = round(time.monotonic() - started_at, 2)
                summary = _summarize_error_body(raw_text)
                hint = ""
                if status in _RETRYABLE_STATUSES:
                    hint = (
                        " The upstream search service is overloaded or slow to respond; "
                        "retrying later may help."
                    )
                message = (
                    f"Search gateway HTTP {status} ({host}) after {elapsed}s "
                    f"(attempt {attempt}/{attempts}): {summary}{hint}"
                )
                if status in _RETRYABLE_STATUSES and attempt < attempts:
                    delay = _retry_delay(attempt, backoff_base, retry_after)
                    logger.warning(
                        "web_search builtin attempt %d/%d got HTTP %d from %s; retrying in %.1fs",
                        attempt,
                        attempts,
                        status,
                        host,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("DashScope Responses web_search request failed: %s", message)
                raise WebSearchError(
                    code="http_error",
                    message=message,
                    provider="builtin",
                    meta={
                        "status": status,
                        "url": api_url,
                        "attempts": attempt,
                        "elapsed_seconds": elapsed,
                        "retry_after": retry_after,
                        "body": raw_text[:2000],
                    },
                )
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise WebSearchError(
                    code="invalid_response",
                    message=f"Invalid JSON response: {exc}",
                    provider="builtin",
                ) from exc
            break

        except WebSearchError:
            raise
        except Exception as exc:  # network/timeout — retryable while attempts remain
            elapsed = round(time.monotonic() - started_at, 2)
            exc_type = type(exc).__name__
            detail = f"{exc_type} after {elapsed}s"
            text = str(exc).strip()
            if text:
                detail = f"{detail}: {text}"
            if attempt < attempts:
                delay = _retry_delay(attempt, backoff_base, None)
                logger.warning(
                    "web_search builtin attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    attempts,
                    detail,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                "DashScope Responses web_search request failed after %d attempts: %s",
                attempt,
                detail,
            )
            raise WebSearchError(
                code="request_failed",
                message=f"{detail} (attempt {attempt}/{attempts}, host {host})",
                provider="builtin",
                meta={
                    "url": api_url,
                    "model": model,
                    "elapsed_seconds": elapsed,
                    "exception_type": exc_type,
                    "attempts": attempt,
                },
            ) from exc

    if data is None:  # pragma: no cover - loop always assigns or raises
        raise WebSearchError(
            code="request_failed",
            message=f"No response from search gateway ({host})",
            provider="builtin",
            meta={"url": api_url, "model": model},
        )

    if not isinstance(data, dict):
        raise WebSearchError(
            code="invalid_response",
            message="Top-level response is not a JSON object",
            provider="builtin",
        )

    answer, references, citation_stats = extract_answer_from_responses_payload(data, max_results)
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
            "citation_extraction": citation_stats,
            "verifiable_sources": len(references) > 0,
        },
    )
