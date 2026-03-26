import asyncio
import logging
import re
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.config import get_search_settings

from .exceptions import WebSearchError
from .result import WebSearchResult
from .router import dispatch

logger = logging.getLogger(__name__)

_COMPARISON_CUE_RE = re.compile(
    r"(?:\bcompare\b|\bcomparison\b|\bversus\b|\bvs\b|\bdifference\b|\bdifferences\b|\bcontrast\b|比较|对比|区别|差异)",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_ENTITY_STOPWORDS = {
    "comparison",
    "compare",
    "versus",
    "difference",
    "differences",
    "contrast",
    "sampling",
    "molecular",
    "dynamics",
    "learning",
    "paper",
    "papers",
    "method",
    "methods",
    "recent",
    "latest",
}


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


def _normalize_queries(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    normalized: List[str] = []
    for item in values:
        text = " ".join(str(item or "").split()).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _extract_focus_entities(query: str) -> List[str]:
    entities: List[str] = []
    seen: set[str] = set()
    for token in _ENTITY_RE.findall(query or ""):
        lowered = token.lower()
        if lowered in _ENTITY_STOPWORDS:
            continue
        # Favor named methods / models: CamelCase, digits, or all-caps fragments.
        if not (any(ch.isdigit() for ch in token) or any(ch.isupper() for ch in token[1:])):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        entities.append(token)
    return entities


def _auto_parallel_queries(query: str) -> List[str]:
    base = " ".join(str(query or "").split()).strip()
    if not base or not _COMPARISON_CUE_RE.search(base):
        return [base] if base else []

    entities = _extract_focus_entities(base)
    if len(entities) < 2:
        return [base]

    lowered = base.lower()
    context_parts: List[str] = []
    years = re.findall(r"\b20\d{2}\b", base)
    if years:
        context_parts.extend(years[:2])
    for phrase in ("all-atom", "molecular dynamics", "sampling", "equilibrium", "dataset", "open source", "paper"):
        if phrase in lowered and phrase not in context_parts:
            context_parts.append(phrase)

    derived: List[str] = []
    for entity in entities[:4]:
        suffix = " ".join(context_parts).strip()
        subquery = f"{entity} {suffix}".strip() if suffix else entity
        derived.append(subquery)
    return _normalize_queries(derived) or [base]


def _merge_parallel_results(
    *,
    original_query: str,
    provider_name: str,
    successful: List[Tuple[str, WebSearchResult]],
    failures: List[Tuple[str, WebSearchError]],
) -> WebSearchResult:
    merged_results: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    response_blocks: List[str] = []

    for query_text, result in successful:
        cleaned_response = str(result.response or "").strip()
        if cleaned_response:
            response_blocks.append(f"[{query_text}]\n{cleaned_response}")
        for item in result.results:
            url = str((item or {}).get("url") or "").strip()
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            merged_results.append(dict(item))

    meta: Dict[str, Any] = {
        "parallel_queries": [q for q, _ in successful] + [q for q, _ in failures],
        "successful_queries": [q for q, _ in successful],
        "failed_queries": [
            {
                "query": q,
                "code": err.code,
                "message": err.message,
                "provider": err.provider,
                "meta": err.meta,
            }
            for q, err in failures
        ],
        "parallel_search": True,
        "search_verified": len(successful) > 0,
    }
    if successful:
        meta["provider_metas"] = [res.meta for _, res in successful if isinstance(res.meta, dict)]

    return WebSearchResult(
        query=original_query,
        provider=provider_name,
        response="\n\n".join(response_blocks).strip(),
        results=merged_results,
        success=True,
        error=None,
        raw=None,
        meta=meta,
    )


async def web_search_handler(
    query: str,
    max_results: int = 5,
    provider: Optional[str] = None,
    include_raw: bool = False,
    queries: Optional[Sequence[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Web search entry point exposed to toolbox integration.
    """

    settings = get_search_settings()
    requested_provider = (provider or "").strip().lower() or None
    provider_name = requested_provider or settings.default_provider or "builtin"
    explicit_queries = _normalize_queries(list(queries or []))
    search_queries = (
        explicit_queries
        if explicit_queries
        else _auto_parallel_queries(query)
    )

    if len(search_queries) > 1:
        parallel_results = await asyncio.gather(
            *[
                dispatch(
                    query=subquery,
                    provider=requested_provider,
                    max_results=max_results,
                    settings=settings,
                    **kwargs,
                )
                for subquery in search_queries
            ],
            return_exceptions=True,
        )
        successful: List[Tuple[str, WebSearchResult]] = []
        failures: List[Tuple[str, WebSearchError]] = []
        for subquery, item in zip(search_queries, parallel_results):
            if isinstance(item, WebSearchResult):
                successful.append((subquery, item))
                continue
            if isinstance(item, WebSearchError):
                failures.append((subquery, item))
                continue
            failures.append(
                (
                    subquery,
                    WebSearchError(
                        code="unexpected_error",
                        message=str(item),
                        provider=provider_name,
                    ),
                )
            )
        if successful:
            return _format_success(
                _merge_parallel_results(
                    original_query=query,
                    provider_name=provider_name,
                    successful=successful,
                    failures=failures,
                ),
                include_raw=include_raw,
            )
        if failures:
            last_error = failures[-1][1]
            return _failure_payload(
                query,
                last_error.provider,
                last_error.code,
                last_error.message,
                meta={
                    "parallel_queries": search_queries,
                    "failed_queries": [
                        {
                            "query": q,
                            "code": err.code,
                            "message": err.message,
                            "provider": err.provider,
                            "meta": err.meta,
                        }
                        for q, err in failures
                    ],
                    "parallel_search": True,
                },
            )

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
