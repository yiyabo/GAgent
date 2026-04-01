"""Structured chat agent core orchestration logic."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from app.config.executor_config import get_executor_settings
from app.repository.chat_action_runs import create_action_run, fetch_action_run, update_action_run
from app.repository.plan_storage import append_action_log_entry, update_decomposition_job_status
from app.llm import LLMClient
from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX, get_settings
from app.services.llm.decomposer_service import PlanDecomposerLLMService
from app.services.llm.llm_service import LLMService, get_llm_service
from app.services.llm.structured_response import LLMAction, LLMStructuredResponse, schema_as_json
from app.services.plans.decomposition_jobs import (
    JobRuntimeController,
    get_current_job,
    log_job_event,
    plan_decomposition_jobs,
    reset_current_job,
    set_current_job,
    start_phagescope_track_job_thread,
)
from app.services.plans.plan_decomposer import DecompositionResult, PlanDecomposer
from app.services.plans.plan_executor import PlanExecutor, PlanExecutorLLMService
from app.services.plans.plan_models import PlanNode
from app.services.plans.plan_session import PlanSession
from app.services.plans.task_verification import TaskVerificationService
from app.services.session_title_service import SessionNotFoundError
from app.services.upload_storage import delete_session_storage
from app.services.deep_think_agent import (
    build_user_visible_step,
    detect_reasoning_language,
    DeepThinkAgent,
    ThinkingStep,
    DeepThinkResult,
    summarize_tool_step_display,
    summarize_simple_chat_reasoning,
)
from tool_box import execute_tool

_DEEP_THINK_MAX_ITER_DEFAULT = 64
_DEEP_THINK_MAX_ITER_CAP = 128


def _resolve_deep_think_max_iterations() -> int:
    """
    Each iteration is roughly one LLM turn (optionally plus tool execution).
    Override with env DEEP_THINK_MAX_ITERATIONS (1..128).
    """
    raw = (os.getenv("DEEP_THINK_MAX_ITERATIONS") or "").strip()
    if not raw:
        return _DEEP_THINK_MAX_ITER_DEFAULT
    try:
        parsed = int(raw, 10)
    except ValueError:
        return _DEEP_THINK_MAX_ITER_DEFAULT
    return max(1, min(parsed, _DEEP_THINK_MAX_ITER_CAP))


def _code_executor_job_stream_loggers(job_id: str) -> Tuple[Any, Any]:
    """Stdout/stderr hooks for code_executor when a plan decomposition job log stream is active."""

    async def on_stdout(line: str) -> None:
        plan_decomposition_jobs.append_log(job_id, "stdout", line, {})

    async def on_stderr(line: str) -> None:
        plan_decomposition_jobs.append_log(job_id, "stderr", line, {})

    return on_stdout, on_stderr


from .action_execution import (
    append_summary_to_reply as _append_summary_to_reply_fn,
    build_actions_summary as _build_actions_summary_fn,
    log_action_event as _log_action_event_fn,
    resolve_job_meta as _resolve_job_meta_fn,
    truncate_summary_text as _truncate_summary_text_fn,
)
from .artifact_gallery import (
    build_artifact_gallery_item,
    merge_artifact_gallery,
    update_recent_image_artifacts,
)
from .action_handlers import (
    handle_context_request as _handle_context_request_fn,
    handle_plan_action as _handle_plan_action_fn,
    handle_system_action as _handle_system_action_fn,
    handle_task_action as _handle_task_action_fn,
    handle_tool_action as _handle_tool_action_fn,
    handle_unknown_action as _handle_unknown_action_fn,
    maybe_synthesize_phagescope_saveall_analysis as _maybe_synthesize_phagescope_saveall_analysis_fn,
)
from .code_executor_helpers import (
    compose_code_executor_atomic_task_prompt as _compose_code_executor_atomic_task_prompt_fn,
    normalize_csv_arg as _normalize_csv_arg_fn,
    resolve_action_placeholders as _resolve_action_placeholders_fn,
    resolve_code_executor_task_context as _resolve_code_executor_task_context_fn,
    resolve_placeholders_in_value as _resolve_placeholders_in_value_fn,
    resolve_previous_path as _resolve_previous_path_fn,
    summarize_amem_experiences_for_cc as _summarize_amem_experiences_for_cc_fn,
)
from .guardrail_handlers import (
    apply_completion_claim_guardrail as _apply_completion_claim_guardrail_fn,
    apply_explicit_plan_review_guardrail as _apply_explicit_plan_review_guardrail_fn,
    apply_experiment_fallback as _apply_experiment_fallback_fn,
    apply_phagescope_fallback as _apply_phagescope_fallback_fn,
    apply_plan_first_guardrail as _apply_plan_first_guardrail_fn,
    apply_task_execution_followthrough_guardrail as _apply_task_execution_followthrough_guardrail_fn,
    first_executable_atomic_descendant as _first_executable_atomic_descendant_fn,
    infer_plan_seed_message as _infer_plan_seed_message_fn,
    match_atomic_task_by_keywords as _match_atomic_task_by_keywords_fn,
    resolve_followthrough_target_task_id as _resolve_followthrough_target_task_id_fn,
)
from .guardrails import (
    explicit_manuscript_request as _explicit_manuscript_request_fn,
    extract_declared_absolute_paths as _extract_declared_absolute_paths_fn,
    extract_task_id_from_text as _extract_task_id_from_text_fn,
    is_generic_plan_confirmation as _is_generic_plan_confirmation_fn,
    is_status_query_only as _is_status_query_only_fn,
    is_task_executable_status as _is_task_executable_status_fn,
    looks_like_completion_claim as _looks_like_completion_claim_fn,
    reply_promises_execution as _reply_promises_execution_fn,
    should_force_plan_first as _should_force_plan_first_fn,
)
from .models import AgentResult, AgentStep
from .plan_helpers import (
    auto_decompose_plan as _auto_decompose_plan_fn,
    build_suggestions as _build_suggestions_fn,
    coerce_int as _coerce_int_fn,
    persist_if_dirty as _persist_if_dirty_fn,
    refresh_plan_tree as _refresh_plan_tree_fn,
    require_plan_bound as _require_plan_bound_fn,
)
from .prompt_builder import (
    build_prompt as _build_prompt_fn,
    build_simple_stream_chat_prompt as _build_simple_stream_chat_prompt_fn,
    coerce_plain_text_chat_response as _coerce_plain_text_chat_response_fn,
    compose_action_catalog as _compose_action_catalog_fn,
    compose_guidelines as _compose_guidelines_fn,
    compose_plan_catalog as _compose_plan_catalog_fn,
    compose_plan_status as _compose_plan_status_fn,
    format_history as _format_history_fn,
    format_memories as _format_memories_fn,
    get_structured_agent_prompts as _get_structured_agent_prompts_fn,
    rewrite_plain_chat_execution_claims as _rewrite_plain_chat_execution_claims_fn,
    strip_code_fence as _strip_code_fence_fn,
)
from .request_routing import (
    RequestRoutingDecision,
    RequestTierProfile,
    build_request_tier_profile,
    requests_existing_image_display,
    requests_image_regeneration,
    resolve_request_routing,
)
from .background import _sse_message
from .services import app_settings, decomposer_settings, plan_repository
from .session_helpers import (
    _derive_conversation_id,
    _extract_taskid_from_result,
    _get_session_current_task,
    _get_session_settings,
    _lookup_phagescope_task_memory,
    _normalize_base_model,
    _normalize_llm_provider,
    _normalize_modulelist_value,
    _resolve_phagescope_taskid_alias,
    _normalize_search_provider,
    _record_phagescope_task_memory,
    _save_chat_message,
    _set_session_plan_id,
    _update_session_metadata,
)
from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    subject_identity_matches,
)

logger = logging.getLogger(__name__)
_RUNTIME_CONTEXT_KEYS = (
    "active_subject",
    "last_failure_state",
    "last_evidence_state",
    "last_subject_action_class",
    "recent_image_artifacts",
)
_LOCAL_INTENT_TYPES = {"local_read", "local_inspect"}
_BRIEF_EXECUTE_INTENT_TYPES = {
    "execute_task",
    "local_mutation",
    "local_read",
    "local_inspect",
}
_CONTINUATION_FILENAME_RE = re.compile(
    r"(?<![A-Za-z0-9_/.-])([A-Za-z0-9][A-Za-z0-9_.-]{1,120}\.(?:tsv|csv|txt|json|ya?ml|gff3?|fa|fasta|faa|fna|fastq|fq|md|pdf|png|jpe?g|svg|xlsx?|zip|gz|tar))",
    flags=re.IGNORECASE,
)
_REAL_ABSOLUTE_PATH_PREFIXES = (
    "/Users/",
    "/home/",
    "/tmp/",
    "/var/",
    "/opt/",
    "/private/",
    "/Volumes/",
    "/etc/",
    "/dev/",
    "/mnt/",
    "/srv/",
    "/root/",
    "/workspace/",
    "/workspaces/",
    "/data/",
)
_LOW_SIGNAL_CONTINUATION_FILENAMES = {
    "result.json",
    "manifest.json",
    "preview.json",
}
_IMAGE_PREVIOUS_SELECTION_PHRASES = ("上一张", "前一张", "previous image", "prior image")
_IMAGE_LATEST_SELECTION_PHRASES = (
    "刚才那张",
    "刚刚那张",
    "最新那张",
    "最后那张",
    "latest image",
    "last image",
)


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


def _current_user_turn_index_from_history(
    history: Optional[List[Dict[str, Any]]],
) -> int:
    if not history:
        return 1
    return 1 + sum(
        1
        for item in history
        if str(item.get("role") or "").strip().lower() == "user"
    )


def _is_brief_execute_followup_request(
    routing_decision: Any,
) -> bool:
    if routing_decision is None:
        return False
    request_tier = str(getattr(routing_decision, "request_tier", "") or "").strip().lower()
    intent_type = str(getattr(routing_decision, "intent_type", "") or "").strip().lower()
    brevity_hint = bool(getattr(routing_decision, "brevity_hint", False))
    return (
        request_tier == "execute"
        and brevity_hint
        and intent_type in _BRIEF_EXECUTE_INTENT_TYPES
    )


def _clip_continuation_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _append_unique_hint(target: List[str], seen: set[str], value: Any, *, limit: int = 6) -> None:
    text = str(value or "").strip()
    if not text:
        return
    normalized = text.lower()
    if normalized in seen:
        return
    seen.add(normalized)
    target.append(text)
    if len(target) > limit:
        del target[limit:]


def _looks_like_real_absolute_path(value: Any) -> bool:
    path = str(value or "").strip()
    if not path.startswith("/"):
        return False
    return any(path.startswith(prefix) for prefix in _REAL_ABSOLUTE_PATH_PREFIXES)


def _path_hint_priority(path: str) -> int:
    normalized = str(path or "").strip().lower()
    basename = os.path.basename(normalized)
    score = 0
    if "/runtime/" not in normalized:
        score += 4
    else:
        score -= 3
    if any(marker in normalized for marker in ("/phagescope/", "/data/", "/paper/", "/results/")):
        score += 4
    if "." in basename:
        score += 2
    if basename in _LOW_SIGNAL_CONTINUATION_FILENAMES:
        score -= 6
    return score


def _extract_recent_path_and_filename_hints(
    history: Optional[List[Dict[str, Any]]],
    recent_tool_results: Any,
    active_subject: Any,
) -> Dict[str, List[str]]:
    paths: List[str] = []
    filenames: List[str] = []
    seen_paths: set[str] = set()
    seen_filenames: set[str] = set()

    def _collect_text_hints(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        for path in _extract_declared_absolute_paths_fn(text):
            if not _looks_like_real_absolute_path(path):
                continue
            _append_unique_hint(paths, seen_paths, path, limit=6)
            basename = os.path.basename(path)
            if basename and "." in basename and basename.lower() not in _LOW_SIGNAL_CONTINUATION_FILENAMES:
                _append_unique_hint(filenames, seen_filenames, basename, limit=6)
        for match in _CONTINUATION_FILENAME_RE.findall(text):
            filename = str(match or "").strip()
            if not filename:
                continue
            if "/" in filename:
                continue
            if filename.lower() in _LOW_SIGNAL_CONTINUATION_FILENAMES:
                continue
            _append_unique_hint(filenames, seen_filenames, filename, limit=6)

    if isinstance(active_subject, dict):
        for key in ("canonical_ref", "display_ref"):
            value = str(active_subject.get(key) or "").strip()
            if not value:
                continue
            if _looks_like_real_absolute_path(value):
                _append_unique_hint(paths, seen_paths, value, limit=6)
            else:
                _collect_text_hints(value)

    if isinstance(history, list):
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            _collect_text_hints(item.get("content"))

    if isinstance(recent_tool_results, list):
        for item in reversed(recent_tool_results):
            if not isinstance(item, dict):
                continue
            _collect_text_hints(item.get("summary"))
            result_payload = item.get("result")
            if result_payload is None:
                continue
            try:
                serialized = json.dumps(result_payload, ensure_ascii=False, default=str)
            except Exception:
                serialized = str(result_payload)
            _collect_text_hints(serialized)

    ranked_paths = sorted(paths, key=_path_hint_priority, reverse=True)

    return {
        "known_paths": ranked_paths[:4],
        "known_filenames": filenames[:4],
    }


def _build_brief_execute_continuation_summary(
    agent: Any,
    routing_decision: Any,
) -> Optional[Dict[str, Any]]:
    if not _is_brief_execute_followup_request(routing_decision):
        return None

    history = getattr(agent, "history", None) or []
    extra_context = getattr(agent, "extra_context", {}) or {}
    previous_user_request = ""
    previous_assistant_summary = ""
    if isinstance(history, list):
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant" and not previous_assistant_summary:
                previous_assistant_summary = _clip_continuation_text(content, limit=260)
            elif role == "user" and not previous_user_request:
                previous_user_request = _clip_continuation_text(content, limit=220)
            if previous_user_request and previous_assistant_summary:
                break

    summary: Dict[str, Any] = {}
    if previous_user_request:
        summary["previous_user_request"] = previous_user_request
    if previous_assistant_summary:
        summary["previous_assistant_summary"] = previous_assistant_summary

    active_subject = extra_context.get("active_subject")
    if isinstance(active_subject, dict):
        active_ref = str(
            active_subject.get("display_ref") or active_subject.get("canonical_ref") or ""
        ).strip()
        if active_ref:
            summary["active_subject"] = _clip_continuation_text(active_ref, limit=240)

    hints = _extract_recent_path_and_filename_hints(
        history,
        extra_context.get("recent_tool_results", []),
        active_subject,
    )
    if hints["known_paths"]:
        summary["known_paths"] = hints["known_paths"]
    if hints["known_filenames"]:
        summary["known_filenames"] = hints["known_filenames"]

    recent_image_artifacts = extra_context.get("recent_image_artifacts")
    if isinstance(recent_image_artifacts, list) and recent_image_artifacts:
        image_anchors: List[str] = []
        for item in recent_image_artifacts[:4]:
            if not isinstance(item, dict):
                continue
            display_name = str(item.get("display_name") or "").strip()
            path = str(item.get("path") or "").strip()
            source_tool = str(item.get("source_tool") or "").strip()
            anchor = display_name or path
            if not anchor:
                continue
            if source_tool:
                anchor = f"{anchor} ({source_tool})"
            image_anchors.append(anchor)
        if image_anchors:
            summary["recent_image_artifacts"] = image_anchors

    recent_tool_results = extra_context.get("recent_tool_results", [])
    if isinstance(recent_tool_results, list) and recent_tool_results:
        latest = recent_tool_results[-1]
        if isinstance(latest, dict):
            tool_name = str(latest.get("tool") or latest.get("name") or "").strip()
            tool_summary = _clip_continuation_text(latest.get("summary"), limit=260)
            if tool_summary:
                summary["latest_tool_result"] = (
                    f"{tool_name}: {tool_summary}" if tool_name else tool_summary
                )

    failure_state = extra_context.get("last_failure_state")
    if isinstance(failure_state, dict):
        tool_name = str(failure_state.get("tool_name") or "").strip()
        operation = str(failure_state.get("operation") or "").strip()
        error_message = _clip_continuation_text(
            failure_state.get("error_message"),
            limit=220,
        )
        if error_message:
            prefix = " ".join(part for part in (tool_name, operation) if part).strip()
            summary["last_failure"] = (
                f"{prefix}: {error_message}" if prefix else error_message
            )

    return summary or None


def _select_recent_image_artifacts(
    user_message: str,
    recent_items: Sequence[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], str]:
    normalized_items = [dict(item) for item in recent_items if isinstance(item, dict)]
    if not normalized_items:
        return [], "none"
    if len(normalized_items) == 1:
        return [normalized_items[0]], "single"

    lowered = str(user_message or "").strip().lower()
    if any(token in lowered for token in _IMAGE_PREVIOUS_SELECTION_PHRASES):
        return [normalized_items[1]], "previous"
    if any(token in lowered for token in _IMAGE_LATEST_SELECTION_PHRASES):
        return [normalized_items[0]], "latest"

    for item in normalized_items:
        display_name = str(item.get("display_name") or "").strip().lower()
        path = str(item.get("path") or "").strip().lower()
        basename = os.path.basename(path).strip().lower()
        if display_name and display_name in lowered:
            return [item], "named"
        if basename and basename in lowered:
            return [item], "named"

    return [], "ambiguous"


def _build_recent_image_display_response(
    agent: Any,
    *,
    user_message: str,
    routing_decision: RequestRoutingDecision,
) -> Optional[tuple[str, Dict[str, Any]]]:
    extra_context = getattr(agent, "extra_context", {}) or {}
    if requests_image_regeneration(user_message):
        return None
    if not requests_existing_image_display(user_message, extra_context):
        return None

    recent_items = extra_context.get("recent_image_artifacts")
    if not isinstance(recent_items, list) or not recent_items:
        return None

    selected_items, selection_mode = _select_recent_image_artifacts(user_message, recent_items)
    metadata: Dict[str, Any] = {
        "status": "completed",
        **routing_decision.metadata(),
    }

    if not selected_items:
        if selection_mode != "ambiguous":
            return None
        response_text = (
            "当前会话里有多张图片。我先不重新生成。你要看哪一张？"
            "可以说“最新那张”“上一张”，或直接说文件名。"
        )
        metadata["analysis_text"] = response_text
        metadata["final_summary"] = response_text
        return response_text, metadata

    merged_gallery = merge_artifact_gallery(None, selected_items, limit=4)
    update_recent_image_artifacts(extra_context, merged_gallery)
    metadata["artifact_gallery"] = merged_gallery

    if selection_mode == "previous":
        response_text = "这里是上一张图片。"
    elif selection_mode in {"latest", "single"}:
        response_text = "这里是刚才那张图片。"
    else:
        response_text = "这里是你要看的那张图片。"
    metadata["analysis_text"] = response_text
    metadata["final_summary"] = response_text
    return response_text, metadata


def _persist_runtime_context(agent: Any) -> None:
    if not getattr(agent, "session_id", None):
        return

    def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
        for key in _RUNTIME_CONTEXT_KEYS:
            value = (getattr(agent, "extra_context", {}) or {}).get(key)
            if isinstance(value, dict):
                metadata[key] = dict(value)
            elif isinstance(value, list) and key == "recent_image_artifacts":
                metadata[key] = [dict(item) for item in value if isinstance(item, dict)]
            else:
                metadata.pop(key, None)
        return metadata

    _update_session_metadata(agent.session_id, _updater)


def _seed_active_subject_from_routing(
    agent: Any,
    routing_decision: RequestRoutingDecision,
) -> None:
    subject = (
        dict(routing_decision.subject_resolution)
        if isinstance(routing_decision.subject_resolution, dict)
        else {}
    )
    kind = str(subject.get("kind") or "none").strip().lower()
    canonical_ref = canonicalize_subject_ref(
        subject.get("canonical_ref") or subject.get("display_ref")
    )
    if kind == "none" or not canonical_ref:
        return
    display_ref = str(subject.get("display_ref") or canonical_ref).strip() or canonical_ref
    aliases = build_subject_aliases(subject.get("aliases"), canonical_ref, display_ref)

    current_turn = int(
        (getattr(agent, "extra_context", {}) or {}).get("current_user_turn_index")
        or _current_user_turn_index_from_history(getattr(agent, "history", None))
    )
    existing = (
        dict((getattr(agent, "extra_context", {}) or {}).get("active_subject") or {})
        if isinstance((getattr(agent, "extra_context", {}) or {}).get("active_subject"), dict)
        else {}
    )
    same_subject = subject_identity_matches(
        existing,
        candidate_ref=canonical_ref,
        candidate_display_ref=display_ref,
        candidate_aliases=aliases,
    )
    verification_state = (
        str(existing.get("verification_state") or "").strip() if same_subject else "unresolved"
    ) or "unresolved"
    active_subject = {
        "kind": kind,
        "canonical_ref": canonical_ref,
        "display_ref": display_ref,
        "aliases": aliases,
        "verification_state": verification_state,
        "salience": 5,
        "last_tool_scope": existing.get("last_tool_scope") if same_subject else None,
        "created_turn": existing.get("created_turn") if same_subject else current_turn,
        "last_referenced_turn": current_turn,
        "last_verified_turn": existing.get("last_verified_turn") if same_subject else None,
    }
    agent.extra_context["active_subject"] = active_subject


def _build_grounded_local_failure_message(
    *,
    failure_state: Dict[str, Any],
    active_subject: Optional[Dict[str, Any]],
    query: str,
) -> str:
    language = detect_reasoning_language(query or "")
    subject_ref = ""
    if isinstance(active_subject, dict):
        subject_ref = str(
            active_subject.get("display_ref") or active_subject.get("canonical_ref") or ""
        ).strip()
    if not subject_ref:
        subject_ref = str(failure_state.get("subject_ref") or "该路径").strip() or "该路径"
    error_message = str(failure_state.get("error_message") or "").strip() or "unknown error"
    lowered_error = error_message.lower()
    if language == "zh":
        if "not found" in lowered_error or "不存在" in error_message:
            return (
                f"我目前还不能确认 `{subject_ref}` 里面有哪些数据。"
                f"刚才对该路径的真实检查返回了 `{error_message}`，所以现在没有足够证据说已经读到了目录或文件内容。"
            )
        if "permission" in lowered_error or "权限" in error_message:
            return (
                f"我目前还不能确认 `{subject_ref}` 的内容。"
                f"真实工具调用返回了权限相关失败：`{error_message}`。"
            )
        if "tool_not_available" in lowered_error or "not available" in lowered_error:
            return (
                f"我目前还不能确认 `{subject_ref}` 的内容。"
                f"这轮请求需要本地只读探索能力，但实际可用工具不足：`{error_message}`。"
            )
        return (
            f"我目前还不能确认 `{subject_ref}` 的内容。"
            f"刚才的真实工具结果是失败：`{error_message}`，因此现在证据不足，不能说已经读到了文件或目录内容。"
        )
    if "not found" in lowered_error:
        return (
            f"I cannot confirm what is inside `{subject_ref}` yet. "
            f"The real tool result for that path was `{error_message}`, so there is not enough evidence to claim the file or directory was read."
        )
    if "permission" in lowered_error:
        return (
            f"I cannot confirm the contents of `{subject_ref}` yet. "
            f"The real tool result was a permission failure: `{error_message}`."
        )
    if "tool_not_available" in lowered_error or "not available" in lowered_error:
        return (
            f"I cannot confirm the contents of `{subject_ref}` yet. "
            f"This request requires local inspection, but the required tool capability was unavailable: `{error_message}`."
        )
    return (
        f"I cannot confirm the contents of `{subject_ref}` yet. "
        f"The real tool result failed with `{error_message}`, so there is not enough evidence to claim the file or directory was read."
    )


def _apply_grounded_local_answer(
    agent: Any,
    answer: str,
    routing_decision: RequestRoutingDecision,
) -> str:
    intent_type = str(getattr(routing_decision, "intent_type", "") or "").strip().lower()
    if (
        intent_type not in _LOCAL_INTENT_TYPES
        and intent_type != "local_mutation"
    ):
        return str(answer or "").strip()
    evidence_state = (getattr(agent, "extra_context", {}) or {}).get("last_evidence_state")
    active_subject = (getattr(agent, "extra_context", {}) or {}).get("active_subject")
    if intent_type == "local_mutation" and isinstance(evidence_state, dict):
        st = str(evidence_state.get("status") or "").strip().lower()
        if st == "unverified":
            subject_ref = ""
            if isinstance(active_subject, dict):
                subject_ref = str(
                    active_subject.get("display_ref") or active_subject.get("canonical_ref") or ""
                ).strip()
            if not subject_ref:
                subject_ref = "当前目标路径"
            language = detect_reasoning_language(routing_decision.effective_user_message or "")
            if language == "zh":
                return (
                    f"命令已发送到终端，但还没有足够证据确认本地修改已经完成（例如解压/移动/删除是否真正成功）。"
                    f"目标：`{subject_ref}`。请稍后重试 `terminal_session replay` 或用 `file_operations` 检查路径。"
                )
            return (
                f"The command was sent to the terminal, but there is not enough evidence yet to confirm "
                f"the local file change completed successfully. Subject: `{subject_ref}`. "
                f"Retry `terminal_session replay` or verify paths with `file_operations`."
            )
    if isinstance(evidence_state, dict):
        verified_facts = evidence_state.get("verified_facts")
        est = str(evidence_state.get("status") or "").strip().lower()
        if est == "verified" and isinstance(verified_facts, list) and verified_facts:
            return str(answer or "").strip()
    failure_state = (getattr(agent, "extra_context", {}) or {}).get("last_failure_state")
    if not isinstance(failure_state, dict):
        return str(answer or "").strip()
    if intent_type == "local_mutation":
        subject_ref = ""
        if isinstance(active_subject, dict):
            subject_ref = str(
                active_subject.get("display_ref") or active_subject.get("canonical_ref") or ""
            ).strip()
        if not subject_ref:
            subject_ref = str(failure_state.get("subject_ref") or "当前目标路径").strip() or "当前目标路径"
        error_message = str(failure_state.get("error_message") or "unknown error").strip()
        language = detect_reasoning_language(routing_decision.effective_user_message or "")
        lowered_error = error_message.lower()
        if language == "zh":
            if "not found" in lowered_error or "不存在" in error_message:
                return (
                    f"我还没有成功完成对 `{subject_ref}` 的本地修改。"
                    f"真实工具调用返回了 `{error_message}`，所以目标 zip 或目录目前没有被成功处理。"
                )
            return (
                f"我还没有成功完成对 `{subject_ref}` 的本地修改。"
                f"真实工具调用失败：`{error_message}`。"
            )
        if "not found" in lowered_error:
            return (
                f"I could not complete the requested local file change for `{subject_ref}`. "
                f"The real tool result was `{error_message}`, so the target zip or directory was not processed."
            )
        return (
            f"I could not complete the requested local file change for `{subject_ref}`. "
            f"The real tool result failed with `{error_message}`."
        )

    return _build_grounded_local_failure_message(
        failure_state=failure_state,
        active_subject=active_subject if isinstance(active_subject, dict) else None,
        query=routing_decision.effective_user_message,
    )


def _structured_plan_metadata_from_result(
    result: DeepThinkResult,
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
    return metadata


def _plan_runtime_metadata(plan_tree: Any) -> Dict[str, Any]:
    metadata = getattr(plan_tree, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    plan_evaluation = metadata.get("plan_evaluation")
    if isinstance(plan_evaluation, dict):
        return {"plan_evaluation": dict(plan_evaluation)}
    return {}


def _should_bind_created_plan(
    *,
    existing_plan_id: Optional[int],
    plan_request_mode: Optional[str],
) -> bool:
    if existing_plan_id is None:
        return True
    return str(plan_request_mode or "").strip().lower() == "create_new"


class StructuredChatAgent:
    """Plan conversation agent using a structured schema."""

    # Legacy attribute for tests / duck-typed agents. Runtime uses `max_history_messages`
    # from settings (`CHAT_HISTORY_MAX_MESSAGES`, default 80).
    MAX_HISTORY = 80
    PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*previous\.([^\}]+)\s*\}\}")

    def __init__(
        self,
        *,
        mode: Optional[str] = "assistant",
        plan_session: Optional[PlanSession] = None,
        plan_decomposer: Optional[PlanDecomposer] = None,
        plan_executor: Optional[PlanExecutor] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.mode = mode or "assistant"
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.history = history or []
        try:
            _ch_raw = int(getattr(get_settings(), "chat_history_max_messages", 80))
        except Exception:
            _ch_raw = 80
        self.max_history_messages = max(1, min(CHAT_HISTORY_ABS_MAX, _ch_raw))
        self.extra_context = extra_context or {}
        provider = _normalize_search_provider(
            self.extra_context.get("default_search_provider")
        )
        if provider:
            self.extra_context["default_search_provider"] = provider
        elif "default_search_provider" in self.extra_context:
            self.extra_context.pop("default_search_provider", None)
        base_model = _normalize_base_model(
            self.extra_context.get("default_base_model")
        )
        if base_model:
            self.extra_context["default_base_model"] = base_model
        elif "default_base_model" in self.extra_context:
            self.extra_context.pop("default_base_model", None)
        llm_provider = _normalize_llm_provider(
            self.extra_context.get("default_llm_provider")
        )
        if llm_provider:
            self.extra_context["default_llm_provider"] = llm_provider
        elif "default_llm_provider" in self.extra_context:
            self.extra_context.pop("default_llm_provider", None)

        override_llm_service: Optional[LLMService] = None
        if llm_provider:
            override_llm_service = LLMService(LLMClient(provider=llm_provider, model=base_model))

        self.plan_session = plan_session or PlanSession(repo=plan_repository)
        self.plan_tree = self.plan_session.current_tree()
        self.schema_json = schema_as_json()
        self.llm_service = override_llm_service or get_llm_service()

        if override_llm_service:
            override_decomposer_settings = decomposer_settings
            if base_model:
                override_decomposer_settings = replace(
                    override_decomposer_settings, model=base_model
                )
            override_executor_settings = get_executor_settings()
            if base_model:
                override_executor_settings = replace(
                    override_executor_settings, model=base_model
                )
            decomposer_llm = PlanDecomposerLLMService(
                llm=override_llm_service, settings=override_decomposer_settings
            )
            self.plan_decomposer = PlanDecomposer(
                repo=self.plan_session.repo,
                llm_service=decomposer_llm,
                settings=override_decomposer_settings,
            )
            executor_llm = PlanExecutorLLMService(
                llm=override_llm_service, settings=override_executor_settings
            )
            self.plan_executor = PlanExecutor(
                repo=self.plan_session.repo,
                llm_service=executor_llm,
                settings=override_executor_settings,
            )
        else:
            self.plan_decomposer = plan_decomposer
            self.plan_executor = plan_executor
        self.decomposer_settings = decomposer_settings
        self._last_decomposition: Optional[DecompositionResult] = None
        self._decomposition_errors: List[str] = []
        self._decomposition_notes: List[str] = []
        self._dirty = False
        self._sync_job_id: Optional[str] = None
        self._current_user_message: Optional[str] = None
        self._include_action_summary = getattr(
            app_settings, "chat_include_action_summary", True
        )
        self._task_verifier = TaskVerificationService()

    async def handle(self, user_message: str) -> AgentResult:
        routing_decision, _route_profile = self._resolve_request_routing(user_message)
        effective_user_message = routing_decision.effective_user_message
        self._update_routing_context(routing_decision)
        structured = await self._invoke_llm(effective_user_message)
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
        structured = self._apply_explicit_plan_review_guardrail(structured)
        structured = self._apply_phagescope_fallback(structured)
        structured = self._apply_task_execution_followthrough_guardrail(structured)
        structured = self._apply_completion_claim_guardrail(structured)
        return await self.execute_structured(structured)

    async def get_structured_response(self, user_message: str) -> LLMStructuredResponse:
        """Return the raw structured response without executing actions."""
        routing_decision, _route_profile = self._resolve_request_routing(user_message)
        effective_user_message = routing_decision.effective_user_message
        self._update_routing_context(routing_decision)
        structured = await self._invoke_llm(effective_user_message)
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
        structured = self._apply_explicit_plan_review_guardrail(structured)
        structured = self._apply_phagescope_fallback(structured)
        structured = self._apply_task_execution_followthrough_guardrail(structured)
        return self._apply_completion_claim_guardrail(structured)

    # -----------------------------------------------------------------------
    # Guardrail predicates (static) – extracted to chat/guardrails.py
    # -----------------------------------------------------------------------
    _explicit_manuscript_request = staticmethod(_explicit_manuscript_request_fn)
    _extract_task_id_from_text = staticmethod(_extract_task_id_from_text_fn)
    _extract_declared_absolute_paths = staticmethod(_extract_declared_absolute_paths_fn)
    _is_generic_plan_confirmation = staticmethod(_is_generic_plan_confirmation_fn)
    _is_status_query_only = staticmethod(_is_status_query_only_fn)
    _is_task_executable_status = staticmethod(_is_task_executable_status_fn)
    _looks_like_completion_claim = staticmethod(_looks_like_completion_claim_fn)
    _reply_promises_execution = staticmethod(_reply_promises_execution_fn)
    _should_force_plan_first = staticmethod(_should_force_plan_first_fn)

    # -----------------------------------------------------------------------
    # Guardrail handlers (instance) – extracted to chat/guardrail_handlers.py
    # -----------------------------------------------------------------------
    async def _apply_experiment_fallback(
        self, structured: LLMStructuredResponse
    ) -> LLMStructuredResponse:
        return await _apply_experiment_fallback_fn(self, structured)

    def _apply_phagescope_fallback(
        self, structured: LLMStructuredResponse
    ) -> LLMStructuredResponse:
        return _apply_phagescope_fallback_fn(self, structured)

    def _apply_task_execution_followthrough_guardrail(
        self, structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        return _apply_task_execution_followthrough_guardrail_fn(self, structured)

    def _resolve_followthrough_target_task_id(
        self, *, tree, user_message, reply_text,
    ):
        return _resolve_followthrough_target_task_id_fn(
            self, tree=tree, user_message=user_message, reply_text=reply_text,
        )

    def _apply_completion_claim_guardrail(
        self, structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        return _apply_completion_claim_guardrail_fn(self, structured)

    def _first_executable_atomic_descendant(self, tree, parent_task_id):
        return _first_executable_atomic_descendant_fn(tree, parent_task_id)

    def _match_atomic_task_by_keywords(self, tree, text):
        return _match_atomic_task_by_keywords_fn(tree, text)

    def _infer_plan_seed_message(self, current_message):
        return _infer_plan_seed_message_fn(self, current_message)

    def _apply_plan_first_guardrail(
        self, structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        return _apply_plan_first_guardrail_fn(self, structured)

    def _apply_explicit_plan_review_guardrail(
        self, structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        return _apply_explicit_plan_review_guardrail_fn(self, structured)


    def _resolve_code_executor_task_context(self):
        return _resolve_code_executor_task_context_fn(self)

    _normalize_csv_arg = staticmethod(_normalize_csv_arg_fn)

    _summarize_amem_experiences_for_cc = staticmethod(_summarize_amem_experiences_for_cc_fn)

    _compose_code_executor_atomic_task_prompt = staticmethod(_compose_code_executor_atomic_task_prompt_fn)

    def _resolve_previous_path(self, previous_result, path):
        return _resolve_previous_path_fn(previous_result, path)

    def _resolve_placeholders_in_value(self, value, previous_result):
        return _resolve_placeholders_in_value_fn(value, previous_result)

    def _resolve_action_placeholders(self, action, previous_result):
        return _resolve_action_placeholders_fn(action, previous_result)

    def _should_route_code_executor_unscoped(
        self, context_error: Optional[str]
    ) -> bool:
        if not context_error:
            return False
        allow_raw = self.extra_context.get("allow_unscoped_code_executor", True)
        if isinstance(allow_raw, str):
            allow_unscoped = allow_raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            allow_unscoped = bool(allow_raw)
        if not allow_unscoped:
            return False

        # If caller explicitly selected task_id in request context, keep strict
        # plan-scoped execution.
        if self.extra_context.get("task_id") is not None:
            return False

        return context_error in {
            "missing_plan_binding",
            "missing_target_task",
            "invalid_target_task",
            "target_task_not_found",
            "target_task_not_atomic",
        }

    async def _prepare_code_executor_params(
        self,
        action: LLMAction,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Union[Tuple[Dict[str, Any], Optional[str]], AgentStep]:
        task_value = params.get("task")
        if not isinstance(task_value, str) or not task_value.strip():
            return AgentStep(
                action=action,
                success=False,
                message="code_executor requires a non-empty `task` string.",
                details={"error": "invalid_task", "tool": tool_name},
            )

        original_task = task_value.strip()
        allowed_tools = self._normalize_csv_arg(params.get("allowed_tools"))
        add_dirs = self._normalize_csv_arg(params.get("add_dirs"))

        task_node, context_error = self._resolve_code_executor_task_context()
        if context_error or task_node is None:
            if self._should_route_code_executor_unscoped(context_error):
                logger.info(
                    "[CLAUDE_CODE] Routing to unscoped execution (reason=%s, source=%s)",
                    context_error,
                    self.extra_context.get("_current_task_source"),
                )
                # Inject conversation summary even for unscoped execution
                from app.routers.chat.code_executor_helpers import build_conversation_summary_for_cc
                conv_summary = build_conversation_summary_for_cc(
                    getattr(self, 'history', None) or [],
                    budget=1200,
                )
                unscoped_task = original_task
                if conv_summary:
                    unscoped_task = (
                        f"{original_task}\n\n"
                        f"[Recent conversation context (reference only)]:\n{conv_summary}"
                    )
                prepared_params: Dict[str, Any] = {
                    "task": unscoped_task,
                    "require_task_context": False,
                    "auth_mode": "api_env",
                    "setting_sources": "project",
                }
                if allowed_tools:
                    prepared_params["allowed_tools"] = allowed_tools
                if add_dirs:
                    prepared_params["add_dirs"] = add_dirs
                if self.session_id:
                    prepared_params["session_id"] = self.session_id

                current_job_id = get_current_job()
                if not current_job_id:
                    current_job_id, _ = self._resolve_job_meta()
                if current_job_id:
                    out_cb, err_cb = _code_executor_job_stream_loggers(current_job_id)
                    prepared_params["on_stdout"] = out_cb
                    prepared_params["on_stderr"] = err_cb

                return prepared_params, original_task

            context_messages = {
                "missing_plan_binding": "code_executor execution requires a bound plan. Please create/bind a plan first.",
                "missing_target_task": "code_executor execution requires a target atomic task context. Please select or run a task first.",
                "invalid_target_task": "code_executor execution requires a valid numeric task id.",
                "plan_tree_unavailable": "Unable to load the current plan tree. Please retry after refreshing plan state.",
                "target_task_not_found": "The selected task was not found in the current plan.",
                "target_task_not_atomic": "code_executor can only execute atomic tasks. Please decompose this task and execute a leaf task.",
            }
            return AgentStep(
                action=action,
                success=False,
                message=context_messages.get(
                    context_error or "",
                    "code_executor execution requires a bound atomic task context.",
                ),
                details={
                    "error": context_error or "missing_task_context",
                    "tool": tool_name,
                    "requires_plan_binding": True,
                    "requires_atomic_task": True,
                },
            )

        amem_hints = ""
        try:
            from app.services.amem_client import get_amem_client

            amem_client = get_amem_client()
            if amem_client.enabled:
                amem_experiences = await amem_client.query_experiences(
                    query=original_task,
                    top_k=3,
                )
                if amem_experiences:
                    amem_hints = self._summarize_amem_experiences_for_cc(amem_experiences)
                    logger.info(
                        "[AMEM] Injected compact hints from %d historical experiences",
                        len(amem_experiences),
                    )
        except Exception as amem_err:
            logger.warning("[AMEM] Failed to query experiences: %s", amem_err)

        # Build conversation summary for CC context injection
        from app.routers.chat.code_executor_helpers import (
            build_conversation_summary_for_cc,
            collect_completed_task_outputs,
        )
        conversation_summary = build_conversation_summary_for_cc(
            getattr(self, 'history', None) or [],
            budget=1800,
        )
        data_context = collect_completed_task_outputs(
            self.plan_tree, task_node.id
        )

        constrained_task = self._compose_code_executor_atomic_task_prompt(
            task_node=task_node,
            original_task=original_task,
            amem_hints=amem_hints,
            data_context=data_context or None,
            conversation_summary=conversation_summary or None,
        )

        prepared_params: Dict[str, Any] = {
            "task": constrained_task,
            "auth_mode": "api_env",
            "setting_sources": "project",
            "require_task_context": True,
        }
        if allowed_tools:
            prepared_params["allowed_tools"] = allowed_tools
        if add_dirs:
            prepared_params["add_dirs"] = add_dirs
        if self.session_id:
            prepared_params["session_id"] = self.session_id
        prepared_params["plan_id"] = task_node.plan_id
        prepared_params["task_id"] = task_node.id

        current_job_id = get_current_job()
        if not current_job_id:
            current_job_id, _ = self._resolve_job_meta()
        if current_job_id:
            out_cb, err_cb = _code_executor_job_stream_loggers(current_job_id)
            prepared_params["on_stdout"] = out_cb
            prepared_params["on_stderr"] = err_cb

        return prepared_params, original_task

    def _sync_task_status_after_tool_execution(
        self,
        tool_name: str,
        success: Any,
        summary: str,
        message: str,
        params: Optional[Dict[str, Any]] = None,
        result: Optional[Any] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if (
            tool_name == "code_executor"
            and isinstance(params, dict)
            and params.get("require_task_context") is False
        ):
            logger.info(
                "[TASK_SYNC] Skipping task status sync for unscoped code_executor execution"
            )
            return

        current_task_id = self.extra_context.get("current_task_id")
        if current_task_id is None or self.plan_session.plan_id is None:
            return
        try:
            new_status = "completed" if success else "failed"
            task_id_int = int(current_task_id)
            repo = self.plan_session.repo
            verifier = getattr(self, "_task_verifier", None) or TaskVerificationService()
            node = PlanNode(
                id=task_id_int,
                plan_id=self.plan_session.plan_id,
                name=f"Task {task_id_int}",
                status="pending",
                metadata={},
            )
            try:
                tree = repo.get_plan_tree(self.plan_session.plan_id)
                if tree.has_node(task_id_int):
                    node = tree.get_node(task_id_int)
            except Exception as tree_err:
                logger.debug(
                    "[TASK_SYNC] Failed to load plan tree for verification context: %s",
                    tree_err,
                )

            payload_metadata: Dict[str, Any] = {"tool_name": tool_name}
            if isinstance(extra_metadata, dict):
                for key in ("deliverables", "storage"):
                    value = extra_metadata.get(key)
                    if value is not None:
                        payload_metadata[key] = value
            artifact_paths = verifier.collect_artifact_paths(
                {"result": result, "params": params or {}, "metadata": payload_metadata}
            )
            if artifact_paths:
                payload_metadata["artifact_paths"] = artifact_paths
            payload = {
                "status": new_status,
                "content": summary or message,
                "notes": [],
                "metadata": payload_metadata,
            }
            finalization = verifier.finalize_payload(
                node,
                payload,
                execution_status=new_status,
                trigger="auto",
            )

            repo.update_task(
                self.plan_session.plan_id,
                task_id_int,
                status=finalization.final_status,
                execution_result=json.dumps(finalization.payload, ensure_ascii=False),
            )
            logger.info(
                "[TASK_SYNC] Updated task %s status to %s after tool %s execution",
                current_task_id,
                finalization.final_status,
                tool_name,
            )

            if finalization.final_status == "completed":
                cascade_result = f"Completed as part of parent task #{task_id_int}"
                descendants_updated = repo.cascade_update_descendants_status(
                    self.plan_session.plan_id,
                    task_id_int,
                    status=finalization.final_status,
                    execution_result=cascade_result,
                )
                if descendants_updated > 0:
                    logger.info(
                        "[TASK_SYNC] Cascade updated %d descendant tasks to %s",
                        descendants_updated,
                        new_status,
                    )

            self._dirty = True
        except Exception as sync_err:
            logger.warning(
                "[TASK_SYNC] Failed to update task %s status: %s",
                current_task_id,
                sync_err,
            )

    async def execute_structured(
        self, structured: LLMStructuredResponse
    ) -> AgentResult:
        steps: List[AgentStep] = []
        errors: List[str] = []
        try:
            job_id, job_type = self._resolve_job_meta()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to resolve job metadata: %s", exc)
            job_id = None
            job_type = "chat_action"

        previous_result: Optional[Dict[str, Any]] = None
        anchor_result: Optional[Dict[str, Any]] = None
        for action in structured.sorted_actions():
            placeholder_source = previous_result
            if isinstance(action.metadata, dict) and action.metadata.get("use_anchor") and anchor_result:
                placeholder_source = anchor_result
            action = self._resolve_action_placeholders(action, placeholder_source)
            if (
                action.kind == "tool_operation"
                and action.name == "phagescope"
                and isinstance(action.parameters, dict)
                and steps
            ):
                last_step = steps[-1]
                last_params = (
                    last_step.details.get("parameters")
                    if isinstance(last_step.details, dict)
                    else None
                )
                if (
                    last_step.action.kind == "tool_operation"
                    and last_step.action.name == "phagescope"
                    and last_step.success
                    and isinstance(last_params, dict)
                    and last_params.get("action") == "submit"
                ):
                    current_action = action.parameters.get("action")
                    if current_action in {"result", "quality", "save_all", "download"}:
                        patched = dict(action.parameters)
                        taskid_value = patched.get("taskid")
                        if taskid_value is not None:
                            resolved_taskid = _resolve_phagescope_taskid_alias(
                                taskid_value,
                                session_id=self.session_id
                                if isinstance(self.session_id, str)
                                else None,
                            )
                            if resolved_taskid:
                                patched["taskid"] = resolved_taskid
                            else:
                                patched.pop("taskid", None)
                        if not patched.get("taskid") and previous_result:
                            extracted_taskid = _extract_taskid_from_result(previous_result)
                            if extracted_taskid:
                                patched["taskid"] = extracted_taskid
                        # Do not block on immediate result retrieval after submit.
                        # Convert follow-up actions to a lightweight status query.
                        patched["action"] = "task_detail"
                        patched.pop("result_kind", None)
                        patched.pop("download_path", None)
                        patched.pop("save_path", None)
                        patched.pop("wait", None)
                        patched.pop("poll_interval", None)
                        patched.pop("poll_timeout", None)
                        action.parameters = patched
            retry_limit = 0
            backoff_sec = 0.0
            if action.retry_policy is not None:
                retry_limit = max(0, int(action.retry_policy.max_retries))
                backoff_sec = max(0.0, float(action.retry_policy.backoff_sec))

            attempt = 0
            step: Optional[AgentStep] = None
            while attempt <= retry_limit:
                attempt += 1
                try:
                    step = await self._execute_action(action)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("Action execution failed: %s", exc)
                    step = AgentStep(
                        action=action,
                        success=False,
                        message=f"Action execution failed: {exc}",
                        details={"exception": type(exc).__name__},
                    )

                if step.success or attempt > retry_limit:
                    break

                retry_message = (
                    f"Action {action.kind}/{action.name} failed on attempt "
                    f"{attempt}/{retry_limit + 1}; retrying."
                )
                errors.append(retry_message)
                logger.warning(retry_message)
                if backoff_sec > 0:
                    await asyncio.sleep(backoff_sec)

            if step is None:  # pragma: no cover - defensive
                step = AgentStep(
                    action=action,
                    success=False,
                    message="Action execution failed with an unknown error.",
                    details={"exception": "UnknownError"},
                )

            step.details = dict(step.details or {})
            step.details.setdefault("attempt", attempt)
            step.details.setdefault("max_attempts", retry_limit + 1)
            if action.retry_policy is not None:
                step.details.setdefault(
                    "retry_policy",
                    {"max_retries": retry_limit, "backoff_sec": backoff_sec},
                )

            steps.append(step)
            details = step.details or {}
            result_payload = details.get("result")
            if isinstance(result_payload, dict):
                if (
                    anchor_result is None
                    and step.action.kind == "tool_operation"
                    and step.action.name == "phagescope"
                    and isinstance(details.get("parameters"), dict)
                    and (details["parameters"].get("action") == "save_all")
                ):
                    anchor_result = result_payload

            if not (isinstance(action.metadata, dict) and action.metadata.get("preserve_previous")):
                previous_result = result_payload if isinstance(result_payload, dict) else None

            if not step.success:
                errors.append(step.message)
                if action.blocking:
                    block_message = (
                        f"Stopping execution because blocking action "
                        f"{action.kind}/{action.name} failed."
                    )
                    errors.append(block_message)
                    logger.warning(block_message)
                    break

        suggestions = self._build_suggestions(structured, steps)
        success = all(step.success for step in steps) if steps else True
        primary_intent = steps[-1].action.name if steps else None
        plan_persisted = False
        if self.plan_session.plan_id is not None:
            try:
                plan_persisted = self._persist_if_dirty()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Failed to persist plan state: %s", exc)
                errors.append(f"Failed to save plan updates: {exc}")
        outline = None
        if self.plan_session.plan_id is not None:
            try:
                outline = self.plan_session.outline(max_depth=4, max_nodes=80)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Failed to build plan outline: %s", exc)

        if self._decomposition_errors:
            errors.extend(self._decomposition_errors)

        actions_summary = self._build_actions_summary(steps)
        reply_text = structured.llm_reply.message or ""

        # Special case: one-shot "download + analyze" chain for PhageScope.
        # We must synthesize the analysis here (there is no post-tool LLM pass in this mode).
        try:
            synthesized = self._maybe_synthesize_phagescope_saveall_analysis(steps)
            if synthesized:
                reply_text = synthesized
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug("Failed to synthesize phagescope save_all analysis: %s", exc)
        if self._include_action_summary and actions_summary:
            reply_text = self._append_summary_to_reply(reply_text, actions_summary)

        result = AgentResult(
            reply=reply_text,
            steps=steps,
            suggestions=suggestions,
            primary_intent=primary_intent,
            success=success,
            bound_plan_id=self.plan_session.plan_id,
            plan_outline=outline,
            plan_persisted=plan_persisted,
            job_id=job_id,
            job_type=job_type,
            actions_summary=actions_summary,
            errors=errors,
        )

        if get_current_job() is None:
            self._sync_job_id = None
            if job_id:
                try:
                    update_decomposition_job_status(
                        self.plan_session.plan_id,
                        job_id=job_id,
                        status="succeeded" if success else "failed",
                        finished_at=datetime.utcnow(),
                        stats={
                            "step_count": len(steps),
                            "success": success,
                            "error_count": len(errors),
                        },
                        result=result.model_dump(),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Failed to update sync job status: %s", exc)
        self._current_user_message = None

        return result

        return result

    def _maybe_synthesize_phagescope_saveall_analysis(self, steps):
        return _maybe_synthesize_phagescope_saveall_analysis_fn(self, steps)

    async def process_unified_stream(
        self,
        user_message: str,
        *,
        run_id: Optional[str] = None,
        cancel_event: Optional[asyncio.Event] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        steer_drain: Optional[Callable[[], List[str]]] = None,
    ) -> AsyncIterator[str]:
        """
        Unified agent loop with streaming support and extended thinking.

        All requests with plan context or tool requirements go through this path.
        The model decides its own thinking depth via enable_thinking.

        Optional ``run_id`` aligns the Deep Think job id with a chat run id.
        ``event_sink`` receives the same JSON payloads as SSE ``data:`` lines (dict form).
        """
        routing_decision, route_profile = self._resolve_request_routing(user_message)
        effective_user_message = routing_decision.effective_user_message
        self._update_routing_context(routing_decision)
        if routing_decision.requires_structured_plan:
            logger.info(
                "[CHAT][ROUTING][PLAN] session=%s mode=%s plan_id=%s route=%s tier=%s",
                self.session_id,
                routing_decision.plan_request_mode,
                self.plan_session.plan_id,
                routing_decision.request_route_mode,
                routing_decision.request_tier,
            )
        _seed_active_subject_from_routing(self, routing_decision)
        if self.session_id:
            try:
                _persist_runtime_context(self)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Failed to persist routing runtime context: %s", exc)

        direct_image_response = _build_recent_image_display_response(
            self,
            user_message=effective_user_message,
            routing_decision=routing_decision,
        )
        if direct_image_response is not None:
            response_text, response_metadata = direct_image_response
            if self.session_id and response_text:
                try:
                    _persist_runtime_context(self)
                    _save_chat_message(
                        self.session_id,
                        "assistant",
                        response_text,
                        metadata=response_metadata,
                    )
                except Exception as save_err:  # pragma: no cover - defensive
                    logger.warning(
                        "[CHAT][IMAGE_REUSE] Failed to save direct response: %s",
                        save_err,
                    )
            payload = {
                "type": "final",
                "payload": {
                    "response": response_text,
                    "actions": [],
                    "metadata": response_metadata,
                },
            }
            if event_sink is not None:
                await event_sink(payload)
            yield _sse_message(payload)
            return

        if (
            routing_decision.capability_floor == "plain_chat"
            and routing_decision.simple_channel_allowed
        ):
            async for chunk in self.stream_simple_chat(
                effective_user_message,
                routing_decision=routing_decision,
                route_profile=route_profile,
                event_sink=event_sink,
            ):
                yield chunk
            return

        queue: asyncio.Queue[Any] = asyncio.Queue()
        deep_think_job_id: Optional[str] = run_id or f"dt_{uuid4().hex}"
        deep_think_job_created = False
        deep_think_job_queue: Optional[asyncio.Queue[Any]] = None
        active_tool_iteration: Optional[int] = None
        thinking_visible = routing_decision.thinking_visibility == "visible"
        progress_visible = routing_decision.thinking_visibility == "progress"
        current_turn_artifact_gallery: List[Dict[str, Any]] = []

        if deep_think_job_id:
            try:
                plan_decomposition_jobs.create_job(
                    plan_id=self.plan_session.plan_id,
                    task_id=None,
                    mode="chat_deep_think",
                    job_type="chat_deep_think",
                    params={
                        "session_id": self.session_id,
                    },
                    metadata={
                        "session_id": self.session_id,
                        "origin": "chat_deep_think",
                        "message_preview": str(effective_user_message or "")[:200],
                    },
                    job_id=deep_think_job_id,
                )
                plan_decomposition_jobs.mark_running(deep_think_job_id)
                deep_think_job_created = True
                deep_think_job_queue = plan_decomposition_jobs.register_subscriber(
                    deep_think_job_id, asyncio.get_running_loop()
                )
            except Exception as job_err:
                logger.warning(
                    "[CHAT][DEEP_THINK] Failed to create runtime control job: %s",
                    job_err,
                )
                deep_think_job_id = None
                deep_think_job_created = False
                deep_think_job_queue = None

        reasoning_language = detect_reasoning_language(effective_user_message)

        def _progress_label_from_phase(phase: str) -> str:
            if reasoning_language == "zh":
                mapping = {
                    "planning": "分析请求中",
                    "gathering": "检索资料中",
                    "analyzing": "整理候选方向中",
                    "synthesizing": "汇总结论中",
                    "finalizing": "生成最终答复中",
                }
            else:
                mapping = {
                    "planning": "Planning the response",
                    "gathering": "Gathering evidence",
                    "analyzing": "Analyzing findings",
                    "synthesizing": "Synthesizing conclusions",
                    "finalizing": "Preparing the final answer",
                }
            return mapping.get(phase, mapping["analyzing"])

        def _normalize_progress_text(text: Optional[str]) -> str:
            return re.sub(r"\s+", " ", str(text or "")).strip()

        def _truncate_progress_text(text: Optional[str], max_chars: int = 72) -> str:
            normalized = _normalize_progress_text(text)
            if len(normalized) <= max_chars:
                return normalized
            return f"{normalized[: max_chars - 1].rstrip()}…"

        def _tool_progress_details(
            tool_name: str, params: Optional[Dict[str, Any]]
        ) -> Optional[str]:
            params = params if isinstance(params, dict) else {}
            lowered = (tool_name or "").strip().lower()
            if lowered == "web_search":
                query = _normalize_progress_text(params.get("query"))
                return query or None
            if lowered == "literature_pipeline":
                topic = _normalize_progress_text(
                    params.get("topic") or params.get("query") or params.get("question")
                )
                return topic or None
            if lowered == "document_reader":
                path = _normalize_progress_text(
                    params.get("path") or params.get("file_path")
                )
                return path or None
            if lowered == "file_operations":
                target = _normalize_progress_text(
                    params.get("path")
                    or params.get("target")
                    or params.get("file_path")
                )
                return target or None
            return None

        def _extract_tool_context(action_raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
            if not action_raw:
                return None, None
            try:
                parsed = json.loads(action_raw)
            except Exception:
                return None, None
            if not isinstance(parsed, dict):
                return None, None
            tool_name = str(parsed.get("tool") or "").strip() or None
            params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
            return tool_name, _tool_progress_details(tool_name or "", params)

        def _progress_phase_from_step(step: ThinkingStep) -> str:
            if step.status == "calling_tool" or step.action:
                return "gathering"
            if step.status == "done":
                return "finalizing"
            if step.status == "analyzing":
                return "synthesizing"
            if step.iteration <= 1:
                return "planning"
            return "analyzing"

        async def _emit_progress_status(
            *,
            phase: str,
            label: Optional[str] = None,
            details: Optional[str] = None,
            iteration: Optional[int] = None,
            tool: Optional[str] = None,
            status: str = "active",
        ) -> None:
            if not progress_visible:
                return
            await queue.put(
                {
                    "type": "progress_status",
                    "phase": phase,
                    "label": _truncate_progress_text(label or _progress_label_from_phase(phase), 72),
                    "details": _normalize_progress_text(details) or None,
                    "iteration": iteration,
                    "tool": tool,
                    "status": status,
                }
            )

        async def on_thinking(step: ThinkingStep):
            nonlocal active_tool_iteration
            active_tool_iteration = step.iteration
            if progress_visible:
                phase = _progress_phase_from_step(step)
                progress_tool, progress_details = _extract_tool_context(step.action)
                progress_label = _progress_label_from_phase(phase)
                if progress_tool:
                    progress_label = summarize_tool_step_display(
                        step, language=reasoning_language
                    )
                await _emit_progress_status(
                    phase=phase,
                    label=progress_label,
                    details=progress_details,
                    iteration=step.iteration,
                    tool=progress_tool,
                    status=(
                        "error"
                        if step.status == "error"
                        else ("completed" if step.status == "done" else "active")
                    ),
                )
            if not thinking_visible:
                return
            # For the final concluded step (done, no tool action) the `thought` content
            # is typically the same text as the final answer streamed separately via
            # `on_final_delta`.  Preserving it here would cause it to appear inside the
            # thinking timeline AND again as the main response — a visible duplication.
            # We rely on the `thinking_delta` events already accumulated in the frontend
            # to supply a partial thought summary if needed.
            is_final_concluded = step.status == "done" and not step.action
            await queue.put(
                {
                    "type": "thinking_step",
                    "step": build_user_visible_step(
                        step,
                        language=reasoning_language,
                        preserve_thought=not is_final_concluded,
                    ),
                }
            )

        async def on_thinking_delta(iteration: int, delta: str):
            """Send token-level updates for thinking process."""
            if not thinking_visible:
                return
            logger.debug(
                "[DEEP_THINK_DELTA] iteration=%s delta_len=%s",
                iteration,
                len(delta),
            )
            await queue.put(
                {
                    "type": "thinking_delta",
                    "iteration": iteration,
                    "delta": delta,
                }
            )

        async def on_final_delta(delta: str):
            """Send token-level updates for final answer."""
            await queue.put({"type": "delta", "content": delta})

        async def on_tool_start(tool_name: str, params: Dict[str, Any]) -> None:
            tool_step = ThinkingStep(
                iteration=active_tool_iteration or 0,
                thought="",
                action=json.dumps(
                    {"tool": tool_name, "params": params}, ensure_ascii=False
                ),
                action_result=None,
                self_correction=None,
                display_text=None,
                kind="tool",
            )
            await _emit_progress_status(
                phase="gathering",
                label=summarize_tool_step_display(
                    tool_step, language=reasoning_language
                ),
                details=_tool_progress_details(tool_name, params),
                iteration=active_tool_iteration,
                tool=tool_name,
                status="active",
            )

        async def on_tool_result(tool_name: str, payload: Dict[str, Any]) -> None:
            ok = bool((payload or {}).get("success", True))
            retrying = bool((payload or {}).get("retrying"))
            if retrying:
                await _emit_progress_status(
                    phase="gathering",
                    label=(
                        "检索失败，正在重试"
                        if reasoning_language == "zh"
                        else "Search failed, retrying"
                    ),
                    details=_tool_progress_details(tool_name, payload),
                    iteration=active_tool_iteration,
                    tool=tool_name,
                    status="retrying",
                )
                return
            if not ok:
                await _emit_progress_status(
                    phase="synthesizing",
                    label=(
                        "切换为保守总结"
                        if reasoning_language == "zh"
                        else "Switching to a conservative summary"
                    ),
                    details=_normalize_progress_text(
                        str((payload or {}).get("error") or "")
                    ) or None,
                    iteration=active_tool_iteration,
                    tool=tool_name,
                    status="failed",
                )
                return
            await _emit_progress_status(
                phase="synthesizing",
                label=(
                    "整理搜索结果"
                    if reasoning_language == "zh"
                    else "Reviewing search results"
                ),
                iteration=active_tool_iteration,
                tool=tool_name,
                status="completed",
            )

        async def on_tool_progress(tool_name: str, data: Dict[str, Any]) -> None:
            message = str(data.get("message") or "").strip()
            stage = str(data.get("stage") or "running").strip()
            if not message:
                return
            status = "completed" if stage == "completed" else "active"
            await _emit_progress_status(
                phase="gathering",
                label=_truncate_progress_text(message, 72),
                details=_normalize_progress_text(
                    str(data.get("detail") or "")
                ) or None,
                iteration=active_tool_iteration,
                tool=tool_name,
                status=status,
            )

        async def relay_job_events() -> None:
            if deep_think_job_queue is None:
                return
            while True:
                payload = await deep_think_job_queue.get()
                if not isinstance(payload, dict):
                    continue
                event_payload = payload.get("event")
                if not isinstance(event_payload, dict):
                    continue
                level = str(event_payload.get("level") or "").strip().lower()
                message = event_payload.get("message")

                if level in {"stdout", "stderr"} and isinstance(message, str):
                    await queue.put(
                        {
                            "type": "tool_output",
                            "tool": "code_executor",
                            "stream": level,
                            "content": message,
                            "iteration": active_tool_iteration,
                        }
                    )
                    continue

                metadata = (
                    event_payload.get("metadata")
                    if isinstance(event_payload.get("metadata"), dict)
                    else {}
                )
                if level == "info" and metadata.get("sub_type") == "runtime_control":
                    action = str(metadata.get("action") or "").strip().lower()
                    paused_state: Optional[bool] = None
                    if action == "pause":
                        paused_state = True
                    elif action == "resume":
                        paused_state = False
                    await queue.put(
                        {
                            "type": "control_ack",
                            "job_id": deep_think_job_id,
                            "available": True,
                            "paused": paused_state,
                            "action": action or None,
                        }
                    )

        async def run_agent():
            relay_task: Optional[asyncio.Task[Any]] = None
            job_token = (
                set_current_job(deep_think_job_id)
                if deep_think_job_created and deep_think_job_id
                else None
            )
            try:
                if deep_think_job_queue is not None:
                    relay_task = asyncio.create_task(relay_job_events())

                deep_think_tool_order = 0
                deep_think_bg_category: Optional[str] = None
                bio_failure_active = False
                failed_tool_name: Optional[str] = None
                help_seen_after_failure = False
                retry_seen_after_help = False
                bio_input_block_key = "bio_tools_no_claude_fallback"
                sequence_input_block_key = "sequence_fetch_no_claude_fallback"
                phagescope_taskid_block_key = "phagescope_invalid_taskid_block"

                def _safe_text(value: Any, *, limit: int = 600) -> str:
                    text = str(value or "").strip()
                    if len(text) <= limit:
                        return text
                    return text[: max(0, limit - 3)] + "..."

                def _normalize_deep_think_tool_result(
                    *,
                    step: AgentStep,
                    tool_name: str,
                    tool_params: Dict[str, Any],
                    iteration: int,
                ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
                    details = step.details if isinstance(step.details, dict) else {}
                    result_payload = details.get("result")
                    if isinstance(result_payload, dict):
                        result: Dict[str, Any] = dict(result_payload)
                    else:
                        message_text = _safe_text(step.message, limit=600)
                        detail_error = _safe_text(details.get("error"), limit=600)
                        error_text = (
                            detail_error
                            or message_text
                            or "Tool execution returned malformed result payload."
                        )
                        result = {
                            "success": False,
                            "tool": tool_name,
                            "error": error_text,
                            "summary": message_text or error_text,
                            "protocol_warning": True,
                            "parameters": dict(tool_params),
                            "iteration": iteration,
                            "result_payload_type": type(result_payload).__name__,
                        }
                        detail_error_code = details.get("error")
                        if isinstance(detail_error_code, str) and detail_error_code.strip():
                            result["error_code"] = detail_error_code.strip()
                        preview = _safe_text(result_payload, limit=280)
                        if preview:
                            result["result_payload_preview"] = preview
                        logger.warning(
                            "[DeepThink] Tool wrapper recovered malformed result payload: tool=%s payload_type=%s",
                            tool_name,
                            type(result_payload).__name__,
                        )

                    if "success" not in result:
                        result["success"] = bool(step.success)
                    if isinstance(step.message, str) and step.message.strip():
                        result.setdefault("summary", step.message.strip())
                    storage_payload = details.get("storage")
                    if storage_payload is not None:
                        result.setdefault("storage", storage_payload)
                    deliverables_payload = details.get("deliverables")
                    if deliverables_payload is not None:
                        result.setdefault("deliverables", deliverables_payload)

                    return result, details

                def _build_bio_recovery_blocked_payload() -> Dict[str, Any]:
                    summary = (
                        "code_executor fallback is blocked until bio_tools recovery completes "
                        "(run bio_tools help, then retry a bio_tools operation once)."
                    )
                    payload: Dict[str, Any] = {
                        "success": False,
                        "tool": "code_executor",
                        "error": summary,
                        "summary": summary,
                        "blocked_reason": "bio_tools_recovery_not_completed",
                        "recovery_required": "bio_tools help -> retry",
                    }
                    if failed_tool_name:
                        payload["failed_tool_name"] = failed_tool_name
                    return payload

                def _build_bio_input_blocked_payload(
                    block_context: Optional[Dict[str, Any]]
                ) -> Dict[str, Any]:
                    root_cause = ""
                    if isinstance(block_context, dict):
                        root_cause = str(block_context.get("summary") or "").strip()
                    summary = (
                        "code_executor fallback is blocked because bio_tools input preparation failed. "
                        "Retry bio_tools with valid input_file or sequence_text."
                    )
                    if root_cause:
                        summary = f"{summary} Root cause: {root_cause}"
                    payload: Dict[str, Any] = {
                        "success": False,
                        "tool": "code_executor",
                        "error": summary,
                        "summary": summary,
                        "blocked_reason": "bio_tools_input_preparation_failed",
                        "error_code": "bio_tools_input_preparation_failed",
                    }
                    if isinstance(block_context, dict):
                        payload["bio_tools_block_context"] = block_context
                    return payload

                def _build_sequence_input_blocked_payload(
                    block_context: Optional[Dict[str, Any]]
                ) -> Dict[str, Any]:
                    root_cause = ""
                    if isinstance(block_context, dict):
                        root_cause = str(block_context.get("summary") or "").strip()
                    summary = (
                        "code_executor fallback is blocked because sequence_fetch failed in input/download stage. "
                        "Retry sequence_fetch with valid accession input."
                    )
                    if root_cause:
                        summary = f"{summary} Root cause: {root_cause}"
                    payload: Dict[str, Any] = {
                        "success": False,
                        "tool": "code_executor",
                        "error": summary,
                        "summary": summary,
                        "blocked_reason": "sequence_fetch_failed_no_fallback",
                        "error_code": "sequence_fetch_failed_no_fallback",
                    }
                    if isinstance(block_context, dict):
                        payload["sequence_fetch_block_context"] = block_context
                    return payload

                # Wrapper for tool execution with plan_operation binding
                async def tool_wrapper(name: str, params: Dict[str, Any]) -> Any:
                    nonlocal deep_think_tool_order, deep_think_bg_category
                    nonlocal bio_failure_active, failed_tool_name
                    nonlocal help_seen_after_failure, retry_seen_after_help
                    safe_params = params if isinstance(params, dict) else {}

                    if name not in ("plan_operation", "verify_task"):
                        if name == "code_executor":
                            sequence_block_context = self.extra_context.get(sequence_input_block_key)
                            if isinstance(sequence_block_context, dict):
                                blocked_payload = _build_sequence_input_blocked_payload(sequence_block_context)
                                logger.warning(
                                    "[DeepThink] Blocked code_executor fallback due to sequence_fetch failure."
                                )
                                return blocked_payload

                            block_context = self.extra_context.get(bio_input_block_key)
                            if isinstance(block_context, dict):
                                blocked_payload = _build_bio_input_blocked_payload(block_context)
                                logger.warning(
                                    "[DeepThink] Blocked code_executor fallback due to bio_tools input preparation failure."
                                )
                                return blocked_payload

                        if (
                            name == "code_executor"
                            and bio_failure_active
                            and not (help_seen_after_failure and retry_seen_after_help)
                        ):
                            blocked_payload = _build_bio_recovery_blocked_payload()
                            logger.warning(
                                "[DeepThink] Blocked code_executor fallback before bio_tools recovery: failed_tool=%s",
                                failed_tool_name or "unknown",
                            )
                            return blocked_payload

                        if name == "phagescope":
                            action_name = str(safe_params.get("action") or "").strip().lower()
                            taskid_value = str(safe_params.get("taskid") or "").strip()
                            blocked_context = self.extra_context.get(
                                phagescope_taskid_block_key
                            )
                            if (
                                isinstance(blocked_context, dict)
                                and action_name
                                in {"save_all", "result", "quality", "task_detail", "task_log", "download"}
                                and taskid_value
                                and str(blocked_context.get("taskid") or "").strip()
                                == taskid_value
                            ):
                                summary = str(
                                    blocked_context.get("summary")
                                    or (
                                        "PhageScope call is blocked because the provided taskid alias "
                                        "is not a numeric remote taskid."
                                    )
                                ).strip()
                                return {
                                    "success": False,
                                    "tool": "phagescope",
                                    "error": summary,
                                    "summary": summary,
                                    "error_code": "invalid_taskid",
                                    "blocked_reason": "phagescope_invalid_taskid",
                                    "taskid": taskid_value,
                                }

                        deep_think_tool_order += 1
                        synthetic_action = LLMAction(
                            kind="tool_operation",
                            name=name,
                            parameters=safe_params,
                            order=max(1, deep_think_tool_order),
                            blocking=True,
                            metadata={"origin": "deep_think"},
                        )
                        step = await self._handle_tool_action(synthetic_action)

                        result, _details = _normalize_deep_think_tool_result(
                            step=step,
                            tool_name=name,
                            tool_params=safe_params,
                            iteration=deep_think_tool_order,
                        )

                        if name == "sequence_fetch":
                            result_success = result.get("success") is not False
                            if result_success:
                                self.extra_context.pop(sequence_input_block_key, None)
                            elif result.get("no_claude_fallback") is True:
                                blocked_summary = str(
                                    result.get("error")
                                    or "sequence_fetch failed."
                                ).strip()
                                self.extra_context[sequence_input_block_key] = {
                                    "summary": blocked_summary,
                                    "blocked_reason": "sequence_fetch_failed_no_fallback",
                                    "error_code": result.get("error_code"),
                                    "error_stage": result.get("error_stage"),
                                    "accessions": result.get("accessions"),
                                    "provider": result.get("provider"),
                                }

                        if name == "bio_tools":
                            operation_name = str(safe_params.get("operation") or "").strip().lower()
                            result_success = result.get("success") is not False
                            if result_success:
                                self.extra_context.pop(bio_input_block_key, None)
                            elif result.get("no_claude_fallback") is True:
                                blocked_summary = str(
                                    result.get("error")
                                    or "bio_tools input preparation failed."
                                ).strip()
                                self.extra_context[bio_input_block_key] = {
                                    "summary": blocked_summary,
                                    "blocked_reason": "bio_tools_input_preparation_failed",
                                    "error_code": result.get("error_code"),
                                    "error_stage": result.get("error_stage"),
                                    "tool_name": result.get("tool"),
                                    "operation": result.get("operation"),
                                }
                            if not bio_failure_active and operation_name != "help" and not result_success:
                                bio_failure_active = True
                                failed_tool_name = (
                                    str(safe_params.get("tool_name") or "").strip() or None
                                )
                                help_seen_after_failure = False
                                retry_seen_after_help = False
                            elif bio_failure_active and operation_name == "help":
                                help_seen_after_failure = True
                            elif (
                                bio_failure_active
                                and help_seen_after_failure
                                and operation_name != "help"
                            ):
                                retry_seen_after_help = True

                        if name == "phagescope":
                            action_name = str(safe_params.get("action") or "").strip().lower()
                            taskid_value = str(safe_params.get("taskid") or "").strip()
                            result_success = result.get("success") is not False
                            if result_success:
                                self.extra_context.pop(phagescope_taskid_block_key, None)
                            elif action_name in {
                                "save_all",
                                "result",
                                "quality",
                                "task_detail",
                                "task_log",
                                "download",
                            } and taskid_value:
                                error_code = str(result.get("error_code") or "").strip().lower()
                                message_text = str(result.get("error") or "").strip().lower()
                                if error_code == "invalid_taskid" or "numeric remote `taskid`" in message_text:
                                    blocked_summary = str(
                                        result.get("error")
                                        or (
                                            "Invalid PhageScope taskid alias. Use numeric remote taskid "
                                            "(for example 37468) or a mappable job id."
                                        )
                                    ).strip()
                                    self.extra_context[phagescope_taskid_block_key] = {
                                        "taskid": taskid_value,
                                        "summary": blocked_summary,
                                        "error_code": "invalid_taskid",
                                    }

                        # DeepThink PhageScope submit: register tracking job so
                        # the task status panel can show progress.
                        if (
                            name == "phagescope"
                            and str(safe_params.get("action") or "").strip().lower()
                            == "submit"
                            and result.get("success") is not False
                        ):
                            taskid = _extract_taskid_from_result(result)
                            if taskid:
                                try:
                                    tracking_id = f"act_{uuid4().hex}"
                                    modulelist_raw = safe_params.get("modulelist")
                                    module_items = (
                                        _normalize_modulelist_value(modulelist_raw)
                                        if modulelist_raw
                                        else None
                                    )
                                    plan_decomposition_jobs.create_job(
                                        plan_id=self.plan_session.plan_id,
                                        task_id=None,
                                        mode="phagescope_track",
                                        job_type="phagescope_track",
                                        params={
                                            "taskid": taskid,
                                            "session_id": self.session_id,
                                        },
                                        metadata={
                                            "session_id": self.session_id,
                                            "origin": "deep_think",
                                            "remote_taskid": taskid,
                                        },
                                        job_id=tracking_id,
                                    )
                                    create_action_run(
                                        run_id=tracking_id,
                                        session_id=self.session_id,
                                        user_message=f"[DeepThink] PhageScope submit (taskid={taskid})",
                                        mode="phagescope_track",
                                        plan_id=self.plan_session.plan_id,
                                        context={"origin": "deep_think"},
                                        history=[],
                                        structured_json=json.dumps(
                                            {
                                                "llm_reply": {
                                                    "message": f"PhageScope submit taskid={taskid}"
                                                },
                                                "actions": [
                                                    {
                                                        "kind": "tool_operation",
                                                        "name": "phagescope",
                                                        "parameters": safe_params,
                                                    }
                                                ],
                                            }
                                        ),
                                    )
                                    update_action_run(tracking_id, status="running")
                                    start_phagescope_track_job_thread(
                                        job_id=tracking_id,
                                        remote_taskid=str(taskid),
                                        modulelist=module_items,
                                        poll_interval=30.0,
                                        poll_timeout=172800.0,
                                        request_timeout=40.0,
                                    )
                                    logger.info(
                                        "[DeepThink] Registered PhageScope tracking job %s for taskid=%s",
                                        tracking_id,
                                        taskid,
                                    )
                                except Exception as track_exc:
                                    logger.warning(
                                        "[DeepThink] Failed to register PhageScope tracking: %s",
                                        track_exc,
                                    )

                        return result

                    # verify_task: route through task_operation handler
                    if name == "verify_task":
                        deep_think_tool_order += 1
                        synthetic_action = LLMAction(
                            kind="task_operation",
                            name="verify_task",
                            parameters=safe_params,
                            order=max(1, deep_think_tool_order),
                            blocking=True,
                            metadata={"origin": "deep_think"},
                        )
                        step = self._handle_task_action(synthetic_action)
                        if inspect.isawaitable(step):
                            step = await step

                        result, _details = _normalize_deep_think_tool_result(
                            step=step,
                            tool_name=name,
                            tool_params=safe_params,
                            iteration=deep_think_tool_order,
                        )
                        return result

                    result = await execute_tool(name, **safe_params)

                    # Special handling: bind Plan to session after successful creation
                    if name == "plan_operation" and isinstance(result, dict):
                        if result.get("success") and result.get("operation") == "create":
                            plan_id = result.get("plan_id")
                            if plan_id:
                                existing_plan_id = self.plan_session.plan_id
                                if not _should_bind_created_plan(
                                    existing_plan_id=existing_plan_id,
                                    plan_request_mode=routing_decision.plan_request_mode,
                                ):
                                    logger.warning(
                                        "[DeepThink] plan_operation create returned plan %s "
                                        "but session already bound to plan %s; "
                                        "keeping original binding",
                                        plan_id,
                                        existing_plan_id,
                                    )
                                    result["binding_skipped"] = True
                                    result["existing_plan_id"] = existing_plan_id
                                else:
                                    try:
                                        self.plan_session.bind(plan_id)
                                        if (
                                            existing_plan_id is not None
                                            and existing_plan_id != plan_id
                                        ):
                                            logger.info(
                                                "[DeepThink] Rebound session from plan %s to new plan %s "
                                                "for explicit create_new request",
                                                existing_plan_id,
                                                plan_id,
                                            )
                                            result["rebound_from_plan_id"] = existing_plan_id
                                        self._refresh_plan_tree(force_reload=True)
                                        self.extra_context["plan_id"] = plan_id
                                        self._dirty = True

                                        if (
                                            deep_think_job_created
                                            and deep_think_job_id
                                        ):
                                            try:
                                                plan_id_int = int(plan_id)
                                            except (TypeError, ValueError):
                                                plan_id_int = None
                                            if plan_id_int is not None:
                                                plan_decomposition_jobs.attach_plan(
                                                    deep_think_job_id, plan_id_int
                                                )

                                        # CRITICAL: Also update the database session record
                                        # so that frontend can fetch the new plan_id
                                        if self.session_id:
                                            _set_session_plan_id(self.session_id, plan_id)
                                            logger.info(
                                                "[DeepThink] Updated database session %s with plan_id=%s",
                                                self.session_id,
                                                plan_id,
                                            )

                                        # CRITICAL: Trigger automatic task decomposition
                                        # This ensures DeepThink-created plans get the same
                                        # multi-level decomposition as regular plans
                                        session_ctx = {
                                            "user_message": effective_user_message,
                                            "request_tier": routing_decision.request_tier,
                                            "chat_history": self.history,
                                            "chat_history_max_messages": self.max_history_messages,
                                            "recent_tool_results": self.extra_context.get(
                                                "recent_tool_results", []
                                            ),
                                        }
                                        decompose_result = await asyncio.to_thread(
                                            self._auto_decompose_plan,
                                            plan_id,
                                            wait_for_completion=False,
                                            session_context=session_ctx,
                                            after_success=lambda: self._run_created_plan_auto_review_sync(
                                                plan_id
                                            ),
                                        )
                                        auto_review_mode = "not_scheduled"
                                        if decompose_result:
                                            if decompose_result.get("result") is not None:
                                                summary = decompose_result["result"]
                                                logger.info(
                                                    "[DeepThink] Auto-decomposition completed for plan %s",
                                                    plan_id,
                                                )
                                                result["decomposition_completed"] = True
                                                result["decomposition_created"] = len(
                                                    summary.created_tasks
                                                )
                                                result["decomposition_stats"] = summary.stats
                                                result["decomposition_note"] = (
                                                    "Automatic task decomposition completed before review."
                                                )
                                                if self._start_background_created_plan_auto_review(
                                                    plan_id
                                                ):
                                                    result["auto_review"] = {
                                                        "status": "scheduled",
                                                        "mode": "background",
                                                    }
                                                    auto_review_mode = "background"
                                            elif decompose_result.get("job") is not None:
                                                decompose_job = decompose_result.get("job")
                                                decompose_job_id = getattr(
                                                    decompose_job, "job_id", None
                                                )
                                                logger.info(
                                                    "[DeepThink] Auto-decomposition submitted for plan %s",
                                                    plan_id,
                                                )
                                                result["decomposition_triggered"] = True
                                                result["decomposition_note"] = (
                                                    "Automatic task decomposition has been submitted for background execution."
                                                )
                                                result["auto_review"] = {
                                                    "status": "scheduled",
                                                    "mode": "after_decomposition",
                                                    "decomposition_job_id": decompose_job_id,
                                                }
                                                auto_review_mode = (
                                                    "after_decomposition"
                                                )
                                            elif self._start_background_created_plan_auto_review(
                                                plan_id
                                            ):
                                                result["auto_review"] = {
                                                    "status": "scheduled",
                                                    "mode": "background",
                                                }
                                                auto_review_mode = "background"
                                        elif self._start_background_created_plan_auto_review(
                                            plan_id
                                        ):
                                            result["auto_review"] = {
                                                "status": "scheduled",
                                                "mode": "background",
                                            }
                                            auto_review_mode = "background"

                                        deep_think_bg_category = "task_creation"

                                        logger.info(
                                            "[DeepThink] Auto-bound plan %s to session "
                                            "(decomposition dispatched to background, "
                                            "auto-review=%s, auto-optimize skipped)",
                                            plan_id,
                                            auto_review_mode,
                                        )
                                    except Exception as bind_err:
                                        logger.warning(
                                            "[DeepThink] Failed to bind plan %s: %s",
                                            plan_id,
                                            bind_err,
                                        )

                    return result

                # Instantiate DeepThinkAgent with streaming callbacks. Use the
                # compatibility shim override when available to preserve legacy
                # monkeypatch behavior in integrations/tests.
                dt_agent_cls = DeepThinkAgent
                try:  # pragma: no cover - compatibility bridge
                    from app.routers import chat_routes as compat_chat_routes

                    compat_candidate = getattr(
                        compat_chat_routes, "DeepThinkAgent", None
                    )
                    if inspect.isclass(compat_candidate):
                        dt_agent_cls = compat_candidate
                except Exception:
                    pass

                async def on_artifact(meta: Dict[str, Any]) -> None:
                    artifact_meta = dict(meta or {})
                    gallery_item = build_artifact_gallery_item(
                        artifact_meta.get("path"),
                        session_id=self.session_id,
                        source_tool=artifact_meta.get("source_tool"),
                        tracking_id=deep_think_job_id,
                        created_at=None,
                        display_name=artifact_meta.get("display_name"),
                        origin=artifact_meta.get("origin"),
                    )
                    if gallery_item is not None:
                        current_turn_artifact_gallery[:] = merge_artifact_gallery(
                            current_turn_artifact_gallery,
                            [gallery_item],
                        )
                        update_recent_image_artifacts(self.extra_context, [gallery_item])
                        artifact_meta = {
                            **artifact_meta,
                            "path": gallery_item["path"],
                            "display_name": gallery_item["display_name"],
                            "mime_family": gallery_item["mime_family"],
                            "origin": gallery_item["origin"],
                            "tracking_id": gallery_item["tracking_id"],
                        }
                    await queue.put({"type": "artifact", **artifact_meta})

                async def on_reasoning_delta(iteration: int, delta: str) -> None:
                    if not thinking_visible:
                        return
                    await queue.put({
                        "type": "reasoning_delta",
                        "iteration": iteration,
                        "delta": delta,
                    })

                async def on_steer_ack(text: str, iteration: int) -> None:
                    await queue.put({
                        "type": "steer_ack",
                        "message": text[:500],
                        "iteration": iteration,
                    })

                dt_agent_kwargs: Dict[str, Any] = {
                    "llm_client": self.llm_service,
                    "cancel_event": cancel_event,
                    "available_tools": route_profile.available_tools,
                    "tool_executor": tool_wrapper,
                    "max_iterations": route_profile.max_iterations,
                    "tool_timeout": 120,
                    "on_thinking": on_thinking,
                    "on_thinking_delta": on_thinking_delta,
                    "on_final_delta": on_final_delta,
                    "on_tool_start": on_tool_start,
                    "on_tool_result": on_tool_result,
                    "on_tool_progress": on_tool_progress,
                    "on_artifact": on_artifact,
                    "enable_thinking": self._resolve_thinking_enabled(),
                    "thinking_budget": route_profile.thinking_budget,
                    "on_reasoning_delta": on_reasoning_delta,
                    "steer_drain": steer_drain,
                    "on_steer_ack": on_steer_ack,
                }
                try:
                    ctor_params = inspect.signature(dt_agent_cls.__init__).parameters
                except Exception:  # pragma: no cover - defensive
                    ctor_params = {}
                if "request_profile" in ctor_params:
                    plan_tree = getattr(self, "plan_tree", None)
                    dt_agent_kwargs["request_profile"] = {
                        **route_profile.prompt_metadata(),
                        **routing_decision.metadata(),
                        "current_plan_id": self.plan_session.plan_id,
                        "current_plan_title": plan_tree.title if plan_tree else None,
                    }
                dt_agent = dt_agent_cls(**dt_agent_kwargs)

                await _emit_progress_status(
                    phase="planning",
                    label=_progress_label_from_phase("planning"),
                    iteration=0,
                    status="active",
                )

                if deep_think_job_created and deep_think_job_id:
                    control_available = plan_decomposition_jobs.register_runtime_controller(
                        deep_think_job_id,
                        JobRuntimeController(
                            pause=dt_agent.pause,
                            resume=dt_agent.resume,
                            skip_step=dt_agent.skip_step,
                        ),
                    )
                    await queue.put(
                        {
                            "type": "control_ack",
                            "job_id": deep_think_job_id,
                            "available": control_available,
                            "paused": False,
                        }
                    )

                # Build context including chat history.
                think_context = {
                    **self.extra_context,
                    "chat_history": self.history,
                    "chat_history_max_messages": self.max_history_messages,
                    "session_id": self.session_id,
                    **routing_decision.metadata(),
                    **route_profile.prompt_metadata(),
                }
                continuation_summary = _build_brief_execute_continuation_summary(
                    self,
                    routing_decision,
                )
                if continuation_summary:
                    think_context["continuation_summary"] = continuation_summary

                # Run think
                result = await dt_agent.think(effective_user_message, think_context)
                if cancel_event is not None and cancel_event.is_set():
                    if deep_think_job_created and deep_think_job_id:
                        plan_decomposition_jobs.mark_failure(
                            deep_think_job_id,
                            "cancelled",
                            result={"cancelled": True},
                        )
                    await queue.put({"type": "error", "error": "Run cancelled."})
                else:
                    if deep_think_job_created and deep_think_job_id:
                        plan_decomposition_jobs.mark_success(
                            deep_think_job_id,
                            result={
                                "final_answer": str(result.final_answer or "")[:2000],
                                "total_iterations": result.total_iterations,
                                "tools_used": result.tools_used,
                                "confidence": result.confidence,
                            },
                            stats={
                                "iterations": result.total_iterations,
                                "tool_count": len(result.tools_used),
                            },
                        )
                    await queue.put(
                        {
                            "type": "result",
                            "result": result,
                            "bg_category": deep_think_bg_category,
                            "job_id": deep_think_job_id if deep_think_job_created else None,
                        }
                    )
            except Exception as e:
                logger.exception("Deep think execution failed")
                if deep_think_job_created and deep_think_job_id:
                    plan_decomposition_jobs.mark_failure(
                        deep_think_job_id,
                        str(e),
                        result={"error": str(e)},
                    )
                await queue.put({"type": "error", "error": str(e)})
            finally:
                if relay_task is not None:
                    relay_task.cancel()
                    await asyncio.gather(relay_task, return_exceptions=True)
                if deep_think_job_created and deep_think_job_id:
                    plan_decomposition_jobs.unregister_runtime_controller(deep_think_job_id)
                    if deep_think_job_queue is not None:
                        plan_decomposition_jobs.unregister_subscriber(
                            deep_think_job_id, deep_think_job_queue
                        )
                if job_token is not None:
                    reset_current_job(job_token)
                await queue.put(None)  # Signal end

        # Start agent in background
        asyncio.create_task(run_agent())

        async def _through_sink(payload: Dict[str, Any]) -> str:
            if event_sink is not None:
                await event_sink(payload)
            return _sse_message(payload)

        # Consume queue
        while True:
            item = await queue.get()
            if item is None:
                break

            event_type = item.get("type")
            if event_type in {
                "thinking_step",
                "thinking_delta",
                "reasoning_delta",
                "progress_status",
                "delta",
                "control_ack",
                "tool_output",
                "artifact",
                "steer_ack",
            }:
                out = await _through_sink(item)
                yield out
            elif event_type == "error":
                err_payload = {"type": "error", "message": item["error"]}
                out = await _through_sink(err_payload)
                yield out
            elif event_type == "result":
                # Final result, yield as standard chat message
                res: DeepThinkResult = item["result"]
                grounded_answer = _apply_grounded_local_answer(
                    self,
                    res.final_answer,
                    routing_decision,
                )
                if grounded_answer != str(res.final_answer or "").strip():
                    res = replace(res, final_answer=grounded_answer)
                result_job_id = (
                    str(item.get("job_id"))
                    if isinstance(item.get("job_id"), str) and item.get("job_id")
                    else None
                )

                # Construct final content for display and saving
                final_content_parts = []
                # Thinking Summary removed per user request
                if res.final_answer:
                    final_content_parts.append(res.final_answer)

                full_response = "\n\n".join(final_content_parts)

                # 💾 Save Deep Think response to database
                if self.session_id and full_response:
                    try:
                        _persist_runtime_context(self)
                        plan_tree = getattr(self, "plan_tree", None)
                        structured_plan_meta = _structured_plan_metadata_from_result(res)
                        plan_runtime_meta = _plan_runtime_metadata(plan_tree)
                        resolved_plan_id = res.structured_plan_plan_id or self.plan_session.plan_id
                        plan_title = res.structured_plan_title or (plan_tree.title if plan_tree else None)
                        metadata_payload: Dict[str, Any] = {
                            "plan_id": resolved_plan_id,
                            "plan_title": plan_title,
                            "deep_think": True,
                            "iterations": res.total_iterations,
                            "tools_used": res.tools_used,
                            "confidence": res.confidence,
                            "tool_failures": res.tool_failures,
                            "search_verified": res.search_verified,
                            "fallback_used": res.fallback_used,
                            **routing_decision.metadata(),
                            **structured_plan_meta,
                            **plan_runtime_meta,
                        }
                        if current_turn_artifact_gallery:
                            metadata_payload["artifact_gallery"] = merge_artifact_gallery(
                                None,
                                current_turn_artifact_gallery,
                            )
                        if thinking_visible:
                            metadata_payload["thinking_process"] = {
                                "status": "completed",
                                "total_iterations": res.total_iterations,
                                "summary": res.thinking_summary,
                                "steps": [
                                    {
                                        **build_user_visible_step(
                                            s,
                                            language=reasoning_language,
                                            preserve_thought=True,
                                        ),
                                        "status": "done"
                                        if s.status == "done"
                                        else "completed",
                                    }
                                    for s in res.thinking_steps
                                ],
                            }
                        if result_job_id:
                            metadata_payload["deep_think_job_id"] = result_job_id
                        _save_chat_message(
                            self.session_id,
                            "assistant",
                            full_response,
                            metadata=metadata_payload,
                        )
                        logger.info(
                            "[CHAT][DEEP_THINK] Response saved to database for session=%s",
                            self.session_id,
                        )
                        if res.structured_plan_required:
                            logger.info(
                                "[CHAT][DEEP_THINK][PLAN] session=%s required=%s mode=%s state=%s satisfied=%s plan_id=%s operation=%s message=%s",
                                self.session_id,
                                res.structured_plan_required,
                                routing_decision.plan_request_mode,
                                res.structured_plan_state,
                                res.structured_plan_satisfied,
                                resolved_plan_id,
                                res.structured_plan_operation,
                                res.structured_plan_message,
                            )
                    except Exception as save_err:
                        logger.warning(
                            "[CHAT][DEEP_THINK] Failed to save response: %s",
                            save_err,
                        )

                # Note: final_answer was already streamed via on_final_delta callback
                # No need to yield it again here to avoid duplication
                bg_category = item.get("bg_category")
                plan_tree = getattr(self, "plan_tree", None)
                structured_plan_meta = _structured_plan_metadata_from_result(res)
                plan_runtime_meta = _plan_runtime_metadata(plan_tree)
                resolved_plan_id = res.structured_plan_plan_id or self.plan_session.plan_id
                plan_title = res.structured_plan_title or (plan_tree.title if plan_tree else None)
                final_metadata: Dict[str, Any] = {
                    "plan_id": resolved_plan_id,  # Include plan_id so frontend can update
                    "plan_title": plan_title,
                    "deep_think": True,
                    "tool_failures": res.tool_failures,
                    "search_verified": res.search_verified,
                    "fallback_used": res.fallback_used,
                    **routing_decision.metadata(),
                    **structured_plan_meta,
                    **plan_runtime_meta,
                }
                if current_turn_artifact_gallery:
                    final_metadata["artifact_gallery"] = merge_artifact_gallery(
                        None,
                        current_turn_artifact_gallery,
                    )
                if bg_category:
                    final_metadata["background_category"] = bg_category
                if result_job_id:
                    final_metadata["deep_think_job_id"] = result_job_id
                payload = {
                    "llm_reply": {"message": res.final_answer},
                    "actions": [],
                    "metadata": final_metadata,
                }
                final_payload = {"type": "final", "payload": payload}
                out = await _through_sink(final_payload)
                yield out

    async def process_deep_think_stream(self, user_message: str) -> AsyncIterator[str]:
        """Backward-compatible helper that force-enables the DeepThink path."""
        if not isinstance(getattr(self, "extra_context", None), dict):
            self.extra_context = {}
        previous = self.extra_context.get("deep_think_enabled")
        self.extra_context["deep_think_enabled"] = True
        try:
            async for chunk in self.process_unified_stream(user_message):
                yield chunk
        finally:
            if previous is None:
                self.extra_context.pop("deep_think_enabled", None)
            else:
                self.extra_context["deep_think_enabled"] = previous

    async def stream_simple_chat(
        self,
        user_message: str,
        *,
        routing_decision: Optional[RequestRoutingDecision] = None,
        route_profile: Optional[RequestTierProfile] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> AsyncIterator[str]:
        """Lightweight chat path with thinking enabled but no tools.

        Used for conversations without plan context where tool access
        is not needed. Thinking budget is smaller to keep latency low.
        """

        if routing_decision is None or route_profile is None:
            routing_decision, route_profile = self._resolve_request_routing(user_message)

        prompt = _build_simple_stream_chat_prompt_fn(self, user_message)
        model_override = self.extra_context.get("default_base_model")
        enable_thinking = self._resolve_thinking_enabled()
        thinking_budget = route_profile.thinking_budget
        visible_reasoning = summarize_simple_chat_reasoning(user_message)

        async def _through_sink(payload: Dict[str, Any]) -> str:
            if event_sink is not None:
                await event_sink(payload)
            return _sse_message(payload)

        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        content_parts: list[str] = []
        step_started_at = datetime.now(timezone.utc).replace(microsecond=0)

        yield await _through_sink(
            {
                "type": "thinking_step",
                "step": {
                    "iteration": 1,
                    "thought": "",
                    "display_text": visible_reasoning,
                    "kind": "summary",
                    "action": None,
                    "action_result": None,
                    "status": "thinking",
                    "timestamp": step_started_at.isoformat().replace("+00:00", "Z"),
                    "started_at": step_started_at.isoformat().replace("+00:00", "Z"),
                    "finished_at": None,
                    "self_correction": None,
                },
            }
        )

        async def _run_stream() -> None:
            try:
                async for delta in self.llm_service.stream_chat_async(
                    prompt, force_real=True, model=model_override,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                ):
                    content_parts.append(delta)
                    await queue.put(
                        await _through_sink({"type": "delta", "content": delta})
                    )
            except Exception as exc:
                logger.error("Simple chat stream failed: %s", exc)
                await queue.put(
                    await _through_sink({
                        "type": "error",
                        "message": f"Stream failed: {exc}",
                    })
                )
            finally:
                await queue.put(None)

        asyncio.create_task(_run_stream())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

        full_response = "".join(content_parts)
        display_text = _coerce_plain_text_chat_response_fn(full_response)
        display_text = _rewrite_plain_chat_execution_claims_fn(display_text)
        thinking_process = {
            "status": "completed",
            "total_iterations": 1,
            "summary": visible_reasoning,
            "steps": [
                {
                    "iteration": 1,
                    "thought": "",
                    "display_text": visible_reasoning,
                    "kind": "summary",
                    "action": None,
                    "action_result": None,
                    "status": "done",
                    "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "started_at": step_started_at.isoformat().replace("+00:00", "Z"),
                    "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "self_correction": None,
                }
            ],
        }

        if self.session_id and display_text:
            try:
                _persist_runtime_context(self)
                from .session_helpers import _save_chat_message
                save_meta: Dict[str, Any] = {
                    "plan_id": self.plan_session.plan_id,
                    "thinking_enabled": enable_thinking,
                    "unified_stream": True,
                    "status": "completed",
                    "analysis_text": display_text,
                    "final_summary": display_text,
                    **routing_decision.metadata(),
                }
                if thinking_process is not None:
                    save_meta["thinking_process"] = thinking_process
                _save_chat_message(
                    self.session_id,
                    "assistant",
                    display_text,
                    metadata=save_meta,
                )
            except Exception as save_err:
                logger.warning("[SIMPLE_CHAT] Failed to save response: %s", save_err)

        plan_tree = getattr(self, "plan_tree", None)
        plan_title = plan_tree.title if plan_tree else None
        meta: Dict[str, Any] = {
            "plan_id": self.plan_session.plan_id,
            "plan_title": plan_title,
            "status": "completed",
            "unified_stream": True,
            "analysis_text": display_text,
            "final_summary": display_text,
            **routing_decision.metadata(),
        }
        if thinking_process is not None:
            meta["thinking_process"] = thinking_process
        payload = {
            "llm_reply": {"message": display_text},
            "response": display_text,
            "actions": [],
            "metadata": meta,
        }
        yield await _through_sink({"type": "final", "payload": payload})

    def _resolve_thinking_enabled(self) -> bool:
        settings = get_settings()
        return getattr(settings, "thinking_enabled", True)

    def _resolve_thinking_budget(self) -> int:
        settings = get_settings()
        return int(getattr(settings, "thinking_budget", 10000))

    def _resolve_thinking_budget_simple(self) -> int:
        settings = get_settings()
        return min(int(getattr(settings, "thinking_budget_simple", 2000)), 800)

    def _resolve_request_routing(
        self,
        user_message: str,
    ) -> tuple[RequestRoutingDecision, RequestTierProfile]:
        settings = get_settings()
        decision = resolve_request_routing(
            message=user_message,
            history=self.history,
            context=self.extra_context,
            plan_id=self.plan_session.plan_id,
            current_task_id=self.extra_context.get("current_task_id"),
        )
        profile = build_request_tier_profile(
            decision,
            default_thinking_budget=int(getattr(settings, "thinking_budget", 10000)),
            simple_thinking_budget=int(
                getattr(settings, "thinking_budget_simple", 2000)
            ),
            default_max_iterations=_resolve_deep_think_max_iterations(),
        )
        return decision, profile

    def _update_routing_context(self, routing_decision: RequestRoutingDecision) -> None:
        self.extra_context.update(
            {
                "request_tier": routing_decision.request_tier,
                "request_route_mode": routing_decision.request_route_mode,
                "intent_type": routing_decision.intent_type,
                "capability_floor": routing_decision.capability_floor,
                "simple_channel_allowed": routing_decision.simple_channel_allowed,
                "subject_resolution": dict(routing_decision.subject_resolution),
                "brevity_hint": routing_decision.brevity_hint,
                "requires_structured_plan": routing_decision.requires_structured_plan,
                "plan_request_mode": routing_decision.plan_request_mode,
                "requires_plan_review": routing_decision.requires_plan_review,
                "requires_plan_optimize": routing_decision.requires_plan_optimize,
                "current_user_turn_index": _current_user_turn_index_from_history(self.history),
            }
        )

    async def _invoke_llm(self, user_message: str) -> LLMStructuredResponse:
        self._current_user_message = user_message
        prompt = self._build_prompt(user_message)
        model_override = self.extra_context.get("default_base_model")
        raw = await self.llm_service.chat_async(
            prompt, force_real=True, model=model_override
        )
        cleaned = self._strip_code_fence(raw)
        return LLMStructuredResponse.model_validate_json(cleaned)

    def _build_prompt(self, user_message):
        return _build_prompt_fn(self, user_message)

    def _format_memories(self, memories):
        return _format_memories_fn(memories)

    def _compose_plan_status(self, plan_bound):
        return _compose_plan_status_fn(self, plan_bound)

    def _compose_plan_catalog(self, plan_bound):
        return _compose_plan_catalog_fn(self, plan_bound)

    def _compose_action_catalog(self, plan_bound):
        return _compose_action_catalog_fn(self, plan_bound)

    def _compose_guidelines(self, plan_bound):
        return _compose_guidelines_fn(self, plan_bound)

    _get_structured_agent_prompts = staticmethod(_get_structured_agent_prompts_fn)

    @staticmethod
    def _extract_tool_name(action_line: str) -> Optional[str]:
        match = re.search(r"-\s*tool_operation:\s*([^\s(]+)", action_line)
        if match:
            return match.group(1).strip()
        return None

    def _resolve_job_meta(self):
        return _resolve_job_meta_fn(self)

    def _log_action_event(self, action, *, status, success, message, parameters, details):
        return _log_action_event_fn(self, action, status=status, success=success, message=message, parameters=parameters, details=details)

    _truncate_summary_text = staticmethod(_truncate_summary_text_fn)

    def _build_actions_summary(self, steps):
        return _build_actions_summary_fn(self, steps)

    def _append_summary_to_reply(self, reply, summary):
        return _append_summary_to_reply_fn(self, reply, summary)

    def _format_history(self):
        return _format_history_fn(self)

    _strip_code_fence = staticmethod(_strip_code_fence_fn)

    async def _execute_action(self, action: LLMAction) -> AgentStep:
        logger.info(
            "[CHAT][ACTION] session=%s plan=%s executing %s/%s params=%s",
            self.session_id,
            self.plan_session.plan_id,
            action.kind,
            action.name,
            action.parameters,
        )
        self._log_action_event(
            action,
            status="running",
            success=None,
            message="Action execution started.",
            parameters=action.parameters,
            details=None,
        )
        log_job_event(
            "info",
            "Preparing to execute the action.",
            {
                "kind": action.kind,
                "name": action.name,
                "order": action.order,
                "blocking": action.blocking,
                "parameters": action.parameters,
            },
        )
        handler = {
            "plan_operation": self._handle_plan_action,
            "task_operation": self._handle_task_action,
            "context_request": self._handle_context_request,
            "system_operation": self._handle_system_action,
            "tool_operation": self._handle_tool_action,
        }.get(action.kind, self._handle_unknown_action)
        try:
            result = handler(action)
            step = await result if inspect.isawaitable(result) else result
        except Exception as exc:
            log_job_event(
                "error",
                "An exception occurred while executing the action.",
                {
                    "kind": action.kind,
                    "name": action.name,
                    "error": str(exc),
                },
            )
            self._log_action_event(
                action,
                status="failed",
                success=False,
                message=str(exc),
                parameters=action.parameters,
                details={"error": str(exc), "exception": type(exc).__name__},
            )
            raise

        self._log_action_event(
            action,
            status="completed" if step.success else "failed",
            success=step.success,
            message=step.message,
            parameters=action.parameters,
            details=step.details,
        )
        log_job_event(
            "success" if step.success else "error",
            "Action execution completed.",
            {
                "kind": action.kind,
                "name": action.name,
                "success": step.success,
                "message": step.message,
                "details": step.details,
            },
        )
        logger.info(
            "[CHAT][ACTION] session=%s plan=%s finished %s/%s success=%s message=%s",
            self.session_id,
            self.plan_session.plan_id,
            action.kind,
            action.name,
            step.success,
            step.message,
        )
        return step

    async def _handle_tool_action(self, action):
        # Keep legacy monkeypatch points from app.routers.chat_routes wired into
        # the split action_handlers module.
        try:  # pragma: no cover - compatibility bridge
            from app.routers import chat_routes as compat_chat_routes
            from . import action_handlers as _action_handlers_module

            for name in (
                "get_tool_policy",
                "is_tool_allowed",
                "execute_tool",
                "get_current_job",
            ):
                candidate = getattr(compat_chat_routes, name, None)
                if candidate is not None:
                    setattr(_action_handlers_module, name, candidate)
        except Exception:
            pass
        return await _handle_tool_action_fn(self, action)

    async def _handle_plan_action(self, action):
        return await _handle_plan_action_fn(self, action)

    def _handle_task_action(self, action):
        return _handle_task_action_fn(self, action)

    def _handle_context_request(self, action):
        return _handle_context_request_fn(self, action)

    def _handle_system_action(self, action):
        return _handle_system_action_fn(self, action)

    def _handle_unknown_action(self, action):
        return _handle_unknown_action_fn(self, action)

    def _build_suggestions(self, structured, steps):
        return _build_suggestions_fn(self, structured, steps)

    def _require_plan_bound(self):
        return _require_plan_bound_fn(self)

    def _refresh_plan_tree(self, force_reload=True):
        return _refresh_plan_tree_fn(self, force_reload=force_reload)

    _coerce_int = staticmethod(_coerce_int_fn)

    def _run_created_plan_auto_review_sync(
        self,
        plan_id: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            review_result = asyncio.run(
                execute_tool(
                    "plan_operation",
                    operation="review",
                    plan_id=plan_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "[DeepThink] Auto-review failed for plan %s: %s",
                plan_id,
                exc,
            )
            return None

        if not isinstance(review_result, dict):
            logger.warning(
                "[DeepThink] Auto-review for plan %s returned non-dict payload: %r",
                plan_id,
                review_result,
            )
            return None
        if not review_result.get("success"):
            logger.warning(
                "[DeepThink] Auto-review for plan %s did not succeed: %s",
                plan_id,
                review_result.get("error") or review_result,
            )
            return None

        logger.info(
            "[DeepThink] Auto-reviewed plan %s after create (status=%s, rubric_score=%s)",
            plan_id,
            review_result.get("status"),
            review_result.get("rubric_score"),
        )
        return dict(review_result)

    def _start_background_created_plan_auto_review(self, plan_id: int) -> bool:
        try:
            thread = threading.Thread(
                target=self._run_created_plan_auto_review_sync,
                args=(plan_id,),
                name=f"deepthink-plan-review-{plan_id}",
                daemon=True,
            )
            thread.start()
        except Exception as exc:
            logger.warning(
                "[DeepThink] Failed to start background auto-review for plan %s: %s",
                plan_id,
                exc,
            )
            return False
        return True

    def _auto_decompose_plan(
        self,
        plan_id,
        *,
        wait_for_completion=False,
        session_context=None,
        after_success=None,
    ):
        return _auto_decompose_plan_fn(
            self,
            plan_id,
            wait_for_completion=wait_for_completion,
            session_context=session_context,
            after_success=after_success,
        )

    def _persist_if_dirty(self):
        return _persist_if_dirty_fn(self)
