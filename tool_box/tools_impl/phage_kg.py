"""PhageRAG knowledge-graph client tool (``phage_kg``).

Calls the read-only graph tools of the PhageRAG MCP server (Streamable HTTP,
stateless JSON-RPC) for entity search, local subgraphs, entity evidence, full
source documents and hierarchy browsing over the 8-shard LightRAG corpus graph.
Retrieval only: the answer is written by Phage-Agent's own LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

ENV_ENABLED = "PHAGE_KG_ENABLED"
ENV_URL = "PHAGE_KG_MCP_URL"
ENV_TOKEN = "PHAGE_KG_MCP_TOKEN"
ENV_TIMEOUT = "PHAGE_KG_TIMEOUT"
DEFAULT_URL = "http://host.docker.internal:19682/mcp"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 2

# operation -> remote MCP tool. rag_query (server-side LLM answer) is not exposed.
OPERATIONS: Dict[str, str] = {
    "search_entities": "domain_kg_phagerag_search_entities",
    "subgraph": "domain_kg_phagerag_query_subgraph",
    "evidence": "domain_kg_phagerag_get_entity_evidence",
    "document": "domain_kg_phagerag_get_document",
    "hierarchy": "domain_kg_phagerag_browse_hierarchy",
    "topology": "domain_kg_phagerag_graph_topology",
}

NOTE = (
    "Retrieval-only evidence from the PhageRAG literature knowledge graph. Synthesize "
    "the answer with the Agent LLM, cite document_id/file_path for claims, and do not "
    "claim facts beyond this evidence."
)


def phage_kg_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip() == "1"


def _endpoint() -> str:
    return (os.environ.get(ENV_URL, "").strip() or DEFAULT_URL).rstrip("/")


def _timeout_seconds() -> float:
    try:
        value = float(os.environ.get(ENV_TIMEOUT, "").strip() or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(value, 300.0))


def _truncate(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lo, min(parsed, hi))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


class _KGError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, request_id: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id


def _decode_body(response: httpx.Response) -> Dict[str, Any]:
    """JSON body, or the last JSON ``data:`` event of an SSE reply."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        payload: Optional[Dict[str, Any]] = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except ValueError:
                    continue
        if payload is None:
            raise _KGError("invalid_response", "PhageRAG MCP returned an empty event stream.")
        return payload
    try:
        body = response.json()
    except ValueError as exc:
        raise _KGError("invalid_response", "PhageRAG MCP returned non-JSON response.") from exc
    if not isinstance(body, dict):
        raise _KGError("invalid_response", "PhageRAG MCP returned an unexpected payload.")
    return body


async def _call_once(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    token = os.environ.get(ENV_TOKEN, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    url = _endpoint()
    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds(), trust_env=False) as client:
            response = await client.post(url, headers=headers, json=request)
    except httpx.TimeoutException as exc:
        raise _KGError("timeout", f"PhageRAG MCP timed out after {_timeout_seconds():g}s.", retryable=True) from exc
    except httpx.RequestError as exc:
        raise _KGError("unreachable", f"PhageRAG MCP unreachable: {exc}", retryable=True) from exc

    if response.status_code in (401, 403):
        raise _KGError("auth_failed", "PhageRAG MCP rejected the request (check PHAGE_KG_MCP_TOKEN).")
    if response.status_code >= 500:
        raise _KGError("http_error", f"PhageRAG MCP returned HTTP {response.status_code}.", retryable=True)
    if response.status_code >= 400:
        raise _KGError("http_error", f"PhageRAG MCP returned HTTP {response.status_code}.")

    body = _decode_body(response)
    if isinstance(body.get("error"), dict):
        err = body["error"]
        raise _KGError("rpc_error", _truncate(err.get("message") or "JSON-RPC error", 400))
    result = body.get("result")
    if not isinstance(result, dict):
        raise _KGError("invalid_response", "PhageRAG MCP returned no result.")
    structured = result.get("structuredContent")
    if result.get("isError"):
        info = structured if isinstance(structured, dict) else {}
        texts = [b.get("text") for b in result.get("content") or [] if isinstance(b, dict)]
        retryable = str(info.get("retryable", "")).strip().lower() == "true"
        raise _KGError(
            str(info.get("code") or "tool_error"),
            _truncate(info.get("message") or " ".join(t for t in texts if t) or "PhageRAG tool error", 400),
            retryable=retryable,
            request_id=info.get("request_id"),
        )
    if not isinstance(structured, dict):
        raise _KGError("invalid_response", "PhageRAG MCP returned no structured content.")
    return structured


async def _call(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    attempt = 0
    while True:
        try:
            return await _call_once(tool, arguments)
        except _KGError as exc:
            if not exc.retryable or attempt >= MAX_RETRIES:
                raise
            attempt += 1
            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))


