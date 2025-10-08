"""
路由辅助函数

包含所有路由共用的解析和验证函数，从main.py中提取出来。
"""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException


def parse_bool(val, default: bool = False) -> bool:
    """解析布尔值参数"""
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    return default


def parse_int(val, default: int, min_value: int, max_value: int) -> int:
    """解析整数参数，带范围限制"""
    try:
        i = int(val)
    except (ValueError, TypeError, OverflowError):
        return default
    try:
        i = max(min_value, min(int(i), max_value))
    except (ValueError, TypeError):
        return default
    return i


def parse_opt_float(val, min_value: float, max_value: float):
    """解析可选浮点数参数"""
    if val is None:
        return None
    try:
        f = float(val)
    except (ValueError, TypeError, OverflowError):
        return None
    try:
        f = max(min_value, min(float(f), max_value))
    except (ValueError, TypeError):
        return None
    return f


def parse_opt_int(val, min_value: int, max_value: int):
    """解析可选整数参数"""
    if val is None:
        return None
    try:
        i = int(val)
    except (ValueError, TypeError, OverflowError):
        return None
    try:
        i = max(min_value, min(int(i), max_value))
    except (ValueError, TypeError):
        return None
    return i


def parse_strategy(val) -> str:
    """解析策略参数"""
    if not isinstance(val, str):
        return "truncate"
    v = val.strip().lower()
    return v if v in {"truncate", "sentence"} else "truncate"


def parse_schedule(val) -> str:
    """解析调度策略参数: 'bfs' (default), 'dag', or 'postorder'."""
    if not isinstance(val, str):
        return "bfs"
    v = val.strip().lower()
    return v if v in {"bfs", "dag", "postorder"} else "bfs"


def sanitize_manual_list(vals) -> Optional[List[int]]:
    """清理手动任务ID列表"""
    if not isinstance(vals, list):
        return None
    out: List[int] = []
    for x in vals:
        try:
            out.append(int(x))
        except (ValueError, TypeError, OverflowError):
            continue
    if not out:
        return None
    # dedup and cap size
    dedup = list(dict.fromkeys(out))
    return dedup[:50]


def sanitize_context_options(co: Dict[str, Any]) -> Dict[str, Any]:
    """清理上下文选项参数"""
    co = co or {}
    return {
        "include_deps": parse_bool(co.get("include_deps"), default=True),
        "include_plan": parse_bool(co.get("include_plan"), default=True),
        "k": parse_int(co.get("k", 5), default=5, min_value=0, max_value=50),
        "manual": sanitize_manual_list(co.get("manual")),
        # GLM semantic retrieval options (now default enabled)
        "semantic_k": parse_int(co.get("semantic_k", 5), default=5, min_value=0, max_value=50),
        "min_similarity": parse_opt_float(co.get("min_similarity", 0.1), min_value=0.0, max_value=1.0) or 0.1,
        # hierarchy options (Phase 5)
        "include_ancestors": parse_bool(co.get("include_ancestors"), default=False),
        "include_siblings": parse_bool(co.get("include_siblings"), default=False),
        "hierarchy_k": parse_int(co.get("hierarchy_k", 3), default=3, min_value=0, max_value=20),
        # budgeting options
        "max_chars": parse_opt_int(co.get("max_chars"), min_value=0, max_value=100_000),
        # non-positive per_section_max considered invalid → None
        "per_section_max": (
            None
            if (co.get("per_section_max") is not None and parse_opt_int(co.get("per_section_max"), 1, 50_000) is None)
            else parse_opt_int(co.get("per_section_max"), min_value=1, max_value=50_000)
        ),
        "strategy": parse_strategy(co.get("strategy")),
        # snapshot controls
        "save_snapshot": parse_bool(co.get("save_snapshot"), default=False),
        "label": (str(co.get("label")).strip()[:64] if co.get("label") else None),
    }


def resolve_scope_params(
    session_id: Optional[str],
    workflow_id: Optional[str],
    *,
    repo=None,
    require_scope: bool = False,
    default_session: Optional[str] = None,  # 🔒 改为None，实现专事专办
) -> Tuple[Optional[str], Optional[str]]:
    """Validate and resolve session/workflow scope parameters.

    When ``workflow_id`` is provided, ensure it存在且归属提供的 ``session_id``。
    当 ``require_scope`` 为 ``True`` 且两个参数均为空时抛出 400。
    默认情况下如果未提供任何作用域但指定了 ``default_session``，则回退到默认会话。
    """

    if repo is None:
        from ..repository.tasks import default_repo

        repo = default_repo

    normalized_session = (session_id or "").strip() or None
    normalized_workflow = (workflow_id or "").strip() or None

    if require_scope and not normalized_session and not normalized_workflow:
        raise HTTPException(status_code=400, detail="必须提供 session_id 或 workflow_id 参数")

    if normalized_workflow:
        metadata = repo.get_workflow_metadata(normalized_workflow)
        if not metadata:
            raise HTTPException(status_code=404, detail="指定的 workflow_id 不存在")
        workflow_session = metadata.get("session_id") or None
        if normalized_session and workflow_session and normalized_session != workflow_session:
            raise HTTPException(status_code=403, detail="workflow_id 不属于指定的 session_id")
        normalized_session = normalized_session or workflow_session

    if not normalized_session and default_session:
        normalized_session = default_session

    return normalized_session, normalized_workflow
