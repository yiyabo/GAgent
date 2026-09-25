"""Response-metadata cluster of ``agent`` (W5a cluster ④).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
④ ``response_metadata.py``): the structured-plan / plan-evaluation / DeepThink
response metadata builders, the created-plan reuse payload, the chat-metadata
sanitizers and the rerun-task result extractor, plus the simple-chat thinking
process payload.  ``agent.py`` re-exports every name, so the class call sites and
the direct test imports (app/tests/chat/test_deep_think_history_persist.py imports
``_build_deep_think_response_metadata``, test_simple_chat_thinking_persist.py
imports ``_build_simple_chat_thinking_process``) are unchanged.

Patch surface (the reason for the one deviation): ``plan_decomposition_jobs`` is
patched **on the agent namespace** at 6 sites
(app/tests/chat/test_no_fallback_policy.py), so the single read in
``_structured_plan_metadata_from_result`` goes through ``_ag()`` at call time
instead of binding the module object by value.  ``W4PATH=app/routers/chat/agent.py
python /tmp/w4diff.py _structured_plan_metadata_from_result
app/routers/chat/response_metadata.py`` shows exactly that one line.  None of the
other names here is patched anywhere in ``app/`` or ``app/tests/``.

The remaining facade aliases this cluster read (``merge_artifact_gallery`` from
``artifact_gallery``, ``DeepThinkResult`` / ``build_user_visible_step`` from
``deep_think_agent``) are imported directly from those source modules, so every
call expression stays verbatim.

``_sanitize_chat_metadata_value`` also has a reader in the deterministic-execute
cluster, which imports it from here.  No logger is used in this cluster; every
metadata key, status literal and message template is byte-identical.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.deep_think_agent import DeepThinkResult, build_user_visible_step

from .artifact_gallery import merge_artifact_gallery


def _ag() -> Any:
    """Late-bound agent facade module (monkeypatch-friendly lookups)."""
    from . import agent

    return agent


def _build_simple_chat_thinking_process(reasoning_text: str) -> Optional[Dict[str, Any]]:
    text = (reasoning_text or "").strip()
    if not text:
        return None
    iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "status": "completed",
        "total_iterations": 1,
        "summary": text,
        "steps": [
            {
                "iteration": 1,
                "thought": "",
                "display_text": text,
                "kind": "summary",
                "action": None,
                "action_result": None,
                "status": "done",
                "timestamp": iso,
                "started_at": iso,
                "finished_at": iso,
                "self_correction": None,
            }
        ],
    }


def _structured_plan_metadata_from_result(
    result: DeepThinkResult,
    *,
    tool_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not result.structured_plan_required:
        return {}
    state = str(result.structured_plan_state or "").strip() or (
        "created" if result.structured_plan_satisfied else "text_only"
    )
    metadata: Dict[str, Any] = {
        "plan_creation_state": state,
    }
    message = str(result.structured_plan_message or "").strip()
    if message:
        metadata["plan_creation_message"] = message
    if isinstance(tool_results, list):
        for item in reversed(tool_results):
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool") or item.get("name") or "").strip().lower()
            if tool_name != "plan_operation":
                continue
            result_payload = item.get("result")
            if not isinstance(result_payload, dict):
                continue
            auto_review = result_payload.get("auto_review")
            if not isinstance(auto_review, dict):
                continue
            job_id = str(auto_review.get("decomposition_job_id") or "").strip()
            if not job_id:
                continue
            get_job_payload = getattr(_ag().plan_decomposition_jobs, "get_job_payload", None)
            job_payload = (
                get_job_payload(job_id, include_logs=False)
                if callable(get_job_payload)
                else None
            ) or {
                "job_id": job_id,
                "job_type": "plan_decompose",
                "status": "queued",
            }
            plan_id = result_payload.get("plan_id")
            if plan_id is not None and job_payload.get("plan_id") is None:
                job_payload["plan_id"] = plan_id
            metadata["decomposition_job"] = _sanitize_chat_metadata_value(job_payload)
            break
    return metadata


def _plan_runtime_metadata(plan_tree: Any) -> Dict[str, Any]:
    metadata = getattr(plan_tree, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    plan_evaluation = metadata.get("plan_evaluation")
    if isinstance(plan_evaluation, dict):
        return {"plan_evaluation": dict(plan_evaluation)}
    return {}


def _plan_evaluation_from_tool_results(
    tool_results: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(tool_results, list):
        return None
    for item in reversed(tool_results):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool") or item.get("name") or "").strip().lower()
        if tool_name != "plan_operation":
            continue
        result_payload = item.get("result")
        if not isinstance(result_payload, dict):
            continue
        operation = str(result_payload.get("operation") or "").strip().lower()
        if operation not in {"review", "optimize"}:
            continue
        rubric_score = result_payload.get("rubric_score")
        if operation == "optimize":
            rubric_score = result_payload.get("rubric_score_after", rubric_score)
        if not isinstance(rubric_score, (int, float)):
            continue
        evaluator = (
            result_payload.get("rubric_evaluator")
            if isinstance(result_payload.get("rubric_evaluator"), dict)
            else {}
        )
        if operation == "optimize" and isinstance(result_payload.get("rubric_evaluator_after"), dict):
            evaluator = result_payload.get("rubric_evaluator_after")

        dimension_scores_payload = result_payload.get("rubric_dimension_scores")
        if operation == "optimize" and isinstance(result_payload.get("rubric_dimension_scores_after"), dict):
            dimension_scores_payload = result_payload.get("rubric_dimension_scores_after")

        evaluation: Dict[str, Any] = {
            "plan_id": result_payload.get("plan_id"),
            "rubric_version": evaluator.get("rubric_version"),
            "evaluator_provider": evaluator.get("provider"),
            "evaluator_model": evaluator.get("model"),
            "evaluated_at": evaluator.get("evaluated_at"),
            "overall_score": float(rubric_score),
            "dimension_scores": (
                dict(dimension_scores_payload)
                if isinstance(dimension_scores_payload, dict)
                else {}
            ),
            "subcriteria_scores": (
                dict(result_payload.get("rubric_subcriteria_scores"))
                if isinstance(result_payload.get("rubric_subcriteria_scores"), dict)
                else {}
            ),
            "feedback": (
                dict(result_payload.get("rubric_feedback"))
                if isinstance(result_payload.get("rubric_feedback"), dict)
                else {}
            ),
        }
        rule_evidence = result_payload.get("rule_evidence")
        if isinstance(rule_evidence, dict):
            evaluation["rule_evidence"] = dict(rule_evidence)
        return {
            key: value
            for key, value in evaluation.items()
            if value not in (None, {}, [])
        }
    return None


def _build_deep_think_response_metadata(
    *,
    result: DeepThinkResult,
    routing_metadata: Dict[str, Any],
    plan_id: Optional[int],
    plan_title: Optional[str],
    reasoning_language: str,
    thinking_visible: bool,
    progress_visible: bool,
    artifact_gallery: Optional[List[Dict[str, Any]]] = None,
    tool_results: Optional[List[Dict[str, Any]]] = None,
    deep_think_job_id: Optional[str] = None,
    background_category: Optional[str] = None,
    display_text: Optional[str] = None,
    structured_plan_meta: Optional[Dict[str, Any]] = None,
    plan_runtime_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "plan_id": plan_id,
        "plan_title": plan_title,
        "deep_think": True,
        "iterations": result.total_iterations,
        "tools_used": result.tools_used,
        "confidence": result.confidence,
        "tool_failures": result.tool_failures,
        "search_verified": result.search_verified,
        "fallback_used": result.fallback_used,
        "status": "completed",
        "unified_stream": True,
        **routing_metadata,
        **(structured_plan_meta or {}),
        **(plan_runtime_meta or {}),
    }
    normalized_display_text = str(display_text or "").strip()
    if normalized_display_text:
        metadata["analysis_text"] = normalized_display_text
        metadata["final_summary"] = normalized_display_text
    metadata["thinking_display_mode"] = "final_answer"
    if artifact_gallery:
        metadata["artifact_gallery"] = merge_artifact_gallery(
            None,
            artifact_gallery,
        )
    if tool_results:
        metadata["tool_results"] = [
            _sanitize_chat_metadata_value(item)
            for item in tool_results
            if isinstance(item, dict)
        ]
        latest_plan_evaluation = _plan_evaluation_from_tool_results(tool_results)
        if isinstance(latest_plan_evaluation, dict) and latest_plan_evaluation:
            metadata["plan_evaluation"] = _sanitize_chat_metadata_value(
                latest_plan_evaluation
            )
    if thinking_visible or progress_visible:
        metadata["thinking_process"] = {
            "status": "completed",
            "total_iterations": result.total_iterations,
            "summary": result.thinking_summary,
            "steps": [
                {
                    **build_user_visible_step(
                        step,
                        language=reasoning_language,
                        preserve_thought=True,
                    ),
                    "status": "done" if step.status == "done" else "completed",
                }
                for step in result.thinking_steps
            ],
        }
    if background_category:
        metadata["background_category"] = background_category
    if deep_think_job_id:
        metadata["deep_think_job_id"] = deep_think_job_id
    return metadata


def _should_bind_created_plan(
    *,
    existing_plan_id: Optional[int],
    allow_new_plan_rebind: bool = False,
) -> bool:
    return existing_plan_id is None or allow_new_plan_rebind


def _build_existing_plan_create_result(
    *,
    existing_plan_id: int,
    plan_title: Optional[str] = None,
) -> Dict[str, Any]:
    title_suffix = f" ('{plan_title}')" if isinstance(plan_title, str) and plan_title.strip() else ""
    summary = (
        f"Structured plan already exists for this request: plan_id={existing_plan_id}{title_suffix}. "
        "Do not call plan_operation create again in this turn. "
        "Reuse the existing plan and submit the final answer."
    )
    payload: Dict[str, Any] = {
        "success": True,
        "operation": "create",
        "plan_id": existing_plan_id,
        "binding_skipped": True,
        "existing_plan_id": existing_plan_id,
        "already_bound_plan_reused": True,
        "message": summary,
        "summary": summary,
        "next_step_hint": (
            "Do not call plan_operation create again. Summarize the created plan "
            "and finish the turn."
        ),
    }
    if isinstance(plan_title, str) and plan_title.strip():
        payload["title"] = plan_title.strip()
    return payload


_SKIP_CHAT_METADATA_VALUE = object()


def _sanitize_chat_metadata_value(value: Any) -> Any:
    if callable(value):
        return _SKIP_CHAT_METADATA_VALUE
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key) == "tool_context":
                continue
            normalized = _sanitize_chat_metadata_value(item)
            if normalized is _SKIP_CHAT_METADATA_VALUE:
                continue
            sanitized[str(key)] = normalized
        return sanitized
    if isinstance(value, list):
        sanitized_list: List[Any] = []
        for item in value:
            normalized = _sanitize_chat_metadata_value(item)
            if normalized is _SKIP_CHAT_METADATA_VALUE:
                continue
            sanitized_list.append(normalized)
        return sanitized_list
    if isinstance(value, tuple):
        sanitized_tuple: List[Any] = []
        for item in value:
            normalized = _sanitize_chat_metadata_value(item)
            if normalized is _SKIP_CHAT_METADATA_VALUE:
                continue
            sanitized_tuple.append(normalized)
        return sanitized_tuple
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _sanitize_deep_think_tool_params(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    sanitized = _sanitize_chat_metadata_value(params)
    return sanitized if isinstance(sanitized, dict) else {}


def _extract_rerun_task_result_payload(step: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    details = step.details if hasattr(step, "details") and isinstance(step.details, dict) else {}

    result_payload = details.get("result")
    if isinstance(result_payload, dict):
        normalized_result = dict(result_payload)
    elif any(
        key in details
        for key in ("plan_id", "task_id", "status", "content", "metadata", "raw_response")
    ):
        normalized_result = {
            key: value
            for key, value in details.items()
            if key not in {"job", "attempt", "max_attempts", "retry_policy"}
        }
    else:
        normalized_result = {}

    execution_payload: Dict[str, Any] = {}
    raw_response = normalized_result.get("raw_response")
    if isinstance(raw_response, str) and raw_response.strip():
        try:
            parsed = json.loads(raw_response)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            execution_payload = parsed

    return normalized_result, execution_payload
