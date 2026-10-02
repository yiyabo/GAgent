"""Explicit continuation creates a new run; terminal history stays immutable."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def current_resume_source() -> str | None:
    """Nested plan/tool controllers inherit the parent run's continuation intent."""
    from app.repository.chat_runs import get_chat_run
    from app.services.chat_run_state import chat_run_claim

    claim = chat_run_claim.get()
    if claim is None:
        return None
    row = get_chat_run(claim[0])
    payload = json.loads((row or {}).get("request_json") or "{}")
    source = (payload.get("context") or {}).get("resume_from_run_id")
    return str(source) if source else None


async def prepare_run_resume(run_id: str, context: dict[str, Any]) -> None:
    source = context.get("resume_from_run_id")
    if not source:
        return
    from app.services.execution.step_ledger import StepLedger

    ledger = StepLedger(run_id)
    await asyncio.to_thread(ledger.import_from, str(source))


def resume_info(row: dict) -> dict:
    """Read-only capability check; never claims or alters a terminal run."""
    from app.repository.run_steps import list_checkpoint_pointers, list_steps
    from app.services.execution.step_ledger import StepLedger

    result = {"run_id": row["run_id"], "session_id": row["session_id"],
              "status": row["status"], "can_resume": False, "reason_code": "not_terminal",
              "reason": "任务尚未结束，请先等待或停止任务。"}
    if row["status"] not in {"failed", "cancelled"}:
        return result
    try:
        payload = json.loads(row.get("request_json") or "{}")
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("missing original message")
        result["message"] = message
    except (ValueError, TypeError, AttributeError):
        return {**result, "reason_code": "request_unavailable", "reason": "原始请求不完整，无法继续此任务。"}
    try:
        pointers = list_checkpoint_pointers(row["run_id"])
        pointer = next((item for item in pointers if item["checkpoint_key"].startswith("chat:")), None)
        pointer = pointer or (pointers[0] if pointers else None)
        checkpoint = StepLedger(row["run_id"]).load_checkpoint(checkpoint_key=pointer["checkpoint_key"]) if pointer else None
        if checkpoint is None:
            return {**result, "reason_code": "checkpoint_missing", "reason": "该任务没有可恢复的执行检查点，请重新发起任务。"}
        latest = {}
        for step in list_steps(row["run_id"]):
            key = (step.key.tool_call_id, step.key.params_fingerprint)
            if key not in latest or step.key.attempt > latest[key].key.attempt:
                latest[key] = step
        if any(step.replay_policy == "mutating" and step.status in {"running", "interrupted", "failed"} for step in latest.values()):
            return {**result, "reason_code": "reconciliation_required", "reason": "部分写入操作的结果尚未确认，需要先核对原任务的文件或外部操作。"}
    except (ValueError, RuntimeError, OSError):
        return {**result, "reason_code": "checkpoint_unavailable", "reason": "执行检查点损坏或已不可读取，无法直接继续。"}
    return {**result, "can_resume": True, "reason_code": "ready", "reason": "将创建一条新任务继续执行；已完成步骤会在校验后复用。"}


def annotate_unanswered_turns(messages: list, session_id: str, owner_id: str) -> None:
    """History keeps a continuation entry after cancellation with no final answer."""
    from app.repository.context_recall import terminal_turns_without_answer

    users = {message.id: message for message in messages if message.role == 'user' and message.id is not None}
    for row in terminal_turns_without_answer(session_id, owner_id, list(users)):
        message = users[row['user_message_id']]
        message.metadata = {**(message.metadata or {}), 'resume_run_id': row['run_id'], 'resume_run_status': row['status']}
