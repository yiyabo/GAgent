"""Structured chat agent core orchestration logic."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from app.config.executor_config import get_executor_settings
from app.repository.chat_action_runs import create_action_run, fetch_action_run, update_action_run
from app.repository.plan_storage import append_action_log_entry, update_decomposition_job_status
from app.llm import LLMClient
from app.services.llm.llm_service import LLMProviderError
from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX, get_settings
from app.services.llm.decomposer_service import PlanDecomposerLLMService
from app.services.llm.llm_service import LLMService, get_llm_service
from app.services.llm.structured_response import (
    LLMAction,
    LLMStructuredResponse,
    build_repair_prompt,
    fallback_reply_response,
    parse_structured_response,
    schema_as_json,
)
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
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor, PlanExecutorLLMService
from app.services.plans.plan_models import PlanNode
from app.services.plans.plan_session import PlanSession
from app.services.plans.task_verification import TaskVerificationService
from app.services.session_title_service import SessionNotFoundError
from app.services.upload_storage import delete_session_storage
from app.services.deep_think_agent import (
    build_user_visible_step,
    detect_reasoning_language,
    DeepThinkAgent,
    TaskExecutionContext,
    ThinkingStep,
    DeepThinkResult,
    summarize_tool_step_display,
    summarize_simple_chat_reasoning,
)
from tool_box import execute_tool


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
    _persist_runtime_context,
    handle_context_request as _handle_context_request_fn,
    handle_plan_action as _handle_plan_action_fn,
    handle_system_action as _handle_system_action_fn,
    handle_task_action as _handle_task_action_fn,
    handle_task_action_async as _handle_task_action_async_fn,
    handle_tool_action as _handle_tool_action_fn,
    handle_unknown_action as _handle_unknown_action_fn,
    maybe_synthesize_phagescope_saveall_analysis as _maybe_synthesize_phagescope_saveall_analysis_fn,
)
from .code_executor_helpers import (
    compose_code_executor_atomic_task_prompt as _compose_code_executor_atomic_task_prompt_fn,
    collect_completed_task_outputs as _collect_completed_task_outputs_fn,
    extract_task_artifact_paths as _extract_task_artifact_paths_fn,
    normalize_csv_arg as _normalize_csv_arg_fn,
    resolve_action_placeholders as _resolve_action_placeholders_fn,
    resolve_code_executor_task_context as _resolve_code_executor_task_context_fn,
    _explicitly_requests_completed_task_rerun as _explicitly_requests_completed_task_rerun_fn,
    resolve_placeholders_in_value as _resolve_placeholders_in_value_fn,
    resolve_previous_path as _resolve_previous_path_fn,
    summarize_amem_experiences_for_cc as _summarize_amem_experiences_for_cc_fn,
)
from .code_executor_bridge import _code_executor_job_stream_loggers
from .guardrail_handlers import (
    apply_completion_claim_guardrail as _apply_completion_claim_guardrail_fn,
    apply_experiment_fallback as _apply_experiment_fallback_fn,
    apply_phagescope_fallback as _apply_phagescope_fallback_fn,
    apply_plan_first_guardrail as _apply_plan_first_guardrail_fn,
    apply_task_execution_followthrough_guardrail as _apply_task_execution_followthrough_guardrail_fn,
    first_executable_atomic_descendant as _first_executable_atomic_descendant_fn,
    infer_plan_seed_message as _infer_plan_seed_message_fn,
    match_atomic_task_by_keywords as _match_atomic_task_by_keywords_fn,
    resolve_explicit_task_scope_target as _resolve_explicit_task_scope_target_fn,
    resolve_all_explicit_task_scope_targets as _resolve_all_explicit_task_scope_targets_fn,
    resolve_full_plan_executable_targets as _resolve_full_plan_executable_targets_fn,
    resolve_followthrough_target_task_id as _resolve_followthrough_target_task_id_fn,
    classify_explicit_scope_none_reason as _classify_explicit_scope_none_reason_fn,
)
from .guardrails import (
    explicit_manuscript_request as _explicit_manuscript_request_fn,
    extract_declared_absolute_paths as _extract_declared_absolute_paths_fn,
    extract_task_id_from_text as _extract_task_id_from_text_fn,
    is_generic_plan_confirmation as _is_generic_plan_confirmation_fn,
    is_status_query_only as _is_status_query_only_fn,
    is_task_executable_status as _is_task_executable_status_fn,
    local_manuscript_assembly_request as _local_manuscript_assembly_request_fn,
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
)
from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    subject_identity_matches,
)
from .phagescope_rewrite import (
    _NON_PHAGESCOPE_TABULAR_FILE_EXTS,
    _PHAGESCOPE_DATASET_STRATEGY_MARKERS,
    _PHAGESCOPE_DATASET_UNDERSTANDING_MARKERS,
    _PLAN_CREATE_NEGATION_MARKERS,
    _directory_positively_lacks_phagescope_meta_data,
    _extract_phagescope_data_dir_from_context,
    _is_phagescope_dataset_understanding_request,
    _normalize_phagescope_data_dir,
    _path_is_generic_tabular_file,
    _rewrite_phagescope_dataset_understanding_plan_to_deep_profile,
)
from .review_loop import (
    _run_blocking_on_review_loop,
)
from .task_context import (
    _DEEP_THINK_MAX_ITER_CAP,
    _DEEP_THINK_MAX_ITER_DEFAULT,
    _MANUSCRIPT_CONTEXT_EXTENSIONS,
    _MANUSCRIPT_CONTEXT_ROOT,
    _OBSERVATION_ONLY_TOOLS,
    _READ_ONLY_FILE_OPERATIONS,
    _build_deep_think_task_context,
    _collect_completed_manuscript_context_paths,
    _looks_like_manuscript_context_file,
    _manuscript_context_exists,
    _refresh_deep_think_runtime_context,
    _resolve_deep_think_max_iterations,
    _should_auto_sync_task_status,
    _task_supports_paper_writing,
)
from .image_display import (
    _IMAGE_LATEST_SELECTION_PHRASES,
    _IMAGE_PREVIOUS_SELECTION_PHRASES,
    _build_recent_image_display_response,
    _select_recent_image_artifacts,
)
from .continuation_hints import (
    _CONTINUATION_FILENAME_RE,
    _LOW_SIGNAL_CONTINUATION_FILENAMES,
    _REAL_ABSOLUTE_PATH_PREFIXES,
    _append_unique_hint,
    _build_brief_execute_continuation_summary,
    _clip_continuation_text,
    _current_user_turn_index_from_history,
    _extract_recent_path_and_filename_hints,
    _is_brief_execute_followup_request,
    _looks_like_real_absolute_path,
    _path_hint_priority,
)
from .subject_grounding import (
    _apply_grounded_local_answer,
    _seed_active_subject_from_routing,
)
from .response_metadata import (
    _SKIP_CHAT_METADATA_VALUE,
    _build_deep_think_response_metadata,
    _build_existing_plan_create_result,
    _build_simple_chat_thinking_process,
    _extract_rerun_task_result_payload,
    _plan_evaluation_from_tool_results,
    _plan_runtime_metadata,
    _sanitize_chat_metadata_value,
    _sanitize_deep_think_tool_params,
    _should_bind_created_plan,
    _structured_plan_metadata_from_result,
)
from .deterministic_execute import (
    _build_deterministic_execute_fallback_text,
    _build_deterministic_execute_final_payload,
    _build_deterministic_execute_placeholder_step,
    _normalize_deterministic_execute_status,
)
from .unified_stream import (
    _drain_unified_stream_events,
    _extract_tool_context,
    _normalize_progress_text,
    _progress_label_from_phase,
    _progress_phase_from_step,
    _stream_deterministic_execute,
    _stream_direct_image_response,
    _stream_full_plan_delegate,
    _tool_progress_details,
    _truncate_progress_text,
)

logger = logging.getLogger(__name__)


async def _emit_event(event_sink: Optional[Callable], event: Dict[str, Any]) -> None:
    """Helper to emit an SSE event via the event_sink callback."""
    if event_sink is not None:
        try:
            result = event_sink(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass


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
        mp = (self.extra_context or {}).get("model_provider") or {}
        mp_base_url = mp.get("base_url") if isinstance(mp, dict) else None
        mp_api_key = mp.get("api_key") if isinstance(mp, dict) else None
        if mp_base_url and mp_api_key:
            mp_model = mp.get("model") or base_model or "qwen3.7-max"
            mp_provider = mp.get("type") or "openai"
            mp_url = mp_base_url.rstrip("/") + "/v1/chat/completions"
            override_llm_service = LLMService(LLMClient(
                provider=mp_provider,
                url=mp_url,
                api_key=mp_api_key,
                model=mp_model,
            ))
            if mp_model:
                base_model = mp_model
            llm_provider = mp_provider
        elif llm_provider:
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

    async def _run_response_guard_pipeline(self, user_message: str) -> LLMStructuredResponse:
        """Resolve routing, build the structured response and run the guard chain.

        Shared by ``handle`` and ``get_structured_response`` (D3): the two entry
        points differ only in what they do with the guarded response.
        """
        self._current_user_message = user_message
        routing_decision, _route_profile = self._resolve_request_routing(user_message)
        effective_user_message = routing_decision.effective_user_message
        self._update_routing_context(routing_decision)
        structured = self._build_deterministic_execute_task_structured()
        if structured is None:
            structured = await self._invoke_llm(effective_user_message)
        session_id = getattr(self, "session_id", None)
        structured = _rewrite_phagescope_dataset_understanding_plan_to_deep_profile(
            structured,
            user_message=effective_user_message,
            extra_context=self.extra_context,
            session_id=session_id,
        )
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
        structured = _rewrite_phagescope_dataset_understanding_plan_to_deep_profile(
            structured,
            user_message=effective_user_message,
            extra_context=self.extra_context,
            session_id=session_id,
        )
        structured = self._apply_phagescope_fallback(structured)
        structured = self._apply_task_execution_followthrough_guardrail(structured)
        return self._apply_completion_claim_guardrail(structured)

    async def handle(self, user_message: str) -> AgentResult:
        structured = await self._run_response_guard_pipeline(user_message)
        return await self.execute_structured(structured)

    async def get_structured_response(self, user_message: str) -> LLMStructuredResponse:
        """Return the raw structured response without executing actions."""
        return await self._run_response_guard_pipeline(user_message)

    def _build_deterministic_local_manuscript_structured(
        self,
        user_message: str,
    ) -> Optional[LLMStructuredResponse]:
        request_tier = str(self.extra_context.get("request_tier") or "").strip().lower()
        intent_type = str(self.extra_context.get("intent_type") or "").strip().lower()
        if request_tier != "execute" or intent_type != "execute_task":
            return None
        if self.plan_session.plan_id is None:
            return None
        # Do not short-circuit to manuscript assembly when the user explicitly
        # requested a plan review or optimize — those take priority.
        reason_codes = self.extra_context.get("route_reason_codes")
        if isinstance(reason_codes, list) and (
            "intent_plan_review_request" in reason_codes
            or "intent_plan_optimize_request" in reason_codes
        ):
            return None
        if not _local_manuscript_assembly_request_fn(
            user_message,
            plan_bound=True,
        ):
            return None

        raw_task_id = self.extra_context.get("current_task_id")
        try:
            task_id = int(raw_task_id) if raw_task_id is not None else None
        except (TypeError, ValueError):
            task_id = None

        tree = getattr(self, "plan_tree", None)
        if tree is None or (
            task_id is not None and not getattr(tree, "has_node", lambda *_: False)(task_id)
        ):
            try:
                tree = self.plan_session.repo.get_plan_tree(self.plan_session.plan_id)
            except Exception:
                tree = None
        if tree is None:
            return None

        current_node = None
        if task_id is not None and getattr(tree, "has_node", lambda *_: False)(task_id):
            try:
                current_node = tree.get_node(task_id)
            except Exception:
                current_node = None

        if current_node is not None and _task_supports_paper_writing(current_node):
            return None

        context_paths = _collect_completed_manuscript_context_paths(
            tree,
            current_task_id=task_id,
            max_paths=12,
        )
        language = detect_reasoning_language(user_message)
        reply_text = (
            "我会基于已完成任务的现有产物直接整合本地论文草稿。"
            if language == "zh"
            else "I will assemble a local manuscript draft directly from the completed task outputs."
        )
        params: Dict[str, Any] = {
            "task": str(user_message or "").strip(),
            "output_path": "manuscript/manuscript_draft.md",
            "draft_only": True,
        }
        if context_paths:
            params["context_paths"] = context_paths

        metadata: Dict[str, Any] = {
            "origin": "local_manuscript_assembly_shortcut",
        }
        if task_id is not None:
            metadata["bound_task_id"] = task_id

        logger.info(
            "[CHAT][ROUTING][MANUSCRIPT_SHORTCUT] plan_id=%s current_task_id=%s context_paths=%s",
            self.plan_session.plan_id,
            task_id,
            len(context_paths),
        )
        return LLMStructuredResponse.model_validate(
            {
                "llm_reply": {"message": reply_text},
                "actions": [
                    {
                        "kind": "tool_operation",
                        "name": "manuscript_writer",
                        "parameters": params,
                        "order": 1,
                        "blocking": True,
                        "metadata": metadata,
                    }
                ],
            }
        )

    def _build_deterministic_execute_task_structured(self) -> Optional[LLMStructuredResponse]:
        # Full-plan intent takes precedence over the single-task shortcut:
        # "开始执行全部任务" with explicit task numbering still means "run the
        # whole tree", so bail out and let the full-plan delegate handle it.
        try:
            from app.routers.chat.request_routing import _is_full_plan_execution_request
            if _is_full_plan_execution_request(
                str(self._current_user_message or ""),
                plan_bound=self.plan_session.plan_id is not None,
            ):
                return None
        except Exception:
            pass
        request_tier = str(self.extra_context.get("request_tier") or "").strip().lower()
        intent_type = str(self.extra_context.get("intent_type") or "").strip().lower()
        if request_tier != "execute" or intent_type != "execute_task":
            return None
        if not bool(self.extra_context.get("explicit_task_override")):
            return None
        if self.plan_session.plan_id is None:
            return None
        if bool(self.extra_context.get("explicit_scope_all_blocked")):
            return None

        raw_task_id = self.extra_context.get("current_task_id")
        try:
            task_id = int(raw_task_id) if raw_task_id is not None else None
        except (TypeError, ValueError):
            task_id = None
        if task_id is None or task_id <= 0:
            return None

        pending_ids = self.extra_context.get("pending_scope_task_ids")
        logger.info(
            "[CHAT][ROUTING][EXEC_SHORTCUT] Using deterministic rerun_task "
            "current=%s pending=%s plan_id=%s",
            task_id,
            list(pending_ids) if isinstance(pending_ids, list) else [],
            self.plan_session.plan_id,
        )
        return LLMStructuredResponse.model_validate(
            {
                "llm_reply": {
                    "message": f"Deterministic execute-task shortcut: task {task_id}."
                },
                "actions": [
                    {
                        "kind": "task_operation",
                        "name": "rerun_task",
                        "parameters": {"task_id": int(task_id)},
                        "order": 1,
                        "blocking": True,
                        "metadata": {"origin": "explicit_execute_shortcut"},
                    }
                ],
            }
        )

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
                "explicit_task_scope_completed": (
                    "All tasks in the explicit set are already completed. "
                    "No re-execution is needed unless you explicitly ask to rerun them."
                ),
                "explicit_task_scope_blocked": (
                    "All tasks in the explicit set are currently blocked by unmet out-of-scope "
                    "dependencies. Report this blocker directly with the specific task IDs — "
                    "do NOT generate a status summary or plan optimisation suggestion."
                ),
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

        task_source = str(self.extra_context.get("_current_task_source") or "").strip().lower()
        explicit_task_selected = (
            self.extra_context.get("task_id") is not None or task_source == "request"
        )
        if task_source == "session" and not explicit_task_selected:
            from app.services.plans.acceptance_criteria import extract_explicit_deliverables_from_text

            task_deliverables = extract_explicit_deliverables_from_text(
                getattr(task_node, "instruction", None)
            )
            user_deliverables = extract_explicit_deliverables_from_text(original_task)
            if (
                task_deliverables
                and user_deliverables
                and not {item.lower() for item in task_deliverables}.intersection(
                    item.lower() for item in user_deliverables
                )
            ):
                logger.info(
                    "[CLAUDE_CODE] Routing session-bound request to unscoped execution due to deliverable conflict. task_id=%s task_deliverables=%s user_deliverables=%s",
                    getattr(task_node, "id", None),
                    task_deliverables,
                    user_deliverables,
                )
                from app.routers.chat.code_executor_helpers import build_conversation_summary_for_cc

                conv_summary = build_conversation_summary_for_cc(
                    getattr(self, "history", None) or [],
                    budget=1200,
                )
                unscoped_task = original_task
                if conv_summary:
                    unscoped_task = (
                        f"{original_task}\n\n"
                        f"[Recent conversation context (reference only)]:\n{conv_summary}"
                    )
                prepared_params = {
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

        if not _should_auto_sync_task_status(
            tool_name,
            params,
            result if isinstance(result, dict) else None,
        ):
            logger.info(
                "[TASK_SYNC] Skipping task status sync for exploratory %s execution",
                tool_name,
            )
            return

        if (
            tool_name == "file_operations"
            and str((self.extra_context or {}).get("intent_type") or "").strip().lower()
            == "execute_task"
        ):
            logger.info(
                "[TASK_SYNC] Skipping task status sync for file_operations during execute_task flow"
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

            if tool_name in {"manuscript_writer", "review_pack_writer"} and not _task_supports_paper_writing(node):
                logger.info(
                    "[TASK_SYNC] Skipping task status sync for non-paper %s execution",
                    tool_name,
                )
                return

            payload_metadata: Dict[str, Any] = {"tool_name": tool_name}
            if isinstance(extra_metadata, dict):
                for key in ("deliverables", "storage"):
                    value = extra_metadata.get(key)
                    if value is not None:
                        payload_metadata[key] = value
            # Propagate error_category from tool result so that downstream
            # logic (e.g. upstream-fallback in guardrail_handlers) can inspect
            # *why* a task failed without re-parsing free-text error messages.
            if isinstance(result, dict) and result.get("error_category"):
                payload_metadata["error_category"] = result["error_category"]
            if isinstance(result, dict):
                for key in (
                    "execution_status",
                    "verification_status",
                    "failure_kind",
                    "contract_diff",
                    "repair_attempts",
                    "plan_patch_suggestion",
                    "session_artifact_paths",
                    "run_directory",
                    "working_directory",
                    "task_directory_full",
                    "task_root_directory",
                    "results_directory",
                ):
                    value = result.get(key)
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
                execution_status=(
                    str(result.get("execution_status")).strip()
                    if isinstance(result, dict) and result.get("execution_status") is not None
                    else new_status
                ),
                trigger="auto",
            )

            # --- Artifact existence/non-empty verification ---
            # Only runs for code_executor when finalization didn't already
            # downgrade status. Only checks absolute paths.
            if (
                finalization.final_status == "completed"
                and artifact_paths
                and tool_name == "code_executor"
            ):
                missing_artifacts: List[str] = []
                empty_artifacts: List[str] = []
                for ap in artifact_paths:
                    try:
                        import os as _os
                        if not _os.path.isabs(ap):
                            continue
                        if not _os.path.exists(ap):
                            missing_artifacts.append(ap)
                        elif _os.path.isfile(ap) and _os.path.getsize(ap) == 0:
                            empty_artifacts.append(ap)
                    except Exception:
                        pass
                if missing_artifacts or empty_artifacts:
                    issues: List[str] = []
                    if missing_artifacts:
                        issues.append(f"missing: {missing_artifacts}")
                    if empty_artifacts:
                        issues.append(f"empty: {empty_artifacts}")
                    logger.warning(
                        "[TASK_SYNC] Artifact verification failed for task %s: %s",
                        current_task_id,
                        "; ".join(issues),
                    )
                    finalization.final_status = "failed"
                    finalization.payload["status"] = "failed"
                    finalization.payload.setdefault("metadata", {})["artifact_verification_failed"] = True
                    finalization.payload["metadata"]["missing_artifacts"] = missing_artifacts
                    finalization.payload["metadata"]["empty_artifacts"] = empty_artifacts

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

                # Advance to the next pending task in composite scope.
                # When "complete task 8" expanded to [19,20,21,22] and we just
                # finished 19, automatically move current_task_id to 20 so the
                # agent continues executing the next subtask.
                pending_ids = self.extra_context.get("pending_scope_task_ids")
                if pending_ids and isinstance(pending_ids, list) and len(pending_ids) > 0:
                    next_task_id = pending_ids.pop(0)
                    self.extra_context["current_task_id"] = next_task_id
                    self.extra_context["task_id"] = next_task_id
                    self.extra_context["pending_scope_task_ids"] = pending_ids
                    logger.info(
                        "[TASK_SYNC] Advancing to next composite subtask: "
                        "completed=%s next=%s remaining=%s",
                        task_id_int,
                        next_task_id,
                        pending_ids,
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
                        finished_at=datetime.now(timezone(timedelta(hours=8))),
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
        _seed_active_subject_from_routing(self, routing_decision)
        if self.session_id:
            try:
                _persist_runtime_context(self)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Failed to persist routing runtime context: %s", exc)

        # ── Full plan execution ────────────────────────────────────────
        # When routing detected an execute-the-whole-plan request, delegate
        # to PlanExecutor immediately.  Without this hook the deterministic
        # single-task shortcut below hijacked the turn and only one atomic
        # task ever ran per chat message.
        if (
            routing_decision.full_plan_execution
            and self.plan_session.plan_id is not None
        ):
            async for chunk in _stream_full_plan_delegate(
                self,
                routing_decision=routing_decision,
                user_message=effective_user_message,
                event_sink=event_sink,
                run_id=run_id,
                log_delegation=True,
            ):
                yield chunk
            return

        direct_image_response = _build_recent_image_display_response(
            self,
            user_message=effective_user_message,
            routing_decision=routing_decision,
        )
        if direct_image_response is not None:
            response_text, response_metadata = direct_image_response
            async for chunk in _stream_direct_image_response(
                self,
                response_text=response_text,
                response_metadata=response_metadata,
                event_sink=event_sink,
            ):
                yield chunk
            return

        deterministic_execute = self._build_deterministic_execute_task_structured()
        if deterministic_execute is not None:
            async for chunk in _stream_deterministic_execute(
                self,
                deterministic_execute=deterministic_execute,
                effective_user_message=effective_user_message,
                run_id=run_id,
                event_sink=event_sink,
            ):
                yield chunk
            return

        # ── Full plan execution via PlanExecutor ──────────────────────
        # When _full_plan_executor_delegate is set, delegate to PlanExecutor
        # instead of the DeepThink cascade.  PlanExecutor handles DAG ordering,
        # artifact manifest registration, task verification, and deliverable
        # publishing.
        if self.extra_context.get("_full_plan_executor_delegate"):
            async for chunk in _stream_full_plan_delegate(
                self,
                routing_decision=routing_decision,
                user_message=effective_user_message,
                event_sink=event_sink,
                run_id=run_id,
                log_delegation=False,
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
        current_turn_tool_results: List[Dict[str, Any]] = []

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
                    "label": _truncate_progress_text(
                        label or _progress_label_from_phase(phase, language=reasoning_language), 72
                    ),
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
                progress_label = _progress_label_from_phase(phase, language=reasoning_language)
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

        # Publish this turn's progress channel on the agent so the *action* lane
        # can reach it: every tool call of this stream is executed through
        # ``action_handlers.handle_tool_action``, which builds its own
        # ``ToolContext`` and therefore cannot see this closure (the native lane
        # wires the same closure into its context in
        # ``deep_think/dispatch.py``).  Without this the delegated ``code_executor``
        # lanes report into nothing and a long run stays invisible until it ends.
        # Cleared in ``run_agent``'s ``finally`` (identity-guarded, so a newer
        # turn's channel is never clobbered).
        self._tool_progress_emitter = on_tool_progress
        self._tool_progress_loop = asyncio.get_running_loop()

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
                    elif result_payload is None:
                        # "result" key absent from details — common for task_operation
                        # handlers (e.g. verify_task) that return AgentStep without a
                        # raw result dict.  Synthesise from step.success / step.message
                        # instead of hardcoding success=False.
                        message_text = _safe_text(step.message, limit=600) or ""
                        result = {
                            "success": bool(step.success),
                            "tool": tool_name,
                            "summary": message_text,
                            "parameters": dict(tool_params),
                            "iteration": iteration,
                        }
                        if not step.success:
                            detail_error = _safe_text(details.get("error"), limit=600)
                            result["error"] = detail_error or message_text or "Tool execution failed."
                    else:
                        # result_payload exists but is not a dict — genuinely malformed.
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

                dt_agent: Any = None
                deep_think_task_context: Optional[TaskExecutionContext] = None
                created_plan_this_turn_id: Optional[int] = None

                def _sync_dt_agent_plan_binding(
                    plan_id: Optional[int],
                    *,
                    plan_title: Optional[str] = None,
                ) -> None:
                    if plan_id is None:
                        return
                    self.extra_context["plan_id"] = plan_id
                    request_profile = getattr(dt_agent, "request_profile", None)
                    if isinstance(request_profile, dict):
                        request_profile["current_plan_id"] = plan_id
                        if isinstance(plan_title, str) and plan_title.strip():
                            request_profile["current_plan_title"] = plan_title.strip()

                # Wrapper for tool execution with plan_operation binding
                async def tool_wrapper(name: str, params: Dict[str, Any]) -> Any:
                    nonlocal deep_think_tool_order, deep_think_bg_category
                    nonlocal bio_failure_active, failed_tool_name
                    nonlocal help_seen_after_failure, retry_seen_after_help
                    nonlocal created_plan_this_turn_id
                    runtime_tool_context = params.get("tool_context") if isinstance(params, dict) else None
                    safe_params = _sanitize_deep_think_tool_params(params)
                    requested_operation = str(safe_params.get("operation") or "").strip().lower()
                    allow_new_plan_rebind = bool(
                        self.extra_context.get("plan_new_requested")
                    ) and created_plan_this_turn_id is None

                    if name == "plan_operation" and requested_operation == "create":
                        existing_plan_id = self.plan_session.plan_id
                        if (
                            existing_plan_id is not None
                            and not _should_bind_created_plan(
                                existing_plan_id=existing_plan_id,
                                allow_new_plan_rebind=allow_new_plan_rebind,
                            )
                        ):
                            plan_tree = getattr(self, "plan_tree", None)
                            plan_title = (
                                str(getattr(plan_tree, "title", "") or "").strip() or None
                            )
                            _sync_dt_agent_plan_binding(
                                existing_plan_id,
                                plan_title=plan_title,
                            )
                            logger.info(
                                "[DeepThink] Reused existing bound plan %s for repeated plan_operation create request",
                                existing_plan_id,
                            )
                            return _build_existing_plan_create_result(
                                existing_plan_id=existing_plan_id,
                                plan_title=plan_title,
                            )

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

                        if name == "code_executor" and self.extra_context.get(
                            "explicit_scope_all_blocked"
                        ):
                            blocked_ids = (
                                self.extra_context.get("explicit_scope_blocked_task_ids") or []
                            )
                            id_list = (
                                ", ".join(str(t) for t in blocked_ids)
                                if blocked_ids
                                else "the requested tasks"
                            )
                            block_reason = self.extra_context.get(
                                "explicit_scope_block_reason", "blocked_deps"
                            )
                            if block_reason == "all_completed":
                                summary = (
                                    f"Tasks [{id_list}] are already completed — "
                                    f"no re-execution is needed. "
                                    f"If you want to re-run them, please say so explicitly."
                                )
                                error_category = "already_completed"
                            else:
                                summary = (
                                    f"Tasks [{id_list}] could not be executed in this turn: "
                                    f"all tasks in the explicit set are blocked by unmet "
                                    f"out-of-scope dependencies. "
                                    f"Do NOT generate a status summary or plan optimisation "
                                    f"suggestion — report this blocker with the specific task IDs."
                                )
                                error_category = "blocked_dependency"
                            logger.warning(
                                "[DeepThink] Blocked code_executor: explicit scope all blocked "
                                "reason=%s task_ids=%s",
                                block_reason,
                                blocked_ids,
                            )
                            if block_reason == "all_completed":
                                # Tasks already completed is not a failure: report a
                                # successful no-op so downstream failure templates
                                # (truth barrier) are not triggered.
                                return {
                                    "success": True,
                                    "tool": "code_executor",
                                    "summary": summary,
                                    "error": None,
                                    "skipped_reason": "already_completed",
                                    "blocked_task_ids": blocked_ids,
                                    "error_category": error_category,
                                }
                            return {
                                "success": False,
                                "tool": "code_executor",
                                "error": summary,
                                "summary": summary,
                                "blocked_reason": "explicit_task_scope_blocked",
                                "blocked_task_ids": blocked_ids,
                                "error_category": error_category,
                            }

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
                        recent_tool_results = self.extra_context.get(
                            "recent_tool_results"
                        )
                        latest_recent_entry = (
                            recent_tool_results[-1]
                            if isinstance(recent_tool_results, list)
                            and recent_tool_results
                            else None
                        )
                        if (
                            isinstance(latest_recent_entry, dict)
                            and str(
                                latest_recent_entry.get("tool")
                                or latest_recent_entry.get("name")
                                or ""
                            ).strip()
                            == name
                        ):
                            captured_tool_result = {
                                **dict(latest_recent_entry),
                                "name": str(
                                    latest_recent_entry.get("name")
                                    or latest_recent_entry.get("tool")
                                    or name
                                ).strip(),
                                "parameters": (
                                    dict(latest_recent_entry.get("parameters"))
                                    if isinstance(
                                        latest_recent_entry.get("parameters"), dict
                                    )
                                    else dict(safe_params)
                                ),
                                "result": (
                                    dict(latest_recent_entry.get("result"))
                                    if isinstance(
                                        latest_recent_entry.get("result"), dict
                                    )
                                    else dict(result)
                                ),
                            }
                        else:
                            captured_tool_result = {
                                "name": name,
                                "tool": name,
                                "summary": _safe_text(
                                    result.get("summary") or step.message,
                                    limit=600,
                                ),
                                "parameters": dict(safe_params),
                                "result": dict(result),
                            }
                        current_turn_tool_results.append(captured_tool_result)

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

                        _refresh_deep_think_runtime_context(
                            self,
                            dt_agent=dt_agent,
                            task_context=deep_think_task_context,
                            user_message=effective_user_message,
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

                    if runtime_tool_context is not None:
                        result = await execute_tool(
                            name,
                            tool_context=runtime_tool_context,
                            **safe_params,
                        )
                    else:
                        result = await execute_tool(name, **safe_params)

                    # Special handling: bind Plan to session after successful creation
                    if name == "plan_operation" and isinstance(result, dict):
                        if result.get("success") and result.get("operation") == "create":
                            plan_id = result.get("plan_id")
                            if plan_id:
                                existing_plan_id = self.plan_session.plan_id
                                if not _should_bind_created_plan(
                                    existing_plan_id=existing_plan_id,
                                    allow_new_plan_rebind=allow_new_plan_rebind,
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
                                        plan_tree = getattr(self, "plan_tree", None)
                                        plan_title = (
                                            str(getattr(plan_tree, "title", "") or "").strip() or None
                                        )
                                        _sync_dt_agent_plan_binding(
                                            plan_id,
                                            plan_title=plan_title,
                                        )
                                        try:
                                            created_plan_this_turn_id = int(plan_id)
                                        except (TypeError, ValueError):
                                            created_plan_this_turn_id = None
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

                                        auto_review_mode = "not_scheduled"
                                        integrated_generation = bool(
                                            result.get("decomposition_completed")
                                        ) or str(
                                            result.get("decomposition_status") or ""
                                        ).strip().lower() in {"completed", "partial", "not_configured", "skipped"}
                                        if integrated_generation:
                                            logger.info(
                                                "[DeepThink] Plan %s returned from integrated generation with decomposition_status=%s",
                                                plan_id,
                                                result.get("decomposition_status"),
                                            )
                                            if self._start_background_created_plan_auto_review(
                                                plan_id
                                            ):
                                                result["auto_review"] = {
                                                    "status": "scheduled",
                                                    "mode": "background",
                                                }
                                                auto_review_mode = "background"
                                        else:
                                            # Backward compatibility for older create handlers that still
                                            # return before decomposition is complete.
                                            session_ctx = {
                                                "user_message": effective_user_message,
                                                "request_tier": routing_decision.request_tier,
                                                "chat_history": self.history,
                                                "chat_history_max_messages": self.max_history_messages,
                                                "recent_tool_results": self.extra_context.get(
                                                    "recent_tool_results", []
                                                ),
                                            }
                                            if self.session_id:
                                                session_ctx["session_id"] = self.session_id
                                            owner_id = str(
                                                self.extra_context.get("owner_id") or ""
                                            ).strip()
                                            if owner_id:
                                                session_ctx["owner_id"] = owner_id
                                            decompose_result = await asyncio.to_thread(
                                                self._auto_decompose_plan,
                                                plan_id,
                                                wait_for_completion=False,
                                                session_context=session_ctx,
                                                after_success=lambda: self._run_created_plan_auto_review_sync(
                                                    plan_id
                                                ),
                                            )
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
                                                    auto_review_mode = "after_decomposition"
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
                                            "(generation ready, "
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

                        current_turn_tool_results.append(
                            {
                                "name": name,
                                "tool": name,
                                "summary": _safe_text(
                                    result.get("summary") or result.get("message"),
                                    limit=600,
                                ),
                                "parameters": dict(safe_params),
                                "result": _sanitize_chat_metadata_value(dict(result)),
                            }
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
                        from .artifact_gallery import filter_gallery_new_images_only

                        merged = merge_artifact_gallery(
                            current_turn_artifact_gallery,
                            [gallery_item],
                        )
                        # Render each image exactly once per session: skip
                        # paths already shown by earlier replies, and paths
                        # outside this session's runtime scope.
                        filtered = filter_gallery_new_images_only(
                            merged,
                            session_id=self.session_id,
                        )
                        if len(filtered) == len(merged):
                            current_turn_artifact_gallery[:] = merged
                            update_recent_image_artifacts(self.extra_context, [gallery_item])
                        else:
                            artifact_meta["path"] = ""
                        artifact_meta = {
                            **artifact_meta,
                            "path": gallery_item["path"],
                            "display_name": gallery_item["display_name"],
                            "mime_family": gallery_item["mime_family"],
                            "origin": gallery_item["origin"],
                            "tracking_id": gallery_item["tracking_id"],
                        }
                        if len(filtered) == len(merged):
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
                        "current_task_id": self.extra_context.get("current_task_id"),
                        "pending_scope_task_ids": list(
                            self.extra_context.get("pending_scope_task_ids") or []
                        ),
                        "session_id": self.session_id,
                        "owner_id": str(
                            self.extra_context.get("owner_id") or ""
                        ).strip() or None,
                    }
                dt_agent = dt_agent_cls(**dt_agent_kwargs)

                await _emit_progress_status(
                    phase="planning",
                    label=_progress_label_from_phase("planning", language=reasoning_language),
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
                deep_think_task_context = _build_deep_think_task_context(
                    self,
                    user_message=effective_user_message,
                )

                # Run think
                result = await dt_agent.think(
                    effective_user_message,
                    think_context,
                    task_context=deep_think_task_context,
                )
                if cancel_event is not None and cancel_event.is_set():
                    if deep_think_job_created and deep_think_job_id:
                        plan_decomposition_jobs.mark_failure(
                            deep_think_job_id,
                            "cancelled",
                            result={"cancelled": True},
                        )
                    await queue.put({"type": "error", "error": "Run cancelled."})
                else:
                    # Put result in queue FIRST so the consumer can emit the
                    # ``final`` SSE event without waiting for job bookkeeping.
                    await queue.put(
                        {
                            "type": "result",
                            "result": result,
                            "bg_category": deep_think_bg_category,
                            "job_id": deep_think_job_id if deep_think_job_created else None,
                        }
                    )
                    # Persist job status AFTER the result is queued (non-blocking
                    # for the SSE consumer).
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
            except Exception as e:
                error_message = str(e) or type(e).__name__
                error_payload: Dict[str, Any] = {
                    "type": "error",
                    "error": error_message,
                    "error_type": type(e).__name__,
                }
                if isinstance(e, LLMProviderError):
                    error_payload.update(
                        {
                            "error_code": e.error_code,
                            "category": e.category,
                            "provider": e.provider,
                            "retryable": e.retryable,
                            "status_code": e.status_code,
                        }
                    )
                logger.exception(
                    "Deep think execution failed: %s: %s",
                    type(e).__name__,
                    error_message,
                )
                if deep_think_job_created and deep_think_job_id:
                    plan_decomposition_jobs.mark_failure(
                        deep_think_job_id,
                        error_message,
                        result={k: v for k, v in error_payload.items() if k != "type"},
                    )
                await queue.put(error_payload)
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
                if getattr(self, "_tool_progress_emitter", None) is on_tool_progress:
                    self._tool_progress_emitter = None
                    self._tool_progress_loop = None
                await queue.put(None)  # Signal end

        # Start agent in background
        asyncio.create_task(run_agent())

        async for chunk in _drain_unified_stream_events(
            self,
            queue=queue,
            routing_decision=routing_decision,
            reasoning_language=reasoning_language,
            thinking_visible=thinking_visible,
            progress_visible=progress_visible,
            current_turn_tool_results=current_turn_tool_results,
            current_turn_artifact_gallery=current_turn_artifact_gallery,
            event_sink=event_sink,
        ):
            yield chunk

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
        meta["thinking_display_mode"] = "final_answer"
        if thinking_process is not None:
            meta["thinking_process"] = thinking_process
        payload = {
            "llm_reply": {"message": display_text},
            "response": display_text,
            "actions": [],
            "metadata": meta,
        }

        # 🚀 Emit final event to client FIRST (before DB save)
        yield await _through_sink({"type": "final", "payload": payload})

        # 💾 Save response to database AFTER final event
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
                save_meta["thinking_display_mode"] = "final_answer"
                if thinking_process is not None:
                    save_meta["thinking_process"] = thinking_process
                _save_chat_message(
                    self.session_id,
                    "assistant",
                    display_text,
                    metadata=save_meta,
                    model_provider=(self.extra_context or {}).get("model_provider"),
                )
            except Exception as save_err:
                logger.warning("[SIMPLE_CHAT] Failed to save response: %s", save_err)

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
        # Attribute the LLM routing-fallback classification to this session;
        # restores the surrounding context on exit (nesting-safe).
        from app.llm import clear_usage_context, set_usage_context

        usage_token = set_usage_context(
            session_id=getattr(self, "session_id", None),
            plan_id=self.plan_session.plan_id,
            task_id=self.extra_context.get("current_task_id"),
            call_purpose="request_routing",
            phase="routing",
        )
        try:
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
        finally:
            clear_usage_context(usage_token)

    # ------------------------------------------------------------------
    # Full plan execution via PlanExecutor
    # ------------------------------------------------------------------

    async def _run_full_plan_via_executor(
        self,
        *,
        user_message: str,
        routing_decision: "RequestRoutingDecision",
        event_sink: Optional[Callable] = None,
        run_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Execute the entire plan via PlanExecutor, bridging progress into SSE.

        This replaces the old DeepThink cascade for full_plan_execution requests.
        PlanExecutor handles DAG ordering, artifact manifest registration, task
        verification, and deliverable publishing.
        """
        plan_id = self.plan_session.plan_id
        if plan_id is None:
            payload = {
                "type": "final",
                "payload": {
                    "response": "No plan is bound to this session. Please create or select a plan first.",
                    "actions": [],
                    "metadata": {"full_plan_execution": True, "error": "no_plan_bound"},
                },
            }
            if event_sink is not None:
                await event_sink(payload)
            yield _sse_message(payload)
            return

        # Build execution config with session context
        session_ctx: Dict[str, Any] = {
            "session_id": self.session_id,
            "owner_id": str(
                self.extra_context.get("owner_id") or ""
            ).strip() or None,
            "user_message": user_message,
            "chat_history": list(self.history[-20:]) if self.history else [],
            "recent_tool_results": [],
            "deep_think_enabled": True,
            "paper_mode": bool(self.extra_context.get("paper_mode", False)),
            "model_provider": (self.extra_context or {}).get("model_provider"),
        }

        completed_ids: List[int] = []
        failed_ids: List[int] = []
        skipped_ids: List[int] = []
        loop = asyncio.get_running_loop()

        def on_task_complete(result: Any, step: int, total: int) -> None:
            """Callback from PlanExecutor after each task finishes."""
            task_id = getattr(result, "task_id", 0)
            status = getattr(result, "status", "unknown")
            if status == "completed":
                completed_ids.append(task_id)
            elif status == "skipped":
                skipped_ids.append(task_id)
            else:
                failed_ids.append(task_id)

            # Push SSE event for task completion
            event = {
                "type": "full_plan_task_complete",
                "task_id": task_id,
                "status": status,
                "step": step,
                "total": total,
                "content": str(getattr(result, "content", "") or "")[:200],
            }
            try:
                # Schedule SSE emission from the sync callback
                asyncio.run_coroutine_threadsafe(
                    _emit_event(event_sink, event), loop
                )
            except Exception:
                pass

        config = ExecutionConfig(
            session_context=session_ctx,
            paper_mode=bool(session_ctx.get("paper_mode", False)),
            dependency_throttle=False,  # Don't stop on first failure for full plan
            auto_recovery=True,
            skip_preflight=True,  # LLM-generated artifact contracts may have issues; don't block execution
            on_task_complete=on_task_complete,
        )

        # Emit start event
        start_payload = {
            "type": "full_plan_start",
            "plan_id": plan_id,
            "message": "Starting full plan execution via PlanExecutor...",
        }
        if event_sink is not None:
            await event_sink(start_payload)
        yield _sse_message(start_payload)

        try:
            executor = self.plan_executor
            if executor is None:
                from app.services.plans.plan_executor import PlanExecutor, PlanExecutorLLMService
                executor = PlanExecutor(
                    repo=self.plan_session.repo,
                    llm_service=PlanExecutorLLMService(llm=self.llm_service),
                )
            summary = await asyncio.to_thread(
                executor.execute_plan,
                plan_id,
                config=config,
            )
        except Exception as exc:
            logger.exception(
                "[CHAT][FULL_PLAN_EXECUTOR] PlanExecutor.execute_plan failed: %s", exc
            )
            error_payload = {
                "type": "final",
                "payload": {
                    "response": f"Full plan execution failed: {exc}",
                    "actions": [],
                    "metadata": {
                        "full_plan_execution": True,
                        "error": str(exc),
                    },
                },
            }
            if event_sink is not None:
                await event_sink(error_payload)
            yield _sse_message(error_payload)
            return

        # Build final summary
        completed_count = len(summary.executed_task_ids)
        failed_count = len(summary.failed_task_ids)
        skipped_count = len(summary.skipped_task_ids)
        total_count = completed_count + failed_count + skipped_count

        parts = []
        if completed_count:
            parts.append(f"{completed_count} completed")
        if failed_count:
            parts.append(f"{failed_count} failed")
        if skipped_count:
            parts.append(f"{skipped_count} skipped")
        summary_text = f"Full plan execution finished: {', '.join(parts) or 'no tasks processed'}."
        if total_count > 0:
            summary_text += f" ({total_count} tasks total)"

        # Refresh plan tree to pick up updated statuses
        try:
            _refresh_plan_tree_fn(self, force_reload=True)
        except Exception:
            pass

        # Dispatch tasksUpdated event for frontend refresh
        try:
            if self.session_id:
                _save_chat_message(
                    self.session_id,
                    "assistant",
                    summary_text,
                    metadata={
                        "full_plan_execution": True,
                        "plan_id": plan_id,
                        "completed_count": completed_count,
                        "failed_count": failed_count,
                        "skipped_count": skipped_count,
                        "execution_summary": summary.to_dict(),
                    },
                    model_provider=(self.extra_context or {}).get("model_provider"),
                )
        except Exception as save_err:
            logger.warning("[CHAT][FULL_PLAN_EXECUTOR] Failed to save summary: %s", save_err)

        final_payload = {
            "type": "final",
            "payload": {
                "response": summary_text,
                "actions": [],
                "metadata": {
                    "full_plan_execution": True,
                    "plan_id": plan_id,
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                    "skipped_count": skipped_count,
                    "executed_task_ids": list(summary.executed_task_ids),
                    "failed_task_ids": list(summary.failed_task_ids),
                    "skipped_task_ids": list(summary.skipped_task_ids),
                },
            },
        }
        if event_sink is not None:
            await event_sink(final_payload)
        yield _sse_message(final_payload)

    def _update_routing_context(self, routing_decision: RequestRoutingDecision) -> None:
        self.extra_context.update(
            {
                "request_tier": routing_decision.request_tier,
                "request_route_mode": routing_decision.request_route_mode,
                "intent_type": routing_decision.intent_type,
                "route_reason_codes": list(routing_decision.route_reason_codes),
                "subject_resolution": dict(routing_decision.subject_resolution),
                "brevity_hint": routing_decision.brevity_hint,
                "explicit_task_ids": list(routing_decision.explicit_task_ids),
                "explicit_task_override": routing_decision.explicit_task_override,
                "full_plan_execution": routing_decision.full_plan_execution,
                "plan_create_required": routing_decision.plan_create_required,
                "plan_execute_required": routing_decision.plan_execute_required,
                "plan_execute_after_create_required": routing_decision.plan_execute_after_create_required,
                "plan_review_required": routing_decision.plan_review_required,
                "plan_optimize_required": routing_decision.plan_optimize_required,
                "plan_new_requested": routing_decision.plan_new_requested,
                "plan_conflict_requires_confirmation": routing_decision.plan_conflict_requires_confirmation,
                "current_user_turn_index": _current_user_turn_index_from_history(self.history),
            }
        )

        # ── Full plan execution ───────────────────────────────────────
        # When user requests "执行整个计划" / "complete all tasks",
        # full_plan_execution stays False so DeepThink handles it via
        # plan_operation(execute_all).  The plan_execute_required flag
        # (set by routing) injects [REQUIREMENT] into DeepThink's system
        # prompt to ensure it calls execute_all reliably.
        if routing_decision.full_plan_execution:
            self.extra_context["_current_task_source"] = "full_plan"
            if self.plan_session.plan_id is not None:
                self.extra_context["_full_plan_executor_delegate"] = True
                self.extra_context.pop("explicit_scope_all_blocked", None)
                self.extra_context.pop("explicit_scope_block_reason", None)
                self.extra_context.pop("explicit_scope_blocked_task_ids", None)
                self.extra_context.pop("explicit_task_override", None)
                self.extra_context.pop("explicit_task_ids", None)
                self.extra_context.pop("current_task_id", None)
                self.extra_context.pop("task_id", None)
                self.extra_context.pop("pending_scope_task_ids", None)
                logger.info(
                    "[CHAT][ROUTING][FULL_PLAN] Delegating to PlanExecutor: plan_id=%s",
                    self.plan_session.plan_id,
                )
            else:
                self.extra_context["_full_plan_executor_delegate"] = False
            return

        if routing_decision.explicit_task_override:
            self.extra_context["_current_task_source"] = "request"
            if self.plan_session.plan_id is not None:
                try:
                    tree = self.plan_session.repo.get_plan_tree(self.plan_session.plan_id)
                    target_task_id = _resolve_explicit_task_scope_target_fn(
                        tree,
                        list(routing_decision.explicit_task_ids),
                        allow_cascade_rerun=True,
                        auto_include_dependency_closure=True,
                    )
                except Exception:
                    target_task_id = None

                # Resolve ALL executable leaves for composite task expansion.
                # This allows the agent to know about pending sibling tasks and
                # continue executing them after the first one completes.
                try:
                    all_targets = _resolve_all_explicit_task_scope_targets_fn(
                        tree,
                        list(routing_decision.explicit_task_ids),
                        allow_cascade_rerun=True,
                        auto_include_dependency_closure=True,
                    )
                except Exception:
                    all_targets = []

                if target_task_id is not None:
                    self.extra_context["current_task_id"] = int(target_task_id)
                    self.extra_context["task_id"] = int(target_task_id)
                    # Store remaining tasks so the agent can continue after
                    # the first task completes (composite task support).
                    remaining = [t for t in all_targets if t != target_task_id]
                    if remaining:
                        self.extra_context["pending_scope_task_ids"] = remaining
                        logger.info(
                            "[CHAT][ROUTING][EXPLICIT_SCOPE] Composite expansion: "
                            "current=%s pending=%s plan_id=%s",
                            target_task_id,
                            remaining,
                            self.plan_session.plan_id,
                        )
                    else:
                        self.extra_context.pop("pending_scope_task_ids", None)
                    # Clear any stale blocked flag from a previous turn
                    self.extra_context.pop("explicit_scope_all_blocked", None)
                    self.extra_context.pop("explicit_scope_block_reason", None)
                    self.extra_context.pop("explicit_scope_blocked_task_ids", None)
                else:
                    # All tasks in the explicit set are currently unexecutable.
                    # Classify the reason so the user gets the right message:
                    # "all_completed" → tasks already done;
                    # "blocked_deps"  → genuine unmet out-of-scope dependency.
                    try:
                        block_reason = _classify_explicit_scope_none_reason_fn(
                            tree, list(routing_decision.explicit_task_ids)
                        )
                    except Exception:
                        block_reason = "blocked_deps"
                    if block_reason == "all_completed" and _explicitly_requests_completed_task_rerun_fn(self):
                        forced_task_id = None
                        for raw_task_id in routing_decision.explicit_task_ids:
                            try:
                                candidate_task_id = int(raw_task_id)
                            except (TypeError, ValueError):
                                continue
                            if tree.has_node(candidate_task_id) and not tree.children_ids(candidate_task_id):
                                forced_task_id = candidate_task_id
                                break
                        if forced_task_id is not None:
                            self.extra_context["current_task_id"] = int(forced_task_id)
                            self.extra_context["task_id"] = int(forced_task_id)
                            self.extra_context["force_rerun_completed"] = True
                            self.extra_context.pop("pending_scope_task_ids", None)
                            self.extra_context.pop("explicit_scope_all_blocked", None)
                            self.extra_context.pop("explicit_scope_block_reason", None)
                            self.extra_context.pop("explicit_scope_blocked_task_ids", None)
                            logger.info(
                                "[CHAT][ROUTING][EXPLICIT_SCOPE] Force-rerun completed task: "
                                "task_id=%s plan_id=%s",
                                forced_task_id,
                                self.plan_session.plan_id,
                            )
                        else:
                            self.extra_context["explicit_scope_all_blocked"] = True
                            self.extra_context["explicit_scope_blocked_task_ids"] = list(
                                routing_decision.explicit_task_ids
                            )
                            self.extra_context["explicit_scope_block_reason"] = block_reason
                    else:
                        self.extra_context["explicit_scope_all_blocked"] = True
                        self.extra_context["explicit_scope_blocked_task_ids"] = list(
                            routing_decision.explicit_task_ids
                        )
                        self.extra_context["explicit_scope_block_reason"] = block_reason
                    if self.extra_context.get("explicit_scope_all_blocked"):
                        logger.info(
                            "[CHAT][ROUTING][EXPLICIT_SCOPE] All tasks unexecutable; "
                            "reason=%s explicit_task_ids=%s plan_id=%s",
                            block_reason,
                            list(routing_decision.explicit_task_ids),
                            self.plan_session.plan_id,
                        )
        else:
            self.extra_context.setdefault("_current_task_source", "session")

    async def _invoke_llm(self, user_message: str) -> LLMStructuredResponse:
        self._current_user_message = user_message
        prompt = self._build_prompt(user_message)
        model_override = self.extra_context.get("default_base_model")
        raw = await self.llm_service.chat_async(
            prompt, force_real=True, model=model_override
        )
        parsed = parse_structured_response(raw)
        if parsed is not None:
            return parsed
        logger.warning("[DeepThink] LLM output was not protocol JSON; attempting one repair pass")
        try:
            repaired_raw = await self.llm_service.chat_async(
                build_repair_prompt(raw), force_real=True, model=model_override
            )
        except Exception as exc:
            logger.warning("[DeepThink] Repair pass call failed: %s", exc)
            repaired_raw = ""
        repaired = parse_structured_response(repaired_raw)
        if repaired is not None:
            return repaired
        logger.warning("[DeepThink] Falling back to plain reply for unparseable LLM output")
        return fallback_reply_response(raw)

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
        # the split action_handlers module, but never leave global module state
        # patched after this call.  Some tests and runtime callers invoke
        # action_handlers.handle_tool_action directly; a leaked fake execute_tool
        # would otherwise corrupt later tool calls in the same process.
        patched: Dict[str, Any] = {}
        try:  # pragma: no cover - compatibility bridge
            from app.routers import chat_routes as compat_chat_routes
            from . import action_handlers as _action_handlers_module
            from app.config.tool_policy import (
                get_tool_policy as _default_get_tool_policy,
                is_tool_allowed as _default_is_tool_allowed,
            )
            from app.services.plans.decomposition_jobs import (
                get_current_job as _default_get_current_job,
            )
            from tool_box import execute_tool as _default_execute_tool

            default_hooks = {
                "get_tool_policy": _default_get_tool_policy,
                "is_tool_allowed": _default_is_tool_allowed,
                "execute_tool": _default_execute_tool,
                "get_current_job": _default_get_current_job,
            }
            for name, default_value in default_hooks.items():
                candidate = getattr(compat_chat_routes, name, None)
                if candidate is None or candidate is default_value:
                    continue
                patched[name] = getattr(_action_handlers_module, name)
                setattr(_action_handlers_module, name, candidate)
        except Exception:
            patched = {}
        try:
            return await _handle_tool_action_fn(self, action)
        finally:
            if patched:
                try:
                    from . import action_handlers as _action_handlers_module

                    for name, previous in patched.items():
                        setattr(_action_handlers_module, name, previous)
                except Exception:
                    pass

    async def _handle_plan_action(self, action):
        return await _handle_plan_action_fn(self, action)

    def _handle_task_action(self, action):
        if getattr(action, "name", None) == "rerun_task":
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return _handle_task_action_fn(self, action)
            return _handle_task_action_async_fn(self, action)
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
        from app.services.plans.plan_optimizer import plan_review_needs_optimization

        try:
            review_result = _run_blocking_on_review_loop(
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

        if plan_review_needs_optimization(review_result):
            try:
                optimize_result = _run_blocking_on_review_loop(
                    execute_tool(
                        "plan_operation",
                        operation="optimize",
                        plan_id=plan_id,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "[DeepThink] Auto-optimize failed for plan %s: %s",
                    plan_id,
                    exc,
                )
                return dict(review_result)

            if isinstance(optimize_result, dict):
                logger.info(
                    "[DeepThink] Auto-optimized plan %s after review (applied_changes=%s, score_before=%s, score_after=%s)",
                    plan_id,
                    optimize_result.get("applied_changes"),
                    optimize_result.get("rubric_score_before"),
                    optimize_result.get("rubric_score_after"),
                )
                review_result = dict(review_result)
                review_result["auto_optimize"] = dict(optimize_result)
        return dict(review_result)

    def _start_background_created_plan_auto_review(self, plan_id: int) -> bool:
        # Disabled: auto-review/optimize after plan creation causes confusion
        # when users start interacting before it completes. Users can manually
        # trigger review/optimize when they want it.
        return False

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
