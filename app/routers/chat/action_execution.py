"""Action execution helpers and action-route handlers.

This module contains agent bookkeeping utilities plus route-level action
analysis/execution handlers used by the chat API endpoints.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException
from fastapi import BackgroundTasks
from app.repository.chat_action_runs import (
    create_action_run,
    fetch_action_run,
    update_action_run,
)
from app.repository.plan_storage import append_action_log_entry, record_decomposition_job, record_phagescope_tracking
from app.services.deep_think_agent import DeepThinkAgent
from app.services.llm.structured_response import LLMAction, LLMStructuredResponse
from app.services.plans.decomposition_jobs import (
    get_current_job,
    plan_decomposition_jobs,
    reset_current_job,
    set_current_job,
    start_phagescope_track_job_thread,
)
from app.services.plans.plan_session import PlanSession
from tool_box import execute_tool

from .models import ActionStatusResponse
from .services import (
    get_structured_chat_agent_cls,
    plan_decomposer_service,
    plan_executor_service,
    plan_repository,
)
from .session_helpers import (
    _backfill_phagescope_submit_params,
    _build_phagescope_submit_background_summary,
    _derive_conversation_id,
    _extract_phagescope_task_snapshot,
    _extract_taskid_from_result,
    _get_llm_service_for_provider,
    _get_session_current_task,
    _get_session_settings,
    _is_empty_phagescope_param,
    _merge_async_metadata,
    _normalize_base_model,
    _normalize_llm_provider,
    _normalize_modulelist_value,
    _normalize_search_provider,
    _record_phagescope_task_memory,
    _set_session_plan_id,
    _update_message_content_by_tracking,
    _update_message_metadata_by_tracking,
)

if TYPE_CHECKING:
    from app.services.llm.structured_response import LLMAction
    from .models import AgentStep

logger = logging.getLogger(__name__)

_AUTO_DEEP_THINK_RETRY_ENV = "CHAT_AUTO_DEEP_THINK_RETRY_ON_BLOCKING_FAILURE"
_AUTO_DEEP_THINK_RETRY_MAX_ITER_ENV = "CHAT_AUTO_DEEP_THINK_RETRY_MAX_ITERATIONS"
_AUTO_DEEP_THINK_RETRY_TOOL_TIMEOUT_ENV = "CHAT_AUTO_DEEP_THINK_RETRY_TOOL_TIMEOUT"
_AUTO_DEEP_THINK_RETRY_CONTEXT_KEY = "auto_deep_think_retry_on_blocking_failure"
_AUTO_DEEP_THINK_RETRY_AVAILABLE_TOOLS: List[str] = [
    "web_search",
    "graph_rag",
    "claude_code",
    "file_operations",
    "document_reader",
    "vision_reader",
    "bio_tools",
    "phagescope",
    "result_interpreter",
]


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: Any, default: int, *, min_value: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < min_value:
        return min_value
    return parsed


def _clip_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _extract_blocking_failures(steps: List[Any]) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for step in steps or []:
        action = getattr(step, "action", None)
        if action is None:
            continue
        if not bool(getattr(action, "blocking", False)):
            continue
        if bool(getattr(step, "success", False)):
            continue
        details = step.details if isinstance(step.details, dict) else {}
        failures.append(
            {
                "kind": str(getattr(action, "kind", "") or ""),
                "name": str(getattr(action, "name", "") or ""),
                "message": _clip_text(getattr(step, "message", "")),
                "parameters": dict(getattr(action, "parameters", {}) or {}),
                "details_error": _clip_text(details.get("error")),
            }
        )
    return failures


def _auto_deep_think_retry_enabled(context: Dict[str, Any]) -> bool:
    context_value = context.get(_AUTO_DEEP_THINK_RETRY_CONTEXT_KEY)
    if context_value is not None:
        return _truthy(context_value, default=True)
    return _truthy(os.getenv(_AUTO_DEEP_THINK_RETRY_ENV, "1"), default=True)


def _build_blocking_failure_retry_prompt(
    *,
    user_message: str,
    failures: List[Dict[str, Any]],
) -> str:
    failure_lines: List[str] = []
    for idx, item in enumerate(failures, start=1):
        failure_lines.append(
            f"{idx}. {item.get('kind')}/{item.get('name')} failed: {item.get('message') or item.get('details_error') or 'unknown'}"
        )
        params = item.get("parameters")
        if isinstance(params, dict) and params:
            failure_lines.append(f"   params={json.dumps(params, ensure_ascii=False, default=str)[:1200]}")

    failure_text = "\n".join(failure_lines) if failure_lines else "(none)"
    return (
        "You are executing one automatic recovery attempt after a blocking action failure.\n"
        "Goal: complete the user's original request with available tools.\n"
        "Rules:\n"
        "1) Retry only once in this run; prioritize fixing failed blocking actions.\n"
        "2) If tool params were wrong, correct them and rerun.\n"
        "3) If still failing, provide the best actionable fallback and clearly state remaining blockers.\n\n"
        f"Original user request:\n{user_message}\n\n"
        f"Blocking failures from previous run:\n{failure_text}\n"
    )


async def _run_blocking_failure_deep_think_retry_once(
    *,
    agent: Any,
    run_id: str,
    user_message: str,
    context: Dict[str, Any],
    failures: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not user_message.strip():
        return {
            "attempted": True,
            "success": False,
            "error": "Missing user message for DeepThink retry",
        }

    async def _fallback_tool_executor(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        safe_params = params if isinstance(params, dict) else {}
        action = LLMAction(
            kind="tool_operation",
            name=str(name),
            parameters=safe_params,
            order=1,
            blocking=True,
            metadata={"origin": "auto_deep_think_retry"},
        )
        step = await agent._handle_tool_action(action)
        details = step.details if isinstance(step.details, dict) else {}
        result_payload = details.get("result")
        if isinstance(result_payload, dict):
            result: Dict[str, Any] = dict(result_payload)
        else:
            error_text = (
                details.get("error")
                or step.message
                or "Tool execution returned malformed result payload."
            )
            result = {
                "success": False,
                "tool": str(name),
                "error": str(error_text),
                "summary": _clip_text(step.message or error_text, limit=600),
                "protocol_warning": True,
                "parameters": dict(safe_params),
            }
        if "success" not in result:
            result["success"] = bool(step.success)
        if isinstance(step.message, str) and step.message.strip():
            result.setdefault("summary", step.message.strip())
        return result

    dt_agent_cls = DeepThinkAgent
    try:  # pragma: no cover - compatibility bridge
        from app.routers import chat_routes as compat_chat_routes

        compat_candidate = getattr(compat_chat_routes, "DeepThinkAgent", None)
        if inspect.isclass(compat_candidate):
            dt_agent_cls = compat_candidate
    except Exception:
        pass

    max_iterations = _parse_int(
        os.getenv(_AUTO_DEEP_THINK_RETRY_MAX_ITER_ENV, "12"),
        default=12,
        min_value=1,
    )
    tool_timeout = _parse_int(
        os.getenv(_AUTO_DEEP_THINK_RETRY_TOOL_TIMEOUT_ENV, "120"),
        default=120,
        min_value=1,
    )

    retry_prompt = _build_blocking_failure_retry_prompt(
        user_message=user_message,
        failures=failures,
    )
    retry_context = dict(context or {})
    retry_context["auto_deep_think_retry"] = True
    retry_context["auto_deep_think_retry_tracking_id"] = run_id
    retry_context["blocking_failures"] = failures

    dt_agent = dt_agent_cls(
        llm_client=agent.llm_service,
        available_tools=_AUTO_DEEP_THINK_RETRY_AVAILABLE_TOOLS,
        tool_executor=_fallback_tool_executor,
        max_iterations=max_iterations,
        tool_timeout=tool_timeout,
    )
    result = await dt_agent.think(retry_prompt, retry_context)
    final_answer = str(getattr(result, "final_answer", "") or "").strip()
    return {
        "attempted": True,
        "success": bool(final_answer),
        "final_answer": final_answer,
        "tools_used": list(getattr(result, "tools_used", []) or []),
        "iterations": int(getattr(result, "total_iterations", 0) or 0),
        "confidence": float(getattr(result, "confidence", 0.0) or 0.0),
    }


def resolve_job_meta(agent: Any) -> Tuple[str, str]:
    job_id = get_current_job()
    job_type = "chat_action"
    if job_id:
        job = plan_decomposition_jobs.get_job(job_id)
        if job is not None and getattr(job, "job_type", None):
            job_type = job.job_type
        return job_id, job_type
    if agent._sync_job_id is None:
        prefix = (agent.session_id or "session").replace(":", "_")
        agent._sync_job_id = f"sync_{prefix}_{uuid4().hex}"
        try:
            record_decomposition_job(
                agent.plan_session.plan_id,
                job_id=agent._sync_job_id,
                job_type="chat_action",
                mode=agent.mode or "assistant",
                target_task_id=None,
                status="running",
                params={
                    "session_id": agent.session_id,
                    "mode": agent.mode,
                },
                metadata={
                    "session_id": agent.session_id,
                    "conversation_id": agent.conversation_id,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to record sync job metadata: %s", exc)
    return agent._sync_job_id, job_type


def log_action_event(
    agent: Any,
    action: "LLMAction",
    *,
    status: str,
    success: Optional[bool],
    message: Optional[str],
    parameters: Optional[Dict[str, Any]],
    details: Optional[Dict[str, Any]],
) -> None:
    try:
        job_id, job_type = resolve_job_meta(agent)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve job metadata for logging: %s", exc)
        return
    try:
        append_action_log_entry(
            plan_id=agent.plan_session.plan_id,
            job_id=job_id,
            job_type=job_type,
            sequence=action.order if isinstance(action.order, int) else None,
            session_id=agent.session_id,
            user_message=agent._current_user_message,
            action_kind=action.kind or "",
            action_name=action.name or "",
            status=status,
            success=success,
            message=message,
            parameters=parameters,
            details=details,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to persist action log entry: %s", exc)


def truncate_summary_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def build_actions_summary(agent: Any, steps: List["AgentStep"]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for step in steps:
        action = step.action
        summary.append({
            "order": action.order,
            "kind": action.kind,
            "name": action.name,
            "success": step.success,
            "message": truncate_summary_text(step.message),
        })
    return summary


def append_summary_to_reply(
    agent: Any, reply: str, summary: List[Dict[str, Any]]
) -> str:
    # Do not append action summary at the end of replies:
    # the frontend already provides status tags and a "View process" panel.
    # Keep this method signature for backward compatibility.
    return reply


# ---------------------------------------------------------------------------
# Route-level handlers for action analysis/execution endpoints.
# ---------------------------------------------------------------------------


async def _generate_tool_analysis(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    """Use the LLM to generate a detailed analysis from tool results."""
    try:
        tools_description = []
        web_search_items: List[Dict[str, str]] = []
        for idx, tool_result in enumerate(tool_results, 1):
            tool_name = tool_result.get("name", "unknown")
            summary = tool_result.get("summary", "")
            result_data = tool_result.get("result", {})

            tool_desc = f"{idx}. Tool: {tool_name}"
            if summary:
                tool_desc += f"\n   Execution summary: {summary}"

            if isinstance(result_data, dict):
                useful_fields = ["output", "stdout", "stderr", "success", "error"]
                for field in useful_fields:
                    if field in result_data and result_data[field]:
                        value = result_data[field]
                        tool_desc += f"\n   {field}: {value}"
                if tool_name == "web_search":
                    results = result_data.get("results")
                    if isinstance(results, list) and results:
                        tool_desc += "\n   results:"
                        for item in results:
                            if not isinstance(item, dict):
                                continue
                            title = str(item.get("title") or "").strip()
                            url = str(item.get("url") or "").strip()
                            snippet = str(item.get("snippet") or "").strip()
                            tool_desc += f"\n   - title: {title}"
                            tool_desc += f"\n     url: {url}"
                            if snippet:
                                tool_desc += f"\n     snippet: {snippet}"
                            web_search_items.append(
                                {
                                    "title": title,
                                    "url": url,
                                }
                            )
                else:
                    details_payload = {}
                    for field in (
                        "data",
                        "results",
                        "action",
                        "status_code",
                        "result_kind",
                    ):
                        if field in result_data and result_data[field] is not None:
                            details_payload[field] = result_data[field]
                    if details_payload:
                        details_text = json.dumps(details_payload, ensure_ascii=True)
                        if len(details_text) > 1200:
                            details_text = details_text[:1197] + "..."
                        tool_desc += f"\n   details: {details_text}"

            tools_description.append(tool_desc)

        tools_text = "\n\n".join(tools_description)

        analysis_requirements = [
            "1. Provide a complete, in-depth analysis; do not repeat the user question.",
            "2. Clearly separate conclusions, evidence, and caveats/risks.",
            "3. If web_search is involved, list each result and explain its value.",
            "4. If errors or uncertainty exist, explain why and propose next steps.",
            "5. Output at least 6 bullet points or 3 natural paragraphs.",
            "6. Use only fields present in the tool outputs; do not invent paths, modules, or metrics.",
            "7. If a field is missing, explicitly say it was not provided by the tool.",
            "8. Prefer factual summaries over speculation.",
        ]

        base_prompt = (
            "You are a senior analysis assistant. Below are the user question and tool execution results.\n"
            "Write a detailed analysis body that can be shown directly as the final answer.\n\n"
            f"User question: {user_message}\n\n"
            "Tool execution results:\n"
            f"{tools_text}\n\n"
            "Requirements:\n"
            + "\n".join(analysis_requirements)
            + "\n\nOutput analysis:"
        )
        llm_service = _get_llm_service_for_provider(llm_provider)

        analysis = await llm_service.chat_async(base_prompt)
        return analysis.strip() if analysis else None

    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate analysis for session=%s: %s",
            session_id,
            exc,
        )
        return None


async def _generate_tool_summary(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    """Use the LLM to generate a short summary from tool results (for process panel)."""
    try:
        tools_description = []
        for idx, tool_result in enumerate(tool_results, 1):
            tool_name = tool_result.get("name", "unknown")
            summary = tool_result.get("summary", "")
            tool_desc = f"{idx}. {tool_name}"
            if summary:
                tool_desc += f" - {summary}"
            tools_description.append(tool_desc)

        tools_text = "\n".join(tools_description)
        prompt = (
            "You are a project assistant. Provide a brief summary (1-3 sentences) based on tool execution.\n"
            f"User question: {user_message}\n"
            f"Tool execution overview:\n{tools_text}\n"
            "Output summary:"
        )
        llm_service = _get_llm_service_for_provider(llm_provider)
        summary = await llm_service.chat_async(prompt)
        return summary.strip() if summary else None
    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate brief summary for session=%s: %s",
            session_id,
            exc,
        )
        return None


def _collect_created_tasks_from_steps(steps: List[Any]) -> List[Dict[str, Any]]:
    created: List[Dict[str, Any]] = []
    for step in steps:
        details = step.details or {}
        created_nodes = details.get("created")
        if isinstance(created_nodes, list):
            for node in created_nodes:
                if isinstance(node, dict):
                    created.append(node)
        task_node = details.get("task")
        if isinstance(task_node, dict):
            created.append(task_node)
    return created


async def _generate_action_analysis(
    user_message: str,
    steps: List[Any],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    created_tasks = _collect_created_tasks_from_steps(steps)

    step_summaries: List[str] = []
    for step in steps:
        if not step.success:
            continue
        details = step.details or {}
        kind = step.action.kind or ""
        name = step.action.name or ""
        msg = step.message or ""
        if kind in ("plan_operation", "task_operation"):
            detail_text = msg
            if isinstance(details, dict):
                for key in ("plans", "outline", "task", "plan_id", "task_count"):
                    val = details.get(key)
                    if val is not None:
                        detail_text += (
                            f"\n{key}: "
                            f"{json.dumps(val, ensure_ascii=False, default=str)[:2000]}"
                        )
            step_summaries.append(f"[{kind}/{name}] {detail_text}")

    if created_tasks:
        lines: List[str] = []
        for idx, task in enumerate(created_tasks, 1):
            task_name = str(task.get("name") or task.get("title") or "").strip()
            instruction = str(task.get("instruction") or "").strip()
            if task_name:
                lines.append(f"{idx}. {task_name}")
            if instruction:
                lines.append(f"   - Instruction: {instruction}")
        tasks_text = "\n".join(lines)

        prompt = (
            "You are a project analysis assistant. The user requests a detailed analysis of task decomposition results. "
            "Based on the decomposition, analyze coverage sufficiency, relationships between tasks, "
            "possible omissions, and potential refinement directions (if any). Do not repeat summaries like "
            "'X subtasks were generated'; provide a professional analysis body directly.\n"
            "Requirements: at least 6 bullet points or 3 natural paragraphs; points must be clear, concrete, and actionable.\n\n"
            f"User question: {user_message}\n\n"
            "Decomposition results:\n"
            f"{tasks_text}\n\n"
            "Output analysis:"
        )
    elif step_summaries:
        steps_text = "\n\n".join(step_summaries)
        prompt = (
            "You are a project analysis assistant. The user requests analysis of background task execution results. "
            "Based on outputs from the following execution steps, provide a structured analysis: key findings, critical data, "
            "and next-step recommendations. Output the professional analysis body directly; avoid preambles like "
            "'I will analyze this now.'\n"
            "Requirements: specific, data-driven, and actionable.\n\n"
            f"User question: {user_message}\n\n"
            "Execution results:\n"
            f"{steps_text}\n\n"
            "Output analysis:"
        )
    else:
        return None
    try:
        llm_service = _get_llm_service_for_provider(llm_provider)
        analysis = await llm_service.chat_async(prompt)
        return analysis.strip() if analysis else None
    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate action analysis for session=%s: %s",
            session_id,
            exc,
        )
        return None


def _build_brief_action_summary(steps: List[Any]) -> Optional[str]:
    if not steps:
        return None
    if len(steps) == 1:
        step = steps[0]
        if step.message:
            return step.message
        if step.action.name:
            return f"Completed action: {step.action.name}"
        if step.action.kind:
            return f"Completed action: {step.action.kind}"
        return None

    names: List[str] = []
    for step in steps:
        if step.action.name:
            names.append(step.action.name)
        elif step.action.kind:
            names.append(step.action.kind)
    if not names:
        return f"Completed {len(steps)} actions."
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    preview = ", ".join(unique[:3])
    suffix = " and more" if len(unique) > 3 else ""
    return f"Completed {len(steps)} actions: {preview}{suffix}."


async def _execute_action_run(run_id: str) -> None:
    structured_chat_agent_cls = get_structured_chat_agent_cls()
    record = fetch_action_run(run_id)
    if not record:
        logger.warning("Action run %s not found when executing", run_id)
        return

    try:
        update_action_run(run_id, status="running")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to mark action run %s as running: %s", run_id, exc)

    logger.info(
        "[CHAT][ASYNC][START] tracking=%s session=%s plan=%s",
        run_id,
        record.get("session_id"),
        record.get("plan_id"),
    )

    plan_session = PlanSession(repo=plan_repository, plan_id=record.get("plan_id"))
    try:
        plan_session.refresh()
    except ValueError:
        plan_session.detach()

    # Parse structured payload early so we can decide job_type/mode.
    try:
        structured = LLMStructuredResponse.model_validate_json(record["structured_json"])
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Structured payload invalid for run %s: %s", run_id, exc)
        update_action_run(run_id, status="failed", errors=[str(exc)])
        return

    sorted_actions = structured.sorted_actions()
    primary_action = sorted_actions[0] if sorted_actions else None

    # PhageScope is treated as a special, long-running remote job:
    # submit -> return taskid immediately -> track via backend polling job.
    phagescope_submit_action: Optional[LLMAction] = None
    dropped_phagescope_actions: List[str] = []
    phagescope_only_actions = bool(sorted_actions) and all(
        action.kind == "tool_operation" and action.name == "phagescope"
        for action in sorted_actions
    )
    if phagescope_only_actions:
        for action in sorted_actions:
            if not isinstance(action.parameters, dict):
                continue
            if str(action.parameters.get("action") or "").strip().lower() == "submit":
                phagescope_submit_action = action
                break
        if phagescope_submit_action is not None:
            for action in sorted_actions:
                if action is phagescope_submit_action:
                    continue
                action_name = ""
                if isinstance(action.parameters, dict):
                    action_name = str(action.parameters.get("action") or "").strip().lower()
                dropped_phagescope_actions.append(action_name or f"{action.kind}:{action.name}")

    job_type_to_use = "phagescope_track" if phagescope_submit_action else "chat_action"
    mode_to_use = "phagescope_track" if phagescope_submit_action else (record.get("mode") or "assistant")

    job_plan_id = plan_session.plan_id
    job_metadata = {
        "session_id": record.get("session_id"),
        "mode": mode_to_use,
        "user_message": record.get("user_message"),
    }
    job_params = {
        key: value
        for key, value in {
            "mode": mode_to_use,
            "session_id": record.get("session_id"),
            "plan_id": job_plan_id,
        }.items()
        if value is not None
    }

    try:
        job = plan_decomposition_jobs.create_job(
            plan_id=job_plan_id,
            task_id=None,
            mode=mode_to_use,
            job_type=job_type_to_use,
            params=job_params,
            metadata=job_metadata,
            job_id=run_id,
        )
    except ValueError:
        job = plan_decomposition_jobs.get_job(run_id)
        if job is None:
            job = plan_decomposition_jobs.create_job(
                plan_id=job_plan_id,
                task_id=None,
                mode=mode_to_use,
                job_type=job_type_to_use,
                params=job_params,
                metadata=job_metadata,
            )

    job_token = set_current_job(job.job_id)
    try:
        if job_plan_id is not None:
            plan_decomposition_jobs.attach_plan(job.job_id, job_plan_id)

        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "Background action enqueued and awaiting execution.",
            {
                "session_id": record.get("session_id"),
                "plan_id": job_plan_id,
                "mode": mode_to_use,
            },
        )

        context = dict(record.get("context") or {})
        history = record.get("history") or []
        provider_in_context = _normalize_search_provider(
            context.get("default_search_provider")
        )
        if provider_in_context:
            context["default_search_provider"] = provider_in_context
        elif record.get("session_id"):
            session_defaults = _get_session_settings(record["session_id"])
            fallback_provider = session_defaults.get("default_search_provider")
            if fallback_provider:
                context["default_search_provider"] = fallback_provider
        base_model_in_context = _normalize_base_model(
            context.get("default_base_model")
        )
        if base_model_in_context:
            context["default_base_model"] = base_model_in_context
        elif record.get("session_id"):
            session_defaults = _get_session_settings(record["session_id"])
            fallback_base_model = session_defaults.get("default_base_model")
            if fallback_base_model:
                context["default_base_model"] = fallback_base_model
        llm_provider_in_context = _normalize_llm_provider(
            context.get("default_llm_provider")
        )
        if llm_provider_in_context:
            context["default_llm_provider"] = llm_provider_in_context
        elif record.get("session_id"):
            session_defaults = _get_session_settings(record["session_id"])
            fallback_llm_provider = session_defaults.get("default_llm_provider")
            if fallback_llm_provider:
                context["default_llm_provider"] = fallback_llm_provider

        # Task-state sync: prioritize task_id from context.
        if "task_id" in context and "current_task_id" not in context:
            context["current_task_id"] = context["task_id"]
            logger.info(
                "[CHAT][ASYNC][TASK_SYNC] Using task_id from context: %s",
                context["current_task_id"],
            )
        # If current_task_id is absent in context, try loading from session.
        if "current_task_id" not in context and record.get("session_id"):
            current_task_id = _get_session_current_task(record["session_id"])
            if current_task_id is not None:
                context["current_task_id"] = current_task_id
                logger.info(
                    "[CHAT][ASYNC][TASK_SYNC] Using current_task_id from session: %s",
                    current_task_id,
                )

        # Special path: PhageScope submit-only tasks run as a background tracker job.
        if phagescope_submit_action is not None:
            plan_decomposition_jobs.mark_running(job.job_id)
            submit_params = dict(phagescope_submit_action.parameters or {})
            submit_params, backfilled_keys = _backfill_phagescope_submit_params(
                submit_params,
                sorted_actions=sorted_actions,
                submit_action=phagescope_submit_action,
                user_message=record.get("user_message"),
            )
            submit_params.setdefault("timeout", 120.0)
            submit_params.setdefault("poll_interval", 30.0)
            submit_params.setdefault("poll_timeout", 172800.0)

            plan_decomposition_jobs.append_log(
                job.job_id,
                "info",
                "Submitting PhageScope remote task.",
                {"parameters": {k: v for k, v in submit_params.items() if k != "token"}},
            )
            if backfilled_keys:
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "info",
                    "Backfilled missing PhageScope submit parameters from context.",
                    {"keys": backfilled_keys},
                )
            if dropped_phagescope_actions:
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "info",
                    "PhageScope submit-only mode enabled; skipped follow-up actions in this turn.",
                    {"skipped_actions": dropped_phagescope_actions},
                )
            # Default userid so LLM doesn't need to know a specific value.
            if _is_empty_phagescope_param(submit_params.get("userid")):
                submit_params["userid"] = "agent_default_user"
            missing_fields: List[str] = []
            if _is_empty_phagescope_param(submit_params.get("modulelist")):
                missing_fields.append("modulelist")
            has_input_source = any(
                not _is_empty_phagescope_param(submit_params.get(key))
                for key in ("phageid", "phageids", "sequence", "file_path", "sequence_ids")
            )
            if not has_input_source:
                missing_fields.append("phageid/phageids/sequence/file_path")
            if missing_fields:
                msg = "PhageScope submit missing required params: " + ", ".join(missing_fields)
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    msg,
                    {"missing_fields": missing_fields},
                )
                plan_decomposition_jobs.mark_failure(job.job_id, msg)
                update_action_run(run_id, status="failed", errors=[msg])
                return
            try:
                submit_result = await execute_tool("phagescope", **submit_params)
            except Exception as exc:  # pragma: no cover - defensive
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    "PhageScope submit failed.",
                    {"error": str(exc)},
                )
                plan_decomposition_jobs.mark_failure(job.job_id, str(exc))
                update_action_run(run_id, status="failed", errors=[str(exc)])
                return

            taskid = _extract_taskid_from_result(submit_result)
            if not taskid:
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    "PhageScope submit returned no taskid.",
                    {"result": submit_result},
                )
                plan_decomposition_jobs.mark_failure(job.job_id, "phagescope submit returned no taskid")
                update_action_run(run_id, status="failed", errors=["phagescope submit returned no taskid"])
                return

            module_items = _normalize_modulelist_value(submit_params.get("modulelist"))
            task_snapshot: Dict[str, Any] = {}
            try:
                detail_result = await execute_tool(
                    "phagescope",
                    action="task_detail",
                    taskid=str(taskid),
                    base_url=submit_params.get("base_url"),
                    timeout=min(float(submit_params.get("timeout") or 60.0), 40.0),
                )
                task_snapshot = _extract_phagescope_task_snapshot(detail_result)
            except Exception as exc:  # pragma: no cover - best-effort
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "warning",
                    "Initial PhageScope task_detail probe failed; tracker will continue polling.",
                    {"remote_taskid": str(taskid), "error": str(exc)},
                )

            counts_from_snapshot = (
                task_snapshot.get("counts")
                if isinstance(task_snapshot.get("counts"), dict)
                else {}
            )
            done_count = (
                counts_from_snapshot.get("done")
                if isinstance(counts_from_snapshot.get("done"), int)
                else 0
            )
            total_count_raw = (
                counts_from_snapshot.get("total")
                if isinstance(counts_from_snapshot.get("total"), int)
                else None
            )
            total_count = total_count_raw if total_count_raw and total_count_raw > 0 else (
                len(module_items) if module_items else None
            )
            percent = 0
            if isinstance(total_count, int) and total_count > 0:
                percent = int(round((done_count / max(1, total_count)) * 100))
                percent = max(0, min(100, percent))
            remote_status = (
                str(task_snapshot.get("remote_status") or "").strip()
                or str(task_snapshot.get("task_status") or "").strip()
                or "submitted"
            )

            progress_payload: Dict[str, Any] = {
                "tool": "phagescope",
                "taskid": str(taskid),
                "percent": percent,
                "status": remote_status,
                "phase": "submitted",
            }
            if isinstance(total_count, int) and total_count > 0:
                progress_payload["counts"] = {
                    "done": done_count,
                    "total": total_count,
                }

            plan_decomposition_jobs.update_stats(
                job.job_id,
                {
                    "tool_progress": progress_payload
                },
            )

            summary_text = _build_phagescope_submit_background_summary(
                taskid=str(taskid),
                background_job_id=job.job_id,
                module_items=module_items,
                snapshot=task_snapshot,
            )

            plan_decomposition_jobs.append_log(
                job.job_id,
                "info",
                "PhageScope task submitted; tracking in background.",
                {
                    "remote_taskid": str(taskid),
                    "modulelist": module_items,
                    "status_snapshot": task_snapshot or None,
                },
            )

            update_action_run(
                run_id,
                result={
                    "tracking_id": run_id,
                    "execution_mode": "phagescope_track",
                    "final_summary": summary_text,
                    "completed_now": [
                        {"tool": "phagescope", "action": "submit", "taskid": str(taskid)}
                    ],
                    "background_running": [
                        {
                            "tool": "phagescope",
                            "taskid": str(taskid),
                            "backend_job_id": job.job_id,
                            "status": remote_status,
                            "modulelist": module_items,
                            "counts": progress_payload.get("counts"),
                        }
                    ],
                    "phagescope": {
                        "taskid": str(taskid),
                        "backend_job_id": job.job_id,
                        "status": remote_status,
                        "modulelist": module_items,
                        "counts": progress_payload.get("counts"),
                    },
                },
                errors=[],
            )

            # Persist taskid into session metadata for later lookup.
            if record.get("session_id"):
                try:
                    _record_phagescope_task_memory(record["session_id"], submit_params, submit_result)
                except Exception:
                    pass

            # Update the assistant message so the user immediately sees taskid.
            if record.get("session_id"):
                try:
                    _update_message_content_by_tracking(
                        record.get("session_id"),
                        run_id,
                        summary_text,
                    )
                    _update_message_metadata_by_tracking(
                        record.get("session_id"),
                        run_id,
                        lambda existing: {
                            **(existing or {}),
                            "phagescope_taskid": str(taskid),
                            "phagescope_modulelist": module_items,
                            "phagescope_remote_status": remote_status,
                            "phagescope_counts": progress_payload.get("counts"),
                            "phagescope_submit_only": True,
                            "job_type": "phagescope_track",
                        },
                    )
                except Exception:
                    pass

            # Start polling tracker (no auto save_all; user will request later).
            _poll_interval = float(submit_params.get("poll_interval") or 30.0)
            _poll_timeout = float(submit_params.get("poll_timeout") or 172800.0)
            start_phagescope_track_job_thread(
                job_id=job.job_id,
                remote_taskid=str(taskid),
                modulelist=module_items,
                base_url=submit_params.get("base_url"),
                token=None,  # avoid persisting secrets; usually unused for PhageScope
                poll_interval=_poll_interval,
                poll_timeout=_poll_timeout,
                request_timeout=40.0,
            )
            # Persist tracking info to DB so the polling thread can be restarted
            # on server restart without losing track of the remote task.
            try:
                record_phagescope_tracking(
                    job_id=job.job_id,
                    session_id=record.get("session_id") or "",
                    plan_id=job_plan_id,
                    remote_taskid=str(taskid),
                    modulelist=module_items,
                    poll_interval=_poll_interval,
                    poll_timeout=_poll_timeout,
                )
            except Exception as _rec_exc:
                logger.warning("Failed to persist phagescope tracking record: %s", _rec_exc)
            # Do not finalize the job here; the tracker thread will mark_success/mark_failure.
            return

        agent = structured_chat_agent_cls(
            mode=record.get("mode"),
            plan_session=plan_session,
            plan_decomposer=plan_decomposer_service,
            plan_executor=plan_executor_service,
            session_id=record.get("session_id"),
            conversation_id=_derive_conversation_id(record.get("session_id")),
            history=history,
            extra_context=context,
        )

        plan_decomposition_jobs.mark_running(job.job_id)
        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "Starting structured action execution.",
            {
                "action_total": len(sorted_actions),
                "first_action": primary_action.name if primary_action else None,
            },
        )

        try:
            result = await agent.execute_structured(structured)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Action run %s failed during execution: %s", run_id, exc)
            plan_decomposition_jobs.append_log(
                job.job_id,
                "error",
                "An exception occurred during execution.",
                {"error": str(exc)},
            )
            current_plan_id = plan_session.plan_id
            if current_plan_id is not None:
                plan_decomposition_jobs.attach_plan(job.job_id, current_plan_id)
            plan_decomposition_jobs.mark_failure(job.job_id, str(exc))
            update_action_run(run_id, status="failed", errors=[str(exc)])
            logger.info(
                "[CHAT][ASYNC][DONE] tracking=%s status=failed errors=%s",
                run_id,
                exc,
            )
            return

        result_dict = result.model_dump()
        effective_success = bool(result.success)
        effective_errors = list(result.errors or [])
        deep_think_retry_payload: Optional[Dict[str, Any]] = None
        blocking_failures = _extract_blocking_failures(result.steps)
        if (
            not effective_success
            and blocking_failures
            and _auto_deep_think_retry_enabled(context)
        ):
            plan_decomposition_jobs.append_log(
                job.job_id,
                "warning",
                "Blocking action failed; escalating to one DeepThink retry attempt.",
                {
                    "tracking_id": run_id,
                    "blocking_failures": blocking_failures,
                },
            )
            try:
                deep_think_retry_payload = await _run_blocking_failure_deep_think_retry_once(
                    agent=agent,
                    run_id=run_id,
                    user_message=str(record.get("user_message") or ""),
                    context=context,
                    failures=blocking_failures,
                )
            except Exception as retry_exc:  # pragma: no cover - defensive
                deep_think_retry_payload = {
                    "attempted": True,
                    "success": False,
                    "error": str(retry_exc),
                }

            result_dict["deep_think_retry"] = deep_think_retry_payload
            result_dict["blocking_failures"] = blocking_failures
            result_dict["initial_result_success"] = bool(result.success)
            if deep_think_retry_payload.get("success"):
                effective_success = True
                effective_errors = []
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "info",
                    "DeepThink retry recovered the failed blocking execution path.",
                    {
                        "tracking_id": run_id,
                        "iterations": deep_think_retry_payload.get("iterations"),
                        "tools_used": deep_think_retry_payload.get("tools_used"),
                    },
                )
            else:
                retry_error = str(deep_think_retry_payload.get("error") or "").strip()
                if retry_error:
                    effective_errors.append(f"DeepThink retry failed: {retry_error}")
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    "DeepThink retry did not recover the failed blocking execution path.",
                    {
                        "tracking_id": run_id,
                        "error": retry_error or None,
                    },
                )

        status = "completed" if effective_success else "failed"
        result_dict["success"] = effective_success
        result_dict["errors"] = effective_errors
        tool_results_payload: List[Dict[str, Any]] = []

        # Diagnostic logging: record all steps.
        logger.info(
            "[CHAT][TOOL_RESULTS] session=%s tracking=%s total_steps=%d success=%s effective_success=%s",
            record.get("session_id"),
            run_id,
            len(result.steps),
            result.success,
            effective_success,
        )

        for step in result.steps:
            logger.info(
                "[CHAT][TOOL_RESULTS] session=%s tracking=%s step_kind=%s step_name=%s step_success=%s",
                record.get("session_id"),
                run_id,
                step.action.kind,
                step.action.name,
                step.success,
            )

            # Only collect actual tool_operation results for analysis.
            # task_operation / plan_operation steps (rerun_task, decompose_task, etc.)
            # return scheduling/status objects, not meaningful tool output — including
            # them causes _generate_tool_analysis to produce "no data" responses that
            # overwrite the LLM's original (often correct) text reply.
            if step.action.kind != "tool_operation":
                continue
            details = step.details or {}
            result_payload = details.get("result")

            # Diagnostic logging: record result payload type.
            logger.info(
                "[CHAT][TOOL_RESULTS] session=%s tracking=%s tool=%s result_type=%s has_result=%s",
                record.get("session_id"),
                run_id,
                step.action.name,
                type(result_payload).__name__,
                result_payload is not None,
            )

            if isinstance(result_payload, dict):
                tool_results_payload.append({
                    "name": step.action.name,
                    "summary": details.get("summary"),
                    "parameters": details.get("parameters"),
                    "result": result_payload,
                })
            elif result_payload is not None:
                # Non-dict result from a tool_operation — wrap it so downstream code
                # can still process it, but only if there is actual content.
                logger.warning(
                    "[CHAT][TOOL_RESULTS] session=%s tracking=%s tool=%s result is not dict, wrapping it",
                    record.get("session_id"),
                    run_id,
                    step.action.name,
                )
                tool_results_payload.append({
                    "name": step.action.name,
                    "summary": details.get("summary"),
                    "parameters": details.get("parameters"),
                    "result": {"output": str(result_payload)},
                })

        if tool_results_payload:
            result_dict["tool_results"] = tool_results_payload
            logger.info(
                "[CHAT][TOOL_RESULTS] session=%s tracking=%s collected %d tool results",
                record.get("session_id"),
                run_id,
                len(tool_results_payload),
            )

        # Agent loop: generate detailed analysis + process summary.
        analysis_text: Optional[str] = None
        summary_text: Optional[str] = None
        llm_provider = _normalize_llm_provider(
            (context or {}).get("default_llm_provider")
        )
        if deep_think_retry_payload and deep_think_retry_payload.get("success"):
            deep_think_answer = str(deep_think_retry_payload.get("final_answer") or "").strip()
            if deep_think_answer:
                analysis_text = deep_think_answer
                result_dict["analysis_source"] = "deep_think_retry"
        if effective_success and tool_results_payload and not analysis_text:
            logger.info(
                "[CHAT][SUMMARY] session=%s tracking=%s Starting analysis generation...",
                record.get("session_id"),
                run_id,
            )
            try:
                analysis_text = await _generate_tool_analysis(
                    user_message=record.get("user_message", ""),
                    tool_results=tool_results_payload,
                    session_id=record.get("session_id"),
                    llm_provider=llm_provider,
                )
            except Exception as exc:
                logger.error(
                    "[CHAT][SUMMARY] session=%s tracking=%s Failed to generate analysis: %s",
                    record.get("session_id"),
                    run_id,
                    exc,
                    exc_info=True,
                )
        if not analysis_text:
            analysis_text = await _generate_action_analysis(
                record.get("user_message", ""),
                result.steps,
                record.get("session_id"),
                llm_provider,
            )
        if not analysis_text:
            analysis_text = result.reply or result.summarize_steps()

        summary_text = _build_brief_action_summary(result.steps) or result.summarize_steps()

        if analysis_text:
            result_dict["analysis_text"] = analysis_text
        if summary_text:
            result_dict["final_summary"] = summary_text
            try:
                result.final_summary = summary_text
            except Exception:
                pass
        content_for_message = analysis_text or summary_text
        if content_for_message:
            _update_message_content_by_tracking(
                record.get("session_id"),
                run_id,
                content_for_message,
            )
            logger.info(
                "[CHAT][SUMMARY] session=%s tracking=%s Analysis saved: %s",
                record.get("session_id"),
                run_id,
                content_for_message[:100] if len(content_for_message) > 100 else content_for_message,
            )

        # Update to completed only now so the frontend sees the summary on next poll.
        update_kwargs: Dict[str, Any] = {
            "status": status,
            "result": result_dict,
            "errors": effective_errors,
        }
        if result.bound_plan_id is not None:
            update_kwargs["plan_id"] = result.bound_plan_id

        logger.info(
            "[CHAT][SUMMARY] session=%s tracking=%s Updating action status to %s",
            record.get("session_id"),
            run_id,
            status,
        )
        update_action_run(run_id, **update_kwargs)

        job_snapshot = plan_decomposition_jobs.get_job_payload(job.job_id)

        _update_message_metadata_by_tracking(
            record.get("session_id"),
            run_id,
            lambda existing: _merge_async_metadata(
                existing,
                status=status,
                tracking_id=run_id,
                plan_id=result.bound_plan_id,
                actions=[step.action_payload for step in result.steps],
                actions_summary=result.actions_summary,
                tool_results=tool_results_payload,
                errors=effective_errors,
                job_id=job.job_id,
                job_payload=job_snapshot,
                job_type=getattr(job, "job_type", None),
                final_summary=summary_text,
                analysis_text=analysis_text,
            ),
        )

        if record.get("session_id"):
            _set_session_plan_id(record["session_id"], result.bound_plan_id)

        final_plan_id = result.bound_plan_id or plan_session.plan_id
        if final_plan_id is not None:
            plan_decomposition_jobs.attach_plan(job.job_id, final_plan_id)

        stats_payload = {
            "step_count": len(result.steps),
            "success": effective_success,
            "error_count": len(effective_errors),
        }

        if effective_success:
            plan_decomposition_jobs.append_log(
                job.job_id,
                "info",
                "Structured action execution completed.",
                stats_payload,
            )
            plan_decomposition_jobs.mark_success(
                job.job_id,
                result=result_dict,
                stats=stats_payload,
            )
        else:
            error_message = effective_errors[0] if effective_errors else "Some actions failed"
            plan_decomposition_jobs.append_log(
                job.job_id,
                "error",
                "Structured actions finished with failures in some steps.",
                {**stats_payload, "errors": effective_errors},
            )
            plan_decomposition_jobs.mark_failure(
                job.job_id,
                error_message,
                result=result_dict,
                stats=stats_payload,
            )

        logger.info(
            "[CHAT][ASYNC][DONE] tracking=%s status=%s plan=%s errors=%s",
            run_id,
            status,
            result.bound_plan_id,
            effective_errors,
        )
    finally:
        reset_current_job(job_token)


async def get_action_status(tracking_id: str):
    """Query background action execution status."""
    record = fetch_action_run(tracking_id)
    if not record:
        raise HTTPException(status_code=404, detail="Action run not found")

    actions, tool_results = _build_action_status_payloads(record)

    result_data = record.get("result") or {}
    if not isinstance(result_data, dict):
        result_data = {}

    job_snapshot = plan_decomposition_jobs.get_job_payload(tracking_id, include_logs=False)
    if isinstance(job_snapshot, dict):
        stats_payload = job_snapshot.get("stats")
        if isinstance(stats_payload, dict):
            progress = stats_payload.get("tool_progress")
            if isinstance(progress, dict):
                result_data = dict(result_data)
                result_data["tool_progress"] = progress
                if str(progress.get("tool") or "").strip().lower() == "phagescope":
                    phage_result = (
                        dict(result_data.get("phagescope"))
                        if isinstance(result_data.get("phagescope"), dict)
                        else {}
                    )
                    taskid = progress.get("taskid")
                    if taskid is not None:
                        phage_result["taskid"] = str(taskid)
                    status_text = progress.get("status")
                    if isinstance(status_text, str) and status_text.strip():
                        phage_result["status"] = status_text.strip()
                    counts = progress.get("counts")
                    if isinstance(counts, dict):
                        phage_result["counts"] = counts
                    if phage_result:
                        result_data["phagescope"] = phage_result
                    if "background_running" not in result_data:
                        result_data["background_running"] = [
                            {
                                "tool": "phagescope",
                                "taskid": phage_result.get("taskid"),
                                "backend_job_id": tracking_id,
                                "status": phage_result.get("status"),
                                "counts": phage_result.get("counts"),
                            }
                        ]

    final_summary = result_data.get("final_summary")
    if not final_summary and isinstance(job_snapshot, dict):
        stats_payload = job_snapshot.get("stats")
        if isinstance(stats_payload, dict):
            progress = stats_payload.get("tool_progress")
            if (
                isinstance(progress, dict)
                and str(progress.get("tool") or "").strip().lower() == "phagescope"
                and progress.get("taskid") is not None
            ):
                snapshot_payload: Dict[str, Any] = {
                    "remote_status": progress.get("status"),
                }
                if isinstance(progress.get("counts"), dict):
                    snapshot_payload["counts"] = progress.get("counts")
                final_summary = _build_phagescope_submit_background_summary(
                    taskid=str(progress.get("taskid")),
                    background_job_id=tracking_id,
                    module_items=None,
                    snapshot=snapshot_payload,
                )
                result_data["final_summary"] = final_summary

    metadata = {}
    if tool_results:
        metadata["tool_results"] = tool_results
    if final_summary:
        metadata["final_summary"] = final_summary
    if isinstance(job_snapshot, dict):
        metadata["job"] = job_snapshot
        stats_payload = job_snapshot.get("stats")
        if isinstance(stats_payload, dict) and isinstance(
            stats_payload.get("tool_progress"), dict
        ):
            metadata["tool_progress"] = stats_payload.get("tool_progress")

    return ActionStatusResponse(
        tracking_id=tracking_id,
        status=record["status"],
        plan_id=record.get("plan_id"),
        actions=actions,
        result=result_data or record.get("result"),
        errors=record.get("errors"),
        created_at=record.get("created_at"),
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        metadata=metadata if metadata else None,
    )


async def retry_action_run(tracking_id: str, background_tasks: BackgroundTasks):
    """Retry a previous action run by cloning its structured actions."""
    original = fetch_action_run(tracking_id)
    if not original:
        raise HTTPException(status_code=404, detail="Action run not found")

    try:
        structured = LLMStructuredResponse.model_validate_json(original["structured_json"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid structured payload: {exc}")

    new_tracking = f"act_{uuid4().hex}"

    plan_session = PlanSession(repo=plan_repository, plan_id=original.get("plan_id"))
    try:
        plan_session.refresh()
    except ValueError:
        plan_session.detach()

    context = original.get("context") or {}
    history = original.get("history") or []

    create_action_run(
        run_id=new_tracking,
        session_id=original.get("session_id"),
        user_message=original.get("user_message", ""),
        mode=original.get("mode"),
        plan_id=plan_session.plan_id,
        context=context,
        history=history,
        structured_json=original["structured_json"],
    )

    job_metadata = {
        "session_id": original.get("session_id"),
        "mode": original.get("mode"),
        "user_message": original.get("user_message"),
    }
    job_params = {
        key: value
        for key, value in {
            "mode": original.get("mode"),
            "session_id": original.get("session_id"),
            "plan_id": plan_session.plan_id,
        }.items()
        if value is not None
    }

    try:
        plan_decomposition_jobs.create_job(
            plan_id=plan_session.plan_id,
            task_id=None,
            mode=original.get("mode") or "assistant",
            job_type="chat_action",
            params=job_params,
            metadata=job_metadata,
            job_id=new_tracking,
        )
    except ValueError:
        pass

    pending_actions = [
        {
            "kind": action.kind,
            "name": action.name,
            "parameters": action.parameters,
            "order": action.order,
            "blocking": action.blocking,
            "status": "pending",
            "success": None,
            "message": None,
            "details": None,
        }
        for action in structured.sorted_actions()
    ]

    for action in structured.sorted_actions():
        try:
            append_action_log_entry(
                plan_id=plan_session.plan_id,
                job_id=new_tracking,
                job_type="chat_action",
                sequence=action.order if isinstance(action.order, int) else None,
                session_id=original.get("session_id"),
                user_message=original.get("user_message", ""),
                action_kind=action.kind,
                action_name=action.name or "",
                status="queued",
                success=None,
                message="Action queued for execution (retry).",
                parameters=action.parameters,
                details=None,
            )
        except Exception:
            logger.debug("Failed to persist queued action log on retry", exc_info=True)

    background_tasks.add_task(_execute_action_run, new_tracking)

    return ActionStatusResponse(
        tracking_id=new_tracking,
        status="pending",
        plan_id=plan_session.plan_id,
        actions=pending_actions,
        result=None,
        errors=None,
        created_at=None,
        started_at=None,
        finished_at=None,
        metadata={"retry_of": tracking_id},
    )


def _build_action_status_payloads(
    record: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Build action payload list based on stored structured/result data."""
    result = record.get("result") or {}
    if isinstance(result, dict):
        completed_now = result.get("completed_now")
        background_running = result.get("background_running")
        payloads: List[Dict[str, Any]] = []
        if isinstance(completed_now, list):
            for item in completed_now:
                if not isinstance(item, dict):
                    continue
                payloads.append(
                    {
                        "kind": "tool_operation",
                        "name": item.get("tool"),
                        "parameters": {
                            "action": item.get("action"),
                            "taskid": item.get("taskid"),
                        },
                        "order": None,
                        "blocking": False,
                        "status": "completed",
                        "success": True,
                        "message": "completed_now",
                        "details": item,
                    }
                )
        if isinstance(background_running, list):
            for item in background_running:
                if not isinstance(item, dict):
                    continue
                payloads.append(
                    {
                        "kind": "tool_operation",
                        "name": item.get("tool"),
                        "parameters": {
                            "action": "task_detail",
                            "taskid": item.get("taskid"),
                        },
                        "order": None,
                        "blocking": False,
                        "status": "running",
                        "success": None,
                        "message": "background_running",
                        "details": item,
                    }
                )
        if payloads:
            return payloads, None

    steps = result.get("steps") or []
    tool_results: List[Dict[str, Any]] = []
    if steps:
        payloads: List[Dict[str, Any]] = []
        for step in steps:
            action = step.get("action") or {}
            details = step.get("details") or {}
            if isinstance(details, dict) and isinstance(details.get("result"), dict):
                tool_results.append(details["result"])
            payloads.append(
                {
                    "kind": action.get("kind"),
                    "name": action.get("name"),
                    "parameters": action.get("parameters"),
                    "order": action.get("order"),
                    "blocking": action.get("blocking"),
                    "status": "completed" if step.get("success") else "failed",
                    "success": step.get("success"),
                    "message": step.get("message"),
                    "details": details,
                }
            )
        return payloads, (tool_results or None)

    try:
        structured = LLMStructuredResponse.model_validate_json(record["structured_json"])
    except Exception:  # pragma: no cover - defensive
        return [], None

    payloads = [
        {
            "kind": action.kind,
            "name": action.name,
            "parameters": action.parameters,
            "order": action.order,
            "blocking": action.blocking,
            "status": record.get("status", "pending"),
            "success": None,
        }
        for action in structured.sorted_actions()
    ]
    return payloads, None
