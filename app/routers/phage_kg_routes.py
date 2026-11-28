"""Source papers behind PhageRAG knowledge-graph citations (``[doc-...]`` in answers)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..services.request_principal import require_authenticated_principal
from ..services.tool_schemas import phage_kg_enabled
from . import register_router

router = APIRouter(prefix="/phage-kg", tags=["phage-kg"])

register_router(
    namespace="phage_kg",
    version="v1",
    path="/phage-kg",
    router=router,
    tags=["phage-kg"],
    description="Read-only source documents of the PhageRAG knowledge graph",
)

_DOCUMENT_ID = re.compile(r"doc-[0-9a-f]{8,64}")
_FIELDS = ("document_id", "file_path", "shard_id", "title", "content", "content_truncated", "graph_version")


@router.get("/documents/{document_id}")
async def get_kg_document(document_id: str, request: Request) -> dict[str, Any]:
    require_authenticated_principal(request)
    if not phage_kg_enabled():
        raise HTTPException(status_code=404, detail="The phage knowledge graph is not enabled.")
    if not _DOCUMENT_ID.fullmatch(document_id):
        raise HTTPException(status_code=400, detail="Invalid knowledge-graph document id.")
    from tool_box.tools_impl.phage_kg import phage_kg_handler

    result = await phage_kg_handler(operation="document", document_id=document_id, max_chars=40000)
    if not result.get("success"):
        status = 404 if result.get("code") in {"RESOURCE_NOT_ACCESSIBLE", "NOT_FOUND"} else 502
        raise HTTPException(status_code=status, detail=str(result.get("error") or "Document unavailable."))
    return {key: result.get(key) for key in _FIELDS if result.get(key) is not None}
