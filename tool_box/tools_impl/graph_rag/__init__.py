"""
Graph RAG Tool Wrapper

Provides unified tool definitions and handlers for toolbox registration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from app.config import get_graph_rag_settings
from tool_box.cache import get_memory_cache

from .exceptions import GraphRAGError
from .service import get_graph_rag_service, query_graph_rag

logger = logging.getLogger(__name__)


def _normalize_focus_entities(raw: Optional[Iterable[Any]]) -> List[str]:
    if not raw:
        return []
    items: List[str] = []
    for value in raw:
        if isinstance(value, str) and value.strip():
            items.append(value.strip())
    return items


async def graph_rag_handler(
    *,
    query: str,
    top_k: int = 12,
    hops: int = 1,
    return_subgraph: bool = True,
    focus_entities: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    query_text = (query or "").strip()
    if not query_text:
        return {
            "query": query,
            "success": False,
            "error": "Graph RAG requires a non-empty query.",
            "code": "missing_query",
        }

    settings = get_graph_rag_settings()
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    default_top_k = min(12, settings.max_top_k)
    default_hops = min(1, settings.max_hops)

    safe_top_k = max(1, min(_coerce_int(top_k, default_top_k), settings.max_top_k))
    safe_hops = max(0, min(_coerce_int(hops, default_hops), settings.max_hops))
    safe_return_subgraph = bool(return_subgraph)
    sanitized_focus = _normalize_focus_entities(focus_entities)

    params = {
        "query": query_text,
        "top_k": safe_top_k,
        "hops": safe_hops,
        "return_subgraph": safe_return_subgraph,
        "focus_entities": sanitized_focus,
    }

    cache = await get_memory_cache()
    cached = await cache.get("graph_rag", params)
    if cached:
        return dict(cached, cache_hit=True)

    try:
        rag = await get_graph_rag_service(settings)
        result = await query_graph_rag(
            rag=rag,
            query=query_text,
            top_k=safe_top_k,
            hops=safe_hops,
            return_subgraph=safe_return_subgraph,
            focus_entities=sanitized_focus,
        )
    except GraphRAGError as exc:
        logger.warning("Graph RAG execution failed: %s", exc.message)
        payload = {
            "query": query_text,
            "success": False,
            "error": exc.message,
            "code": exc.code,
        }
        await cache.set("graph_rag", params, payload, ttl=60)
        return payload

    payload = {
        "query": query_text,
        "success": True,
        "result": result,
    }

    await cache.set("graph_rag", params, payload, ttl=settings.cache_ttl)
    return payload


graph_rag_tool = {
    "name": "graph_rag",
    "description": "Query phage knowledge graph, return relevant triples, prompts, and optional subgraph.",
    "category": "knowledge_graph",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Query statement, e.g., 'How do phages infect bacteria?'",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 12,
                "description": "Number of most relevant triples to return (limited by system cap).",
            },
            "hops": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "default": 1,
                "description": "Number of hops to expand subgraph.",
            },
            "return_subgraph": {
                "type": "boolean",
                "default": True,
                "description": "Whether to return k-hop subgraph JSON.",
            },
            "focus_entities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of entity names to prioritize, can be used to reorder results.",
            },
        },
        "required": ["query"],
    },
    "handler": graph_rag_handler,
    "tags": ["knowledge", "graph", "rag", "phage"],
    "examples": [
        "How do phages infect drug-resistant bacteria?",
        "List interactions between phage genomes and hosts",
    ],
}

__all__ = ["graph_rag_tool", "graph_rag_handler"]
