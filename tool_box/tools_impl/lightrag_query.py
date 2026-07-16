"""LightRAG 8-shard gateway client tool (primary knowledge graph).

Calls the external LightRAG query gateway for retrieval-only context.
Final answer generation stays in Phage-Agent's own LLM path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_lightrag_gateway_settings

logger = logging.getLogger(__name__)


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def _slim_items(
    items: Any,
    *,
    limit: int,
    text_keys: tuple[str, ...],
    max_chars: int,
) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    slimmed: List[Dict[str, Any]] = []
    for raw in items[: max(0, limit)]:
        if not isinstance(raw, dict):
            continue
        row: Dict[str, Any] = {}
        for key, value in raw.items():
            if key in text_keys and isinstance(value, str):
                row[key] = _truncate_text(value, max_chars)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                row[key] = value
            elif isinstance(value, list) and key in {"content", "file_path", "source_id"}:
                # Keep short list payloads only.
                if all(isinstance(v, str) for v in value[:5]):
                    row[key] = [_truncate_text(v, max_chars) for v in value[:5]]
        slimmed.append(row)
    return slimmed


def _build_context_preview(context_items: Any, *, max_items: int, max_chars: int) -> str:
    if not isinstance(context_items, list) or not context_items:
        return ""
    blocks: List[str] = []
    used = 0
    for index, item in enumerate(context_items[:max_items], start=1):
        if not isinstance(item, dict):
            continue
        header = (
            f"[{index}] shard={item.get('shard') or '?'} "
            f"file={item.get('file_path') or 'unknown'}"
        )
        content = _truncate_text(item.get("content"), 1200)
        block = f"{header}\n{content}\n"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                blocks.append(block[:remaining])
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks).strip()


async def lightrag_query_handler(
    *,
    query: str,
    mode: str = "mix",
    top_k: int = 5,
    max_chunks: int = 12,
    max_references: int = 12,
    include_references: bool = True,
) -> Dict[str, Any]:
    query_text = (query or "").strip()
    if len(query_text) < 3:
        return {
            "success": False,
            "error": "lightrag_query requires a query of at least 3 characters.",
            "code": "missing_query",
        }

    settings = get_lightrag_gateway_settings()
    if not settings.enabled:
        return {
            "success": False,
            "error": "LightRAG gateway is disabled (LIGHTRAG_GATEWAY_ENABLED=false).",
            "code": "disabled",
        }
    if not settings.base_url:
        return {
            "success": False,
            "error": "LIGHTRAG_GATEWAY_URL is not configured.",
            "code": "not_configured",
        }

    def _coerce_int(value: Any, default: int, *, lo: int, hi: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(lo, min(parsed, hi))

    safe_mode = str(mode or settings.default_mode or "mix").strip() or "mix"
    safe_top_k = _coerce_int(top_k, settings.default_top_k, lo=1, hi=20)
    safe_max_chunks = _coerce_int(max_chunks, settings.default_max_chunks, lo=1, hi=40)
    safe_max_refs = _coerce_int(
        max_references, settings.default_max_references, lo=1, hi=40
    )

    url = f"{settings.base_url.rstrip('/')}/query/data"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key

    payload = {
        "query": query_text,
        "mode": safe_mode,
        "top_k": safe_top_k,
        "max_chunks": safe_max_chunks,
        "max_references": safe_max_refs,
        "include_references": bool(include_references),
        "include_chunk_content": True,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        logger.warning("LightRAG gateway timed out: %s", url)
        return {
            "success": False,
            "error": f"LightRAG gateway timed out after {settings.timeout_seconds}s.",
            "code": "timeout",
            "gateway_url": settings.base_url,
        }
    except httpx.RequestError as exc:
        logger.warning("LightRAG gateway request failed: %s", exc)
        return {
            "success": False,
            "error": f"LightRAG gateway unreachable: {exc}",
            "code": "unreachable",
            "gateway_url": settings.base_url,
        }

    if response.status_code == 403:
        return {
            "success": False,
            "error": "LightRAG gateway rejected the API key.",
            "code": "auth_failed",
            "status_code": 403,
        }
    if response.status_code >= 400:
        return {
            "success": False,
            "error": f"LightRAG gateway returned HTTP {response.status_code}.",
            "code": "http_error",
            "status_code": response.status_code,
            "detail": _truncate_text(response.text, 800),
            "gateway_url": settings.base_url,
        }

    try:
        body = response.json()
    except ValueError:
        return {
            "success": False,
            "error": "LightRAG gateway returned non-JSON response.",
            "code": "invalid_json",
        }

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        data = {}
    metadata = body.get("metadata") if isinstance(body, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}

    entities = _slim_items(
        data.get("entities"),
        limit=20,
        text_keys=("description", "entity_name", "name", "content"),
        max_chars=400,
    )
    relationships = _slim_items(
        data.get("relationships"),
        limit=20,
        text_keys=("description", "keywords", "src_id", "tgt_id", "content"),
        max_chars=400,
    )
    chunks = _slim_items(
        data.get("chunks"),
        limit=safe_max_chunks,
        text_keys=("content", "chunk_content", "text", "description", "file_path"),
        max_chars=800,
    )
    references = _slim_items(
        data.get("references"),
        limit=safe_max_refs,
        text_keys=("file_path", "reference_id", "title"),
        max_chars=300,
    )
    context_preview = _build_context_preview(
        data.get("context_items") or chunks,
        max_items=min(safe_max_chunks, 12),
        max_chars=12000,
    )

    success = str(body.get("status") or "").lower() == "success" or bool(
        entities or relationships or chunks or context_preview
    )
    if not success:
        return {
            "success": False,
            "error": body.get("message") or "LightRAG gateway returned no usable evidence.",
            "code": "empty_result",
            "metadata": metadata,
            "gateway_url": settings.base_url,
        }

    return {
        "success": True,
        "query": query_text,
        "mode": safe_mode,
        "summary": {
            "entities": len(entities),
            "relationships": len(relationships),
            "chunks": len(chunks),
            "references": len(references),
            "shards_ok": metadata.get("shards_ok"),
            "shards_total": metadata.get("shards_total"),
        },
        "context_preview": context_preview,
        "entities": entities,
        "relationships": relationships,
        "chunks": chunks,
        "references": references if include_references else [],
        "metadata": metadata,
        "gateway_url": settings.base_url,
        "note": (
            "Retrieval-only evidence from LightRAG. Synthesize the answer with the "
            "Agent LLM; do not claim facts beyond this evidence."
        ),
    }


lightrag_query_tool = {
    "name": "lightrag_query",
    "description": (
        "PREFERRED knowledge-graph / literature RAG tool. Query the large LightRAG "
        "8-shard corpus for entities, relations, chunks, and references. Use this by "
        "default for knowledge-graph, literature-backed, or corpus factual questions. "
        "Returns retrieval evidence only (no final generation)."
    ),
    "category": "knowledge_graph",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language question for the LightRAG corpus.",
            },
            "mode": {
                "type": "string",
                "enum": ["mix", "hybrid", "local", "global", "naive"],
                "default": "mix",
                "description": "LightRAG retrieval mode (default: mix).",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "Per-shard top_k for entity/relation retrieval.",
            },
            "max_chunks": {
                "type": "integer",
                "minimum": 1,
                "maximum": 40,
                "default": 12,
                "description": "Max merged chunks/context items to keep.",
            },
            "max_references": {
                "type": "integer",
                "minimum": 1,
                "maximum": 40,
                "default": 12,
                "description": "Max references to keep.",
            },
            "include_references": {
                "type": "boolean",
                "default": True,
                "description": "Whether to include reference metadata in the tool result.",
            },
        },
        "required": ["query"],
    },
    "handler": lightrag_query_handler,
    "tags": ["knowledge", "graph", "rag", "lightrag", "literature"],
    "examples": [
        "What does the corpus say about phage-host antibiotic resistance?",
        "Summarize CRISPR anti-phage defense mechanisms from the knowledge base.",
    ],
}

__all__ = ["lightrag_query_tool", "lightrag_query_handler"]