def _entity(raw: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    row = {
        key: raw.get(key)
        for key in ("id", "name", "entity_type", "degree", "evidence_count", "source_shards", "score", "rank")
        if raw.get(key) is not None
    }
    description = _text(raw.get("description"))
    if description:
        row["description"] = _truncate(description, max_chars)
    return row


def _edge(raw: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "source": raw.get("start_id"),
        "target": raw.get("end_id"),
        "description": _truncate(_text(raw.get("description")), 300),
    }
    for key in ("edge_key", "evidence_count", "occurrence_count"):
        if raw.get(key) is not None:
            row[key] = raw.get(key)
    return row


def _items(value: Any) -> List[Dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _shape(operation: str, data: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"graph_version": data.get("graph_version")}
    if operation == "search_entities":
        items = [_entity(item, 400) for item in _items(data.get("items"))]
        out.update(total=data.get("total"), returned=len(items), entities=items)
    elif operation == "subgraph":
        nodes = _items(data.get("nodes"))
        names = {node.get("id"): node.get("name") for node in nodes}
        edges = [_edge(edge) for edge in _items(data.get("edges"))]
        for edge in edges:
            edge["source_name"] = names.get(edge["source"])
            edge["target_name"] = names.get(edge["target"])
        out.update(
            nodes=[_entity(node, 300) for node in nodes],
            edges=edges,
            truncated=bool(data.get("truncated")),
        )
    elif operation == "evidence":
        chunks = []
        for item in _items(data.get("items")):
            row = {
                key: item.get(key)
                for key in ("chunk_id", "document_id", "shard_id", "file_path")
                if item.get(key) is not None
            }
            row["content"] = _truncate(item.get("content"), 1500)
            chunks.append(row)
        out.update(total=data.get("total"), returned=len(chunks), evidence=chunks)
    elif operation == "document":
        content = str(data.get("content_markdown") or data.get("content") or data.get("markdown") or "")
        out.update(
            {
                key: data.get(key)
                for key in ("document_id", "file_path", "shard_id", "title", "chunk_count")
                if data.get(key) is not None
            }
        )
        out["content"] = content[:max_chars]
        out["content_truncated"] = bool(data.get("truncated")) or len(content) > max_chars
    elif operation == "hierarchy":
        items = []
        for item in _items(data.get("items")):
            items.append(
                {
                    key: item.get(key)
                    for key in ("id", "parent_id", "level", "kind", "name", "member_count", "entity_type")
                    if item.get(key) is not None
                }
            )
        out.update(
            total=data.get("total"),
            next_cursor=data.get("next_cursor"),
            breadcrumb=data.get("breadcrumb") or [],
            items=items,
        )
    else:
        out.update({key: value for key, value in data.items() if not isinstance(value, (dict, list))})
        for key, value in data.items():
            if isinstance(value, dict):
                out[key] = value
    return out


def _arguments(operation: str, params: Dict[str, Any]) -> Dict[str, Any] | str:
    if operation in {"search_entities", "subgraph"}:
        query = _text(params.get("query"))[:300]
        if not query:
            return f"phage_kg {operation} requires a non-empty `query`."
        if operation == "search_entities":
            return {"query": query, "limit": _int(params.get("limit"), 10, 1, 50)}
        return {
            "query": query,
            "node_limit": _int(params.get("node_limit"), 20, 1, 50),
            "edge_limit": _int(params.get("edge_limit"), 40, 1, 100),
            "max_hops": _int(params.get("max_hops"), 1, 1, 2),
        }
    if operation == "evidence":
        identifier = _text(params.get("identifier") or params.get("entity_id") or params.get("query"))[:512]
        if not identifier:
            return "phage_kg evidence requires `identifier` (entity id or name)."
        return {
            "identifier": identifier,
            "limit": _int(params.get("limit"), 5, 1, 50),
            "offset": _int(params.get("offset"), 0, 0, 100000),
        }
    if operation == "document":
        args: Dict[str, Any] = {}
        for key in ("document_id", "file_path", "shard_id"):
            value = _text(params.get(key))
            if value:
                args[key] = value
        if "document_id" not in args and "file_path" not in args:
            return "phage_kg document requires `document_id` or `file_path` (from evidence results)."
        args["max_chars"] = _int(params.get("max_chars"), 12000, 500, 40000)
        return args
    if operation == "hierarchy":
        args = {"limit": _int(params.get("limit"), 25, 1, 100)}
        for key in ("parent_id", "cursor"):
            value = _text(params.get(key))
            if value:
                args[key] = value
        return args
    return {}


async def phage_kg_handler(
    *,
    operation: str = "search_entities",
    query: Optional[str] = None,
    identifier: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    node_limit: Optional[int] = None,
    edge_limit: Optional[int] = None,
    max_hops: Optional[int] = None,
    document_id: Optional[str] = None,
    file_path: Optional[str] = None,
    shard_id: Optional[str] = None,
    max_chars: Optional[int] = None,
    parent_id: Optional[str] = None,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    op = str(operation or "").strip().lower()
    base: Dict[str, Any] = {"tool": "phage_kg", "operation": op}
    if not phage_kg_enabled():
        return {**base, "success": False, "code": "disabled", "error": f"phage_kg is disabled ({ENV_ENABLED}!=1)."}
    if op not in OPERATIONS:
        return {
            **base,
            "success": False,
            "code": "invalid_operation",
            "error": f"Unknown operation {operation!r}; use one of {sorted(OPERATIONS)}.",
        }
    params = {
        "query": query,
        "identifier": identifier,
        "limit": limit,
        "offset": offset,
        "node_limit": node_limit,
        "edge_limit": edge_limit,
        "max_hops": max_hops,
        "document_id": document_id,
        "file_path": file_path,
        "shard_id": shard_id,
        "max_chars": max_chars,
        "parent_id": parent_id,
        "cursor": cursor,
    }
    arguments = _arguments(op, params)
    if isinstance(arguments, str):
        return {**base, "success": False, "code": "invalid_arguments", "error": arguments}
    try:
        data = await _call(OPERATIONS[op], arguments)
    except _KGError as exc:
        logger.warning("phage_kg %s failed: %s (%s)", op, exc.code, exc.message)
        failure = {**base, "success": False, "code": exc.code, "error": exc.message}
        if exc.request_id:
            failure["request_id"] = exc.request_id
        return failure
    shaped = _shape(op, data, int(arguments.get("max_chars") or 12000))
    result = {**base, "success": True, "arguments": arguments, **shaped, "note": NOTE}
    if op in {"search_entities", "evidence"} and not result.get("returned"):
        result["empty_result"] = True
    if op == "subgraph" and not result.get("nodes"):
        result["empty_result"] = True
    return result


DESCRIPTION = (
    "PhageRAG literature knowledge graph (1.3M entities, 3M relations built from the "
    "8-shard LightRAG phage corpus; read-only). Use it to ground phage/host/protein/"
    "mechanism questions and plans in graph structure and source papers. Operations: "
    "`search_entities` (find entities by name/description), `subgraph` (1-2 hop "
    "neighbourhood with relation descriptions), `evidence` (paper text chunks for an "
    "entity id or name), `document` (full Markdown of a source paper by document_id/"
    "file_path from evidence), `hierarchy` (browse components/communities), "
    "`topology` (graph version and size). Typical chain: search_entities -> subgraph "
    "-> evidence -> document. Retrieval only; queries in English work best. Use "
    "`lightrag_query` for one-shot semantic retrieval of passages."
)

PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(OPERATIONS),
            "description": "What to retrieve (see tool description).",
        },
        "query": {
            "type": "string",
            "description": "Entity name or short phrase for search_entities / subgraph (English, <=300 chars).",
        },
        "identifier": {
            "type": "string",
            "description": "evidence: entity id (e:...) from search/subgraph results, or an exact entity name.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": (
                "Max items: search_entities 1-50 (default 10), evidence 1-50 (default 5, page with "
                "offset), hierarchy 1-100 (default 25)."
            ),
        },
        "offset": {"type": "integer", "minimum": 0, "description": "evidence: page offset."},
        "node_limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "subgraph: max nodes (default 20)."},
        "edge_limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "subgraph: max edges (default 40)."},
        "max_hops": {"type": "integer", "minimum": 1, "maximum": 2, "description": "subgraph: 1 or 2 hops (default 1)."},
        "document_id": {"type": "string", "description": "document: document_id from evidence results."},
        "file_path": {"type": "string", "description": "document: file_path from evidence results."},
        "shard_id": {"type": "string", "description": "document: shard_id from evidence results (optional)."},
        "max_chars": {
            "type": "integer",
            "minimum": 500,
            "maximum": 40000,
            "description": "document: max characters of Markdown to return (default 12000).",
        },
        "parent_id": {"type": "string", "description": "hierarchy: node id to expand (omit for top level)."},
        "cursor": {"type": "string", "description": "hierarchy: next_cursor from the previous page."},
    },
    "required": ["operation"],
}

phage_kg_tool: Dict[str, Any] = {
    "name": "phage_kg",
    "description": DESCRIPTION,
    "category": "knowledge_graph",
    "parameters_schema": PARAMETERS_SCHEMA,
    "handler": phage_kg_handler,
    "tags": ["knowledge", "graph", "phage", "literature", "entity", "evidence"],
    "examples": [
        "Find knowledge-graph entities for Pseudomonas phage depolymerase",
        "Show the 1-hop subgraph around endolysin and the papers supporting its relations",
    ],
}

__all__ = [
    "DESCRIPTION",
    "OPERATIONS",
    "PARAMETERS_SCHEMA",
    "phage_kg_enabled",
    "phage_kg_handler",
    "phage_kg_tool",
]
