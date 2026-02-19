"""确认机制相关代码：危险操作的确认流程管理。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# 需要用户确认才能执行的危险操作（仅删除类操作需要确认）
ACTIONS_REQUIRING_CONFIRMATION = {
    ("plan_operation", "delete_plan"),      # 删除计划
    ("task_operation", "delete_task"),      # 删除任务
    ("task_operation", "clear_tasks"),      # 清空任务
}

# 待确认操作存储: {confirmation_id: {session_id, actions, structured, created_at, ...}}
_pending_confirmations: Dict[str, Dict[str, Any]] = {}

def _generate_confirmation_id() -> str:
    """生成确认ID"""
    return f"confirm_{uuid4().hex[:12]}"

def _requires_confirmation(actions: List[Any]) -> bool:
    """检查操作列表是否包含需要确认的操作"""
    for action in actions:
        key = (getattr(action, 'kind', None), getattr(action, 'name', None))
        if key in ACTIONS_REQUIRING_CONFIRMATION:
            return True
    return False

def _store_pending_confirmation(
    confirmation_id: str,
    session_id: str,
    actions: List[Any],
    structured: Any,
    plan_id: Optional[int] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> None:
    """存储待确认的操作"""
    _pending_confirmations[confirmation_id] = {
        "session_id": session_id,
        "actions": actions,
        "structured": structured,
        "plan_id": plan_id,
        "extra_context": extra_context or {},
        "created_at": datetime.now().isoformat(),
    }
    logger.info(f"[CONFIRMATION] Stored pending confirmation: {confirmation_id} for session {session_id}")

def _get_pending_confirmation(confirmation_id: str) -> Optional[Dict[str, Any]]:
    """获取待确认的操作"""
    return _pending_confirmations.get(confirmation_id)

def _remove_pending_confirmation(confirmation_id: str) -> Optional[Dict[str, Any]]:
    """移除并返回待确认的操作"""
    return _pending_confirmations.pop(confirmation_id, None)

def _cleanup_old_confirmations(max_age_seconds: int = 600) -> None:
    """清理过期的待确认操作（默认10分钟）"""
    now = datetime.now()
    expired = []
    for cid, data in _pending_confirmations.items():
        created = datetime.fromisoformat(data["created_at"])
        if (now - created).total_seconds() > max_age_seconds:
            expired.append(cid)
    for cid in expired:
        del _pending_confirmations[cid]
        logger.info(f"[CONFIRMATION] Cleaned up expired confirmation: {cid}")
