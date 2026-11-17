"""memory_operation tool: the agent's governed view of the user's long-term memory.

Every operation is scoped to the calling user (``tool_context.owner_id``);
there is no way to address another user's memories. ``search``/``list`` are
read-only; ``save``/``update``/``forget`` mutate and are blocked in Plan mode.
"""

from __future__ import annotations

import logging
from typing import Any

from tool_box.context import ToolContext

logger = logging.getLogger(__name__)

MEMORY_OPERATIONS = ("search", "save", "list", "update", "forget")
READ_ONLY_MEMORY_OPERATIONS = frozenset({"search", "list"})
_MAX_LIMIT = 50
_MAX_CONTENT_CHARS = 2000


def _error(operation: str, message: str) -> dict[str, Any]:
    return {"success": False, "operation": operation, "error": message}


def _clamp_limit(limit: Any, default: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, _MAX_LIMIT))


def _str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return None
    return [str(v).strip() for v in value if str(v).strip()]


async def _search(service: Any, owner_id: str, query: str | None, memory_type: str | None, limit: int) -> dict[str, Any]:
    from app.models_memory import MemoryType, QueryMemoryRequest

    text = (query or "").strip()
    if not text:
        return _error("search", "query is required for search")
    request = QueryMemoryRequest(
        search_text=text,
        memory_types=[MemoryType(memory_type)] if memory_type else None,
        limit=limit,
        min_similarity=0.35,
        owner_id=owner_id,
    )
    response = await service.query_memory(request)
    items = [
        {
            "id": m.memory_id,
            "content": m.content,
            "memory_type": m.memory_type.value,
            "importance": m.importance.value,
            "similarity": round(float(m.similarity or 0.0), 4),
            "keywords": list(m.keywords or []),
            "tags": list(m.tags or []),
            "created_at": str(m.created_at),
        }
        for m in response.memories
    ]
    return {"success": True, "operation": "search", "items": items, "total": len(items)}


async def _save(service: Any, owner_id: str, content: str | None, memory_type: str | None,
                importance: str | None, keywords: list[str] | None, tags: list[str] | None,
                session_id: str | None) -> dict[str, Any]:
    from app.models_memory import ImportanceLevel, MemoryType, SaveMemoryRequest

    text = (content or "").strip()
    if not text:
        return _error("save", "content is required for save")
    merged_tags: list[str] = list(dict.fromkeys(["agent_saved", *(tags or [])]))
    if session_id:
        merged_tags.append(f"session:{session_id}")
    request = SaveMemoryRequest(
        content=text[:_MAX_CONTENT_CHARS],
        memory_type=MemoryType(memory_type or "knowledge"),
        importance=ImportanceLevel(importance or "medium"),
        keywords=keywords or [],
        tags=merged_tags,
        owner_id=owner_id,
    )
    response = await service.save_memory(request)
    return {
        "success": True,
        "operation": "save",
        "memory_id": response.memory_id,
        "content": response.content,
    }


async def memory_operation_handler(
    operation: str,
    query: str | None = None,
    content: str | None = None,
    memory_id: str | None = None,
    memory_type: str | None = None,
    importance: str | None = None,
    keywords: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search, save, list, update or forget the calling user's memories."""
    op = str(operation or "").strip().lower()
    if op not in MEMORY_OPERATIONS:
        return _error(op or "unknown", f"operation must be one of {list(MEMORY_OPERATIONS)}")
    owner_id = str(getattr(tool_context, "owner_id", None) or "").strip()
    if not owner_id:
        return _error(op, "memory is unavailable: no authenticated owner in tool context")
    session_id = getattr(tool_context, "session_id", None)

    from app.services.memory.memory_service import get_memory_service

    service = get_memory_service()
    try:
        if op == "search":
            return await _search(service, owner_id, query, memory_type, _clamp_limit(limit, 10))
        if op == "list":
            page = service.list_memories(
                owner_id,
                search=(query or None),
                memory_type=memory_type,
                importance=importance,
                limit=_clamp_limit(limit, 20),
                offset=max(0, int(offset or 0)),
            )
            return {"success": True, "operation": "list", **page}
        if op == "save":
            return await _save(service, owner_id, content, memory_type, importance,
                               _str_list(keywords), _str_list(tags), session_id)
        if not memory_id:
            return _error(op, f"memory_id is required for {op}")
        if op == "update":
            item = await service.update_memory(
                memory_id,
                owner_id,
                content=content,
                importance=importance,
                memory_type=memory_type,
                tags=_str_list(tags),
                keywords=_str_list(keywords),
            )
            if item is None:
                return _error(op, "memory not found")
            return {"success": True, "operation": "update", "item": item}
        if not service.delete_memory(memory_id, owner_id):
            return _error(op, "memory not found")
        return {"success": True, "operation": "forget", "deleted_id": memory_id}
    except ValueError as exc:
        return _error(op, f"invalid argument: {exc}")
    except Exception as exc:
        logger.warning("memory_operation %s failed: %s", op, exc)
        return _error(op, f"memory operation failed: {type(exc).__name__}")


memory_operation_tool = {
    "name": "memory_operation",
    "description": (
        "Manage the current user's long-term memory (durable preferences, facts, constraints, "
        "paths). Operations: search (semantic/keyword recall by query), list (paginated browse), "
        "save (store one atomic durable fact; near-duplicates are merged), update (edit by "
        "memory_id), forget (delete by memory_id). Scoped to the current user only."
    ),
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": list(MEMORY_OPERATIONS)},
            "query": {"type": "string", "description": "Search text (search) or filter text (list)."},
            "content": {"type": "string", "description": "Memory text for save/update; one atomic fact."},
            "memory_id": {"type": "string", "description": "Target memory id for update/forget."},
            "memory_type": {
                "type": "string",
                "enum": ["conversation", "experience", "knowledge", "context", "task_output", "evaluation"],
            },
            "importance": {"type": "string", "enum": ["critical", "high", "medium", "low", "temporary"]},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_LIMIT},
            "offset": {"type": "integer", "minimum": 0},
        },
        "required": ["operation"],
    },
    "handler": memory_operation_handler,
    "tags": ["memory", "preference", "recall", "remember", "forget"],
    "examples": [
        "Search the user's memory for their preferred plotting style",
        "Save the user's stated constraint that outputs must be in Chinese",
        "Forget a memory the user says is wrong",
    ],
}
