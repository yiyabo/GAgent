"""Fast read-only replies for explicit task-status questions."""

from __future__ import annotations

from typing import Any
import re

from .guardrails import extract_task_ids_from_text


_STATE_WORDS = ("失败", "完成", "结束", "执行中", "还在执行", "运行中", "卡住", "状态", "进度",
                "failed", "completed", "finished", "running", "status", "progress")
_QUESTION_WORDS = ("?", "？", "吗", "么", "呢", "是否", "是不是", "还在", "什么状态", "怎么样")
_DECISION_QUESTIONS = ("是否", "要不要", "需不需要", "该不该", "应不应该", "能否", "可否", "可以")
_ACTION_WORDS = ("重新执行", "继续执行", "开始执行", "执行任务", "执行task", "完成任务", "启动任务", "重试", "修复", "补交", "继续做", "帮我做", "run task", "retry task", "rerun task", "fix task")


def task_status_question(message: str) -> list[int]:
    text = str(message or "").strip().lower()
    if not text or len(text) > 180:
        return []
    ids = extract_task_ids_from_text(re.sub(r"(?:plan|计划)\s*#?\s*\d+", "", text))
    if not ids:
        return []
    if re.fullmatch(r"(?:任务|task)\s*#?\s*\d+", text):
        return ids[:5]
    if any(word in text for word in _DECISION_QUESTIONS) and any(word in text for word in _QUESTION_WORDS):
        return ids[:5]
    if any(word in text for word in _ACTION_WORDS):
        return []
    if not any(word in text for word in _STATE_WORDS) or not any(word in text for word in _QUESTION_WORDS):
        return []
    return ids[:5]


_STATUS = {
    "completed": "已完成", "done": "已完成", "failed": "失败", "running": "执行中",
    "pending": "待执行", "skipped": "已跳过", "cancelled": "已取消", "blocked": "受阻",
}


def task_status_reply(repo: Any, plan_id: int, task_ids: list[int]) -> str:
    tree = repo.get_plan_tree(plan_id)
    lines = []
    for task_id in task_ids:
        node = tree.nodes.get(task_id)
        if node is None:
            lines.append(f"计划 #{plan_id} 中没有 Task #{task_id}。")
            continue
        state = _STATUS.get(str(node.status or "").lower(), str(node.status or "未知"))
        lines.append(f"计划 #{plan_id} 的 Task #{task_id}「{node.display_name()}」当前状态：{state}。")
    if any(tree.nodes.get(task_id) and tree.nodes[task_id].status in {"failed", "skipped", "blocked"} for task_id in task_ids):
        lines.append("需要继续时，可以回复“重新执行”或点击任务旁的“重新执行”；任务会提交后台，查询状态不会启动任务。")
    elif any(tree.nodes.get(task_id) and tree.nodes[task_id].status == "running" for task_id in task_ids):
        lines.append("任务正在后台运行，可在计划运行记录查看进度；这条状态查询不会启动新的执行。")
    return "\n".join(lines)
