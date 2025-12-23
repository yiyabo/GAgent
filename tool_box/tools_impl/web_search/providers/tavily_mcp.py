import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

import aiohttp

from app.config import SearchSettings

from ..exceptions import WebSearchError
from ..result import WebSearchResult

logger = logging.getLogger(__name__)


def _build_mcp_url(settings: SearchSettings) -> str:
    base_url = settings.tavily_mcp_url or "https://mcp.tavily.com/mcp/"
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if settings.tavily_api_key:
        query["tavilyApiKey"] = settings.tavily_api_key
    return urlunparse(parsed._replace(query=urlencode(query)))


def _has_api_key(settings: SearchSettings) -> bool:
    if settings.tavily_api_key:
        return True
    parsed = urlparse(settings.tavily_mcp_url or "")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return bool(query.get("tavilyApiKey"))


def _select_tool_name(payload: Dict[str, Any]) -> Optional[str]:
    tools = payload.get("result", {}).get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if "tavily" in name:
            return name
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if "search" in name:
            return name
    return None


def _normalize_results(raw_results: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in raw_results[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "Result").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        snippet = str(item.get("snippet") or item.get("content") or item.get("summary") or "").strip()
        source = urlparse(url).netloc if url else item.get("source") or "tavily"
        normalized.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": source,
            }
        )
    return normalized


def _extract_text(content: Any) -> str:
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        return str(text).strip() if text else ""
    if isinstance(content, str):
        return content.strip()
    return ""


def _parse_payload(payload: Any, max_results: int) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"answer": _extract_text(payload), "results": []}

    if isinstance(payload.get("results"), list):
        return {
            "answer": str(payload.get("answer") or payload.get("summary") or "").strip(),
            "results": payload.get("results") or [],
        }

    content_text = _extract_text(payload.get("content"))
    if content_text:
        try:
            parsed = json.loads(content_text)
            if isinstance(parsed, dict):
                return {
                    "answer": str(parsed.get("answer") or parsed.get("summary") or "").strip(),
                    "results": parsed.get("results") or [],
                }
        except json.JSONDecodeError:
            pass
        return {"answer": content_text, "results": []}

    return {"answer": "", "results": []}


def _parse_sse_payload(raw_text: str) -> Dict[str, Any]:
    data_lines: List[str] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                data_lines.append(data)
    for data in reversed(data_lines):
        try:
            payload = json.loads(data)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    return {}


async def _mcp_request(
    session: aiohttp.ClientSession,
    url: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": method,
        "params": params or {},
    }
    async with session.post(url, json=payload, headers=headers) as response:
        text = await response.text()
        if response.status != 200:
            raise WebSearchError(
                code="http_error",
                message=f"HTTP {response.status}: {text}",
                provider="tavily",
                meta={"status": response.status},
            )
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            data = _parse_sse_payload(text)
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise WebSearchError(
                    code="invalid_response",
                    message=f"Invalid JSON response: {exc}",
                    provider="tavily",
                    meta={"content_type": content_type},
                ) from exc
    if "error" in data:
        raise WebSearchError(
            code="mcp_error",
            message=str(data.get("error")),
            provider="tavily",
            meta={"error": data.get("error")},
        )
    return data


async def search(
    *,
    query: str,
    max_results: int,
    settings: SearchSettings,
    **_: Any,
) -> WebSearchResult:
    if not _has_api_key(settings):
        raise WebSearchError(
            code="missing_api_key",
            message="Tavily API key is not configured",
            provider="tavily",
        )

    mcp_url = _build_mcp_url(settings)
    timeout = aiohttp.ClientTimeout(total=settings.tavily_timeout)

    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            list_payload = await _mcp_request(session, mcp_url, "tools/list")
            tool_name = settings.tavily_tool_name or _select_tool_name(list_payload)
            if not tool_name:
                raise WebSearchError(
                    code="tool_not_found",
                    message="Tavily MCP tool not found",
                    provider="tavily",
                )

            call_payload = await _mcp_request(
                session,
                mcp_url,
                "tools/call",
                params={
                    "name": tool_name,
                    "arguments": {"query": query, "max_results": max_results},
                },
            )
    except WebSearchError:
        raise
    except Exception as exc:  # pragma: no cover - network/runtime
        logger.error("Tavily MCP search failed: %s", exc)
        raise WebSearchError(
            code="request_failed",
            message=str(exc),
            provider="tavily",
        ) from exc

    result_payload = call_payload.get("result") if isinstance(call_payload, dict) else {}
    parsed = _parse_payload(result_payload, max_results)
    answer = parsed.get("answer") or ""
    raw_results = parsed.get("results") or []
    results = _normalize_results(raw_results, max_results)

    return WebSearchResult(
        query=query,
        provider="tavily",
        response=answer,
        results=results,
        success=True,
        error=None,
        raw=call_payload,
        meta={"tool_name": tool_name},
    )
