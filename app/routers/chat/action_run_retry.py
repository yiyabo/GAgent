"""Blocking-failure auto DeepThink retry cluster of ``action_execution``.

Moved out of ``action_execution.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (execution cluster ②):
the ``CHAT_AUTO_DEEP_THINK_RETRY_*`` env knobs and tool allow-list, the small
value-coercion utilities the retry path shares with the facade
(``_truthy``/``_parse_int``/``_clip_text``), blocking-failure extraction and the
retry gate, the retry prompt builder and
``_run_blocking_failure_deep_think_retry_once`` itself.

Patch surface (the reason for the one deviation):
``_run_blocking_failure_deep_think_retry_once`` is patched **on the
action_execution namespace** at 12 sites
(app/tests/chat/test_action_execution_auto_deep_think_retry.py,
test_cascade_auto_continue.py); its only caller is ``_execute_action_run``,
which stays in the facade and therefore still reads the facade binding at call
time — so those patches keep working with no late binding.  ``DeepThinkAgent``
is read through ``_ae()`` at call time (one added line) so the same
namespace-patch semantics hold if it is ever patched there; the pre-existing
``compat_chat_routes.DeepThinkAgent`` dynamic lookup below is untouched.

No logger is used in this cluster; every message, tool list, env default and
retry parameter (max_iterations 12, tool_timeout 120, THINKING_BUDGET 10000) is
unchanged.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any, Dict, List

from app.services.llm.structured_response import LLMAction

_AUTO_DEEP_THINK_RETRY_ENV = "CHAT_AUTO_DEEP_THINK_RETRY_ON_BLOCKING_FAILURE"
_AUTO_DEEP_THINK_RETRY_MAX_ITER_ENV = "CHAT_AUTO_DEEP_THINK_RETRY_MAX_ITERATIONS"
_AUTO_DEEP_THINK_RETRY_TOOL_TIMEOUT_ENV = "CHAT_AUTO_DEEP_THINK_RETRY_TOOL_TIMEOUT"
_AUTO_DEEP_THINK_RETRY_CONTEXT_KEY = "auto_deep_think_retry_on_blocking_failure"
_AUTO_DEEP_THINK_RETRY_AVAILABLE_TOOLS: List[str] = [
    "web_search",
    "lightrag_query",
    "graph_rag",
    "sequence_fetch",
    "url_fetch",
    "scientific_figure_generator",
    "code_executor",
    "file_operations",
    "document_reader",
    "vision_reader",
    "bio_tools",
    "phagescope",
    "result_interpreter",
    "terminal_session",
]


def _ae() -> Any:
    """Late-bound action_execution facade module (monkeypatch-friendly lookups)."""
    from . import action_execution

    return action_execution


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


_AUTO_DEEP_THINK_RETRY_BLOCKED_TOOLS = {
    "review_pack_writer",
    "manuscript_writer",
    "literature_pipeline",
}
_AUTO_DEEP_THINK_RETRY_BLOCKED_ERROR_CODES = {
    "section_evaluation_failed",
    "citation_validation_failed",
    "polish_quality_gate_failed",
}


def _should_attempt_blocking_failure_retry(steps: List[Any], context: Dict[str, Any]) -> bool:
    if not _auto_deep_think_retry_enabled(context):
        return False

    for step in steps or []:
        action = getattr(step, "action", None)
        if action is None or not bool(getattr(action, "blocking", False)):
            continue
        if bool(getattr(step, "success", False)):
            continue

        if (
            getattr(action, "kind", None) == "plan_operation"
            and str(getattr(action, "name", "") or "").strip().lower() == "review_plan"
        ):
            return False

        action_name = str(getattr(action, "name", "") or "").strip().lower()
        if getattr(action, "kind", None) == "tool_operation" and action_name in _AUTO_DEEP_THINK_RETRY_BLOCKED_TOOLS:
            return False

        details = step.details if isinstance(step.details, dict) else {}
        result_payload = details.get("result")
        if not isinstance(result_payload, dict):
            continue

        error_code = str(result_payload.get("error_code") or result_payload.get("error") or "").strip().lower()
        if error_code in _AUTO_DEEP_THINK_RETRY_BLOCKED_ERROR_CODES:
            return False
        if result_payload.get("public_release_ready") is False:
            return False
        if str(result_payload.get("release_state") or "").strip().lower() == "blocked":
            return False
        if result_payload.get("partial") or result_payload.get("partial_output_path"):
            return False

        draft_payload = result_payload.get("draft")
        if isinstance(draft_payload, dict):
            if draft_payload.get("public_release_ready") is False:
                return False
            if str(draft_payload.get("release_state") or "").strip().lower() == "blocked":
                return False
            if draft_payload.get("quality_gate_passed") is False:
                return False
            failed_sections = draft_payload.get("failed_sections")
            if isinstance(failed_sections, list) and failed_sections:
                return False

    return True


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
        try:
            from app.llm import update_usage_context
            update_usage_context(tool_name=str(name), phase="execution", call_purpose="tool_execution")
        except Exception:
            pass
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

    dt_agent_cls = _ae().DeepThinkAgent
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
        enable_thinking=True,
        thinking_budget=int(os.getenv("THINKING_BUDGET", "10000")),
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
