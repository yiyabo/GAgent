"""
上下文管理相关API端点

包含任务间链接管理、上下文预览和快照功能。
"""

import os
from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict, Optional

from ..repository.tasks import default_repo
from ..services.context.context import gather_context
from ..services.context.context_budget import apply_budget
from ..models import ContextPreviewRequest
from ..utils.route_helpers import (
    parse_bool, parse_int, parse_opt_int, parse_opt_float, 
    parse_strategy, sanitize_manual_list, sanitize_context_options
)

router = APIRouter(tags=["context"])


@router.post("/context/links")
def create_link(payload: Dict[str, Any]):
    """创建任务间的上下文链接"""
    try:
        from_id = int(payload.get("from_id"))
        to_id = int(payload.get("to_id"))
        kind = str(payload.get("kind") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")
    if not from_id or not to_id or not kind:
        raise HTTPException(status_code=400, detail="from_id, to_id, kind are required")
    default_repo.create_link(from_id, to_id, kind)
    return {"ok": True, "link": {"from_id": from_id, "to_id": to_id, "kind": kind}}


@router.delete("/context/links")
def delete_link(payload: Dict[str, Any]):
    """删除任务间的上下文链接"""
    try:
        from_id = int(payload.get("from_id"))
        to_id = int(payload.get("to_id"))
        kind = str(payload.get("kind") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")
    if not from_id or not to_id or not kind:
        raise HTTPException(status_code=400, detail="from_id, to_id, kind are required")
    default_repo.delete_link(from_id, to_id, kind)
    return {"ok": True}


@router.get("/context/links/{task_id}")
def get_links(task_id: int):
    """获取指定任务的所有入站和出站链接"""
    inbound = default_repo.list_links(to_id=task_id)
    outbound = default_repo.list_links(from_id=task_id)
    return {"task_id": task_id, "inbound": inbound, "outbound": outbound}


# 任务上下文相关端点
@router.post("/tasks/{task_id}/context/preview")
def context_preview(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """预览任务的上下文信息"""
    # Typed parsing
    try:
        req = ContextPreviewRequest.model_validate(payload or {})
    except Exception:
        req = ContextPreviewRequest()
    include_deps = bool(req.include_deps)
    include_plan = bool(req.include_plan)
    k = parse_int(req.k, default=5, min_value=0, max_value=50)
    # Phase 2 options (optional): budgeting
    max_chars = parse_opt_int(req.max_chars, min_value=0, max_value=100_000)
    per_section_max = parse_opt_int(req.per_section_max, min_value=1, max_value=50_000)
    # Optional summarization strategy: 'truncate' (default) or 'sentence'
    strategy = parse_strategy(req.strategy) if (max_chars is not None or per_section_max is not None) else None
    # GLM semantic retrieval options
    semantic_k = parse_int(req.semantic_k, default=5, min_value=0, max_value=50)
    min_similarity = parse_opt_float(req.min_similarity, min_value=0.0, max_value=1.0) or 0.1
    # Hierarchy options (Phase 5)
    include_ancestors = bool(req.include_ancestors)
    include_siblings = bool(req.include_siblings)
    hierarchy_k = parse_int(req.hierarchy_k, default=3, min_value=0, max_value=20)
    manual = sanitize_manual_list(req.manual)
    bundle = gather_context(
        task_id,
        repo=default_repo,
        include_deps=include_deps,
        include_plan=include_plan,
        k=k,
        manual=manual,
        semantic_k=semantic_k,
        min_similarity=min_similarity,
        include_ancestors=include_ancestors,
        include_siblings=include_siblings,
        hierarchy_k=hierarchy_k,
    )
    # Apply budget only when options are provided (backward compatible)
    if (max_chars is not None) or (per_section_max is not None):
        max_chars = parse_opt_int(max_chars, min_value=0, max_value=100_000)
        per_section_max = parse_opt_int(per_section_max, min_value=1, max_value=50_000)
        strategy = parse_strategy(strategy) if strategy else "truncate"
        bundle = apply_budget(bundle, max_chars=max_chars, per_section_max=per_section_max, strategy=strategy)
    return bundle


@router.get("/tasks/{task_id}/context/snapshots")
def list_task_contexts_api(task_id: int):
    """列出指定任务的所有上下文快照"""
    snaps = default_repo.list_task_contexts(task_id)
    return {"task_id": task_id, "snapshots": snaps}


@router.get("/tasks/{task_id}/context/snapshots/{label}")
def get_task_context_api(task_id: int, label: str):
    """获取指定任务和标签的上下文快照"""
    snap = default_repo.get_task_context(task_id, label)
    if not snap:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return snap


# 全局索引端点
def _global_index_path() -> str:
    p = os.environ.get("GLOBAL_INDEX_PATH")
    return p if (isinstance(p, str) and p.strip()) else "INDEX.md"


@router.get("/index")
def get_global_index():
    """获取全局索引文件内容"""
    path = _global_index_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""
    return {"path": path, "content": content}


@router.put("/index")
def put_global_index(payload: Dict[str, Any] = Body(...)):
    """更新全局索引文件内容"""
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content (string) is required")
    path = payload.get("path") if isinstance(payload, dict) else None
    if not isinstance(path, str) or not path.strip():
        path = _global_index_path()
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}") from e
    return {"ok": True, "path": path, "bytes": len(content)}
