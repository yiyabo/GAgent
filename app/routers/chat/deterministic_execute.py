"""Deterministic-execute payload cluster of ``agent`` (W5a cluster ⑥).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
⑥ ``deterministic_execute.py``): the status normalizer, the bilingual fallback
text, the placeholder reasoning step and the final SSE payload builder for the
deterministic (non-DeepThink) execute shortcut.  ``agent.py`` re-exports every
name, so the class call sites are unchanged.

Patch surface: **zero body deviations**.  None of these names is patched in
``app/`` or ``app/tests/``, and no patched ``agent`` binding
(``execute_tool`` / ``plan_decomposition_jobs`` / job triple / ...) is read here.

Cross-cluster imports: ``_sanitize_chat_metadata_value`` and
``_extract_rerun_task_result_payload`` now live in ``response_metadata.py`` and
are imported from there (both were read as facade globals before, and neither is
patched anywhere, so every call expression stays verbatim).

No logger is used in this cluster; every payload key, status mapping, display
text and thought string (zh + en) is byte-identical.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import AgentResult
from .response_metadata import (
    _extract_rerun_task_result_payload,
    _sanitize_chat_metadata_value,
)


def _normalize_deterministic_execute_status(status: Any) -> Optional[str]:
    value = str(status or "").strip().lower()
    if not value:
        return None
    if value in {"queued", "pending"}:
        return "pending"
    if value in {"active", "running", "in_progress"}:
        return "running"
    if value in {"done", "success", "succeeded", "completed"}:
        return "completed"
    if value in {"error", "failed"}:
        return "failed"
    return value


def _build_deterministic_execute_fallback_text(
    *,
    task_id: Optional[int],
    status: Optional[str],
    language: str,
) -> str:
    if language == "zh":
        target = f"任务 {task_id}" if task_id is not None else "当前任务"
        if status in {"pending", "running"}:
            return f"{target} 已开始执行。"
        if status == "completed":
            return f"{target} 执行已完成。"
        if status == "failed":
            return f"{target} 执行失败。"
        return f"{target} 执行状态已更新。"

    target = f"Task {task_id}" if task_id is not None else "The task"
    if status in {"pending", "running"}:
        return f"{target} has started running."
    if status == "completed":
        return f"{target} completed."
    if status == "failed":
        return f"{target} failed."
    return f"{target} status was updated."


def _build_deterministic_execute_placeholder_step(
    *,
    language: str,
    status: str,
    started_at: str,
    finished_at: Optional[str] = None,
) -> Dict[str, Any]:
    if language == "zh":
        if status == "done":
            display_text = "任务上下文已就绪"
            thought = "已完成任务上下文、依赖产物与执行约束的准备，开始进入深度思考执行。"
        elif status == "error":
            display_text = "任务执行准备失败"
            thought = "任务上下文准备阶段发生错误，未能继续进入深度思考执行。"
        else:
            display_text = "准备任务上下文"
            thought = "正在加载任务上下文、依赖产物与执行约束。"
    else:
        if status == "done":
            display_text = "Task context ready"
            thought = "Task context, dependency artifacts, and execution constraints are ready. Starting DeepThink execution."
        elif status == "error":
            display_text = "Task preparation failed"
            thought = "The task preparation phase failed before DeepThink execution could continue."
        else:
            display_text = "Preparing task context"
            thought = "Loading task context, dependency artifacts, and execution constraints."

    payload: Dict[str, Any] = {
        "iteration": 0,
        "status": status,
        "display_text": display_text,
        "thought": thought,
        "kind": "reasoning",
        "started_at": started_at,
        "timestamp": started_at,
    }
    if finished_at:
        payload["finished_at"] = finished_at
    return payload


def _build_deterministic_execute_final_payload(
    result: "AgentResult",
    *,
    plan_id: Optional[int],
    language: str,
) -> Dict[str, Any]:
    response_text = str(result.reply or "").strip()
    metadata: Dict[str, Any] = {}
    serialized_actions: List[Dict[str, Any]] = []
    derived_task_id: Optional[int] = None
    if plan_id is not None:
        metadata["plan_id"] = plan_id

    for step in result.steps:
        action_payload = _sanitize_chat_metadata_value(step.action_payload)
        if isinstance(action_payload, dict) and action_payload:
            serialized_actions.append(action_payload)
            if derived_task_id is None:
                parameters = action_payload.get("parameters")
                if isinstance(parameters, dict):
                    try:
                        derived_task_id = int(parameters.get("task_id"))
                    except (TypeError, ValueError):
                        derived_task_id = None

    if result.steps:
        last_step = result.steps[-1]
        rerun_result, execution_payload = _extract_rerun_task_result_payload(last_step)

        source_metadata = execution_payload.get("metadata")
        if not isinstance(source_metadata, dict):
            source_metadata = rerun_result.get("metadata") if isinstance(rerun_result.get("metadata"), dict) else {}
        sanitized_source_metadata = _sanitize_chat_metadata_value(source_metadata)
        if isinstance(sanitized_source_metadata, dict):
            metadata.update(sanitized_source_metadata)

        for source in (execution_payload, rerun_result):
            if not isinstance(source, dict):
                continue
            for key in ("artifact_paths", "session_artifact_paths", "output_location"):
                value = _sanitize_chat_metadata_value(source.get(key))
                if value not in (None, [], {}):
                    metadata[key] = value

        step_details = last_step.details if isinstance(last_step.details, dict) else {}
        job_payload = _sanitize_chat_metadata_value(step_details.get("job"))
        if isinstance(job_payload, dict) and job_payload:
            metadata["job"] = job_payload

        for candidate in (
            execution_payload.get("content") if isinstance(execution_payload, dict) else None,
            rerun_result.get("content") if isinstance(rerun_result, dict) else None,
            last_step.message,
            response_text,
        ):
            text = str(candidate or "").strip()
            if text:
                response_text = text
                break

        status_value = str(
            (execution_payload.get("status") if isinstance(execution_payload, dict) else None)
            or rerun_result.get("status")
            or metadata.get("status")
            or ("completed" if result.success else "failed")
        ).strip()
        normalized_status = _normalize_deterministic_execute_status(status_value)
        if normalized_status:
            metadata["status"] = normalized_status

        for candidate in (
            rerun_result.get("task_id") if isinstance(rerun_result, dict) else None,
            metadata.get("task_id"),
            derived_task_id,
        ):
            try:
                if candidate is not None:
                    derived_task_id = int(candidate)
                    break
            except (TypeError, ValueError):
                continue

    if serialized_actions:
        metadata["actions"] = serialized_actions
        metadata["action_list"] = serialized_actions

    metadata["deterministic_execute_shortcut"] = True
    if not response_text:
        response_text = _build_deterministic_execute_fallback_text(
            task_id=derived_task_id,
            status=_normalize_deterministic_execute_status(metadata.get("status")),
            language=language,
        )
    if response_text:
        metadata.setdefault("analysis_text", response_text)
        metadata.setdefault("final_summary", response_text)

    return {
        "type": "final",
        "payload": {
            "response": response_text,
            "actions": serialized_actions,
            "metadata": metadata,
        },
    }
