"""Structured chat agent core orchestration logic."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from app.config.executor_config import get_executor_settings
from app.repository.chat_action_runs import create_action_run, fetch_action_run, update_action_run
from app.repository.plan_storage import append_action_log_entry, update_decomposition_job_status
from app.llm import LLMClient
from app.services.foundation.settings import get_settings
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
from app.services.plans.plan_session import PlanSession
from app.services.session_title_service import SessionNotFoundError
from app.services.upload_storage import delete_session_storage
from app.services.deep_think_agent import (
    DeepThinkAgent,
    DeepThinkProtocolError,
    ThinkingStep,
    DeepThinkResult,
)
from tool_box import execute_tool

from .action_execution import (
    append_summary_to_reply as _append_summary_to_reply_fn,
    build_actions_summary as _build_actions_summary_fn,
    log_action_event as _log_action_event_fn,
    resolve_job_meta as _resolve_job_meta_fn,
    truncate_summary_text as _truncate_summary_text_fn,
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
from .claude_code_helpers import (
    compose_claude_code_atomic_task_prompt as _compose_claude_code_atomic_task_prompt_fn,
    normalize_csv_arg as _normalize_csv_arg_fn,
    resolve_action_placeholders as _resolve_action_placeholders_fn,
    resolve_claude_code_task_context as _resolve_claude_code_task_context_fn,
    resolve_placeholders_in_value as _resolve_placeholders_in_value_fn,
    resolve_previous_path as _resolve_previous_path_fn,
    summarize_amem_experiences_for_cc as _summarize_amem_experiences_for_cc_fn,
)
from .guardrail_handlers import (
    apply_completion_claim_guardrail as _apply_completion_claim_guardrail_fn,
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
    compose_action_catalog as _compose_action_catalog_fn,
    compose_guidelines as _compose_guidelines_fn,
    compose_plan_catalog as _compose_plan_catalog_fn,
    compose_plan_status as _compose_plan_status_fn,
    format_history as _format_history_fn,
    format_memories as _format_memories_fn,
    get_structured_agent_prompts as _get_structured_agent_prompts_fn,
    should_use_deep_think as _should_use_deep_think_fn,
    strip_code_fence as _strip_code_fence_fn,
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

logger = logging.getLogger(__name__)

class StructuredChatAgent:
    """Plan conversation agent using a structured schema."""

    MAX_HISTORY = 30
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
            override_llm_service = LLMService(LLMClient(provider=llm_provider))

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

    async def handle(self, user_message: str) -> AgentResult:
        structured = await self._invoke_llm(user_message)
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
        structured = self._apply_phagescope_fallback(structured)
        structured = self._apply_task_execution_followthrough_guardrail(structured)
        structured = self._apply_completion_claim_guardrail(structured)
        return await self.execute_structured(structured)

    async def get_structured_response(self, user_message: str) -> LLMStructuredResponse:
        """Return the raw structured response without executing actions."""
        structured = await self._invoke_llm(user_message)
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
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


    def _resolve_claude_code_task_context(self):
        return _resolve_claude_code_task_context_fn(self)

    _normalize_csv_arg = staticmethod(_normalize_csv_arg_fn)

    _summarize_amem_experiences_for_cc = staticmethod(_summarize_amem_experiences_for_cc_fn)

    _compose_claude_code_atomic_task_prompt = staticmethod(_compose_claude_code_atomic_task_prompt_fn)

    def _resolve_previous_path(self, previous_result, path):
        return _resolve_previous_path_fn(previous_result, path)

    def _resolve_placeholders_in_value(self, value, previous_result):
        return _resolve_placeholders_in_value_fn(value, previous_result)

    def _resolve_action_placeholders(self, action, previous_result):
        return _resolve_action_placeholders_fn(action, previous_result)

    def _should_route_claude_code_unscoped(
        self, context_error: Optional[str]
    ) -> bool:
        if not context_error:
            return False
        allow_raw = self.extra_context.get("allow_unscoped_claude_code", True)
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

    async def _prepare_claude_code_params(
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
                message="claude_code requires a non-empty `task` string.",
                details={"error": "invalid_task", "tool": tool_name},
            )

        original_task = task_value.strip()
        allowed_tools = self._normalize_csv_arg(params.get("allowed_tools"))
        add_dirs = self._normalize_csv_arg(params.get("add_dirs"))

        task_node, context_error = self._resolve_claude_code_task_context()
        if context_error or task_node is None:
            if self._should_route_claude_code_unscoped(context_error):
                logger.info(
                    "[CLAUDE_CODE] Routing to unscoped execution (reason=%s, source=%s)",
                    context_error,
                    self.extra_context.get("_current_task_source"),
                )
                # Inject conversation summary even for unscoped execution
                from app.routers.chat.claude_code_helpers import build_conversation_summary_for_cc
                conv_summary = build_conversation_summary_for_cc(getattr(self, 'history', None) or [])
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
                    async def log_stdout(line: str):
                        plan_decomposition_jobs.append_log(
                            current_job_id,
                            "stdout",
                            line,
                            {},
                        )

                    async def log_stderr(line: str):
                        plan_decomposition_jobs.append_log(
                            current_job_id,
                            "stderr",
                            line,
                            {},
                        )

                    prepared_params["on_stdout"] = log_stdout
                    prepared_params["on_stderr"] = log_stderr

                return prepared_params, original_task

            context_messages = {
                "missing_plan_binding": "claude_code execution requires a bound plan. Please create/bind a plan first.",
                "missing_target_task": "claude_code execution requires a target atomic task context. Please select or run a task first.",
                "invalid_target_task": "claude_code execution requires a valid numeric task id.",
                "plan_tree_unavailable": "Unable to load the current plan tree. Please retry after refreshing plan state.",
                "target_task_not_found": "The selected task was not found in the current plan.",
                "target_task_not_atomic": "claude_code can only execute atomic tasks. Please decompose this task and execute a leaf task.",
            }
            return AgentStep(
                action=action,
                success=False,
                message=context_messages.get(
                    context_error or "",
                    "claude_code execution requires a bound atomic task context.",
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
        from app.routers.chat.claude_code_helpers import (
            build_conversation_summary_for_cc,
            collect_completed_task_outputs,
        )
        conversation_summary = build_conversation_summary_for_cc(
            getattr(self, 'history', None) or []
        )
        data_context = collect_completed_task_outputs(
            self.plan_tree, task_node.id
        )

        constrained_task = self._compose_claude_code_atomic_task_prompt(
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
            async def log_stdout(line: str):
                plan_decomposition_jobs.append_log(
                    current_job_id,
                    "stdout",
                    line,
                    {},
                )

            async def log_stderr(line: str):
                plan_decomposition_jobs.append_log(
                    current_job_id,
                    "stderr",
                    line,
                    {},
                )

            prepared_params["on_stdout"] = log_stdout
            prepared_params["on_stderr"] = log_stderr

        return prepared_params, original_task

    def _sync_task_status_after_tool_execution(
        self,
        tool_name: str,
        success: Any,
        summary: str,
        message: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        if (
            tool_name == "claude_code"
            and isinstance(params, dict)
            and params.get("require_task_context") is False
        ):
            logger.info(
                "[TASK_SYNC] Skipping task status sync for unscoped claude_code execution"
            )
            return

        current_task_id = self.extra_context.get("current_task_id")
        if current_task_id is None or self.plan_session.plan_id is None:
            return
        try:
            new_status = "completed" if success else "failed"
            task_id_int = int(current_task_id)

            self.plan_session.repo.update_task(
                self.plan_session.plan_id,
                task_id_int,
                status=new_status,
                execution_result=summary or message,
            )
            logger.info(
                "[TASK_SYNC] Updated task %s status to %s after tool %s execution",
                current_task_id,
                new_status,
                tool_name,
            )

            if new_status == "completed":
                cascade_result = f"Completed as part of parent task #{task_id_int}"
                descendants_updated = self.plan_session.repo.cascade_update_descendants_status(
                    self.plan_session.plan_id,
                    task_id_int,
                    status=new_status,
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

    async def _should_use_deep_think(self, message: str) -> bool:
        """
        LLM-routed DeepThink trigger.

        Policy:
        - explicit command always triggers
        - explicit mode disables auto-routing
        - all other routing decisions come from an LLM classifier (no keyword/regex fallback)
        """
        mode = str(getattr(get_settings(), "deep_think_mode", "smart")).strip().lower()

        # 1) Context override has highest priority.
        if self.extra_context.get("deep_think_enabled", False):
            logger.info("[DEEP_THINK] Triggered by context override flag")
            return True

        msg = (message or "").strip()
        if not msg:
            return False

        # 2) Explicit commands always trigger.
        explicit_commands = ("/think", "/deep", "/plan", "/analyze", "/decompose")
        for cmd in explicit_commands:
            if msg.startswith(cmd):
                logger.info("[DEEP_THINK] Triggered by explicit command '%s'", cmd)
                return True

        # 3) Explicit mode: no automatic routing.
        if mode == "explicit":
            logger.debug(
                "[DEEP_THINK] Not triggered (mode=explicit, non-explicit message)"
            )
            return False

        history_lines: List[str] = []
        for item in self.history[-6:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower() or "unknown"
            content = str(item.get("content", "")).strip()
            if content:
                history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines) if history_lines else "(none)"

        router_prompt = f"""You are a strict router for DeepThink mode.

Decide whether the next user message should be routed to DeepThink (iterative reasoning/tool exploration) or handled by the normal structured response path.

Return JSON only:
{{
  "use_deep_think": true or false,
  "confidence": 0.0-1.0,
  "reason": "one short sentence"
}}

Set `use_deep_think=true` only when the request is genuinely complex and benefits from deep multi-step reasoning, e.g. open-ended research planning, ambiguous scientific analysis, or tasks requiring substantial tool orchestration.

Set `use_deep_think=false` for simple chat, direct status checks, lightweight follow-ups, confirmations, or straightforward execution requests.

Conversation history (recent):
{history_text}

User message:
{msg}
"""

        model_override = self.extra_context.get("default_base_model")
        try:
            raw = await self.llm_service.chat_async(
                router_prompt,
                force_real=True,
                model=model_override,
            )
        except Exception as exc:
            logger.error("[DEEP_THINK] LLM router failed: %s", exc)
            raise RuntimeError("DeepThink router LLM call failed in strict mode.") from exc

        cleaned = self._strip_code_fence(str(raw or "").strip())
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise DeepThinkProtocolError(
                "DeepThink router output is not a valid top-level JSON object."
            )

        payload_text = cleaned[start : end + 1]
        try:
            payload = json.loads(payload_text)
        except Exception as exc:
            raise DeepThinkProtocolError(
                "DeepThink router returned invalid JSON payload."
            ) from exc

        raw_flag = payload.get("use_deep_think")
        if not isinstance(raw_flag, bool):
            raise DeepThinkProtocolError(
                "DeepThink router JSON field `use_deep_think` must be a boolean."
            )
        use_deep_think = raw_flag

        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise DeepThinkProtocolError(
                "DeepThink router JSON field `confidence` must be numeric."
            ) from exc
        confidence = max(0.0, min(1.0, confidence))

        reason_raw = payload.get("reason")
        if not isinstance(reason_raw, str) or not reason_raw.strip():
            raise DeepThinkProtocolError(
                "DeepThink router JSON field `reason` must be a non-empty string."
            )
        reason = reason_raw.strip()

        logger.info(
            "[DEEP_THINK] Routed by LLM (mode=%s, use_deep_think=%s, confidence=%.2f, reason=%s)",
            mode,
            use_deep_think,
            confidence,
            reason or "n/a",
        )
        return use_deep_think

    async def process_deep_think_stream(self, user_message: str) -> AsyncIterator[str]:
        """
        Execute deep thinking process and yield SSE events with streaming support.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue()
        deep_think_job_id: Optional[str] = f"dt_{uuid4().hex}"
        deep_think_job_created = False
        deep_think_job_queue: Optional[asyncio.Queue[Any]] = None
        active_tool_iteration: Optional[int] = None

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
                        "message_preview": str(user_message or "")[:200],
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

        async def on_thinking(step: ThinkingStep):
            nonlocal active_tool_iteration
            active_tool_iteration = step.iteration
            await queue.put(
                {
                    "type": "thinking_step",
                    "step": {
                        "iteration": step.iteration,
                        "thought": step.thought,
                        "action": step.action,
                        "status": step.status,
                        "action_result": step.action_result,
                        "evidence": step.evidence,
                        "started_at": step.started_at.isoformat() if step.started_at else None,
                        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
                        "timestamp": step.timestamp.isoformat() if step.timestamp else None,
                    },
                }
            )

        async def on_thinking_delta(iteration: int, delta: str):
            """Send token-level updates for thinking process."""
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
                            "tool": "claude_code",
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
                        "claude_code fallback is blocked until bio_tools recovery completes "
                        "(run bio_tools help, then retry a bio_tools operation once)."
                    )
                    payload: Dict[str, Any] = {
                        "success": False,
                        "tool": "claude_code",
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
                        "claude_code fallback is blocked because bio_tools input preparation failed. "
                        "Retry bio_tools with valid input_file or sequence_text."
                    )
                    if root_cause:
                        summary = f"{summary} Root cause: {root_cause}"
                    payload: Dict[str, Any] = {
                        "success": False,
                        "tool": "claude_code",
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
                        "claude_code fallback is blocked because sequence_fetch failed in input/download stage. "
                        "Retry sequence_fetch with valid accession input."
                    )
                    if root_cause:
                        summary = f"{summary} Root cause: {root_cause}"
                    payload: Dict[str, Any] = {
                        "success": False,
                        "tool": "claude_code",
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

                    if name != "plan_operation":
                        if name == "claude_code":
                            sequence_block_context = self.extra_context.get(sequence_input_block_key)
                            if isinstance(sequence_block_context, dict):
                                blocked_payload = _build_sequence_input_blocked_payload(sequence_block_context)
                                logger.warning(
                                    "[DeepThink] Blocked claude_code fallback due to sequence_fetch failure."
                                )
                                return blocked_payload

                            block_context = self.extra_context.get(bio_input_block_key)
                            if isinstance(block_context, dict):
                                blocked_payload = _build_bio_input_blocked_payload(block_context)
                                logger.warning(
                                    "[DeepThink] Blocked claude_code fallback due to bio_tools input preparation failure."
                                )
                                return blocked_payload

                        if (
                            name == "claude_code"
                            and bio_failure_active
                            and not (help_seen_after_failure and retry_seen_after_help)
                        ):
                            blocked_payload = _build_bio_recovery_blocked_payload()
                            logger.warning(
                                "[DeepThink] Blocked claude_code fallback before bio_tools recovery: failed_tool=%s",
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

                    result = await execute_tool(name, **safe_params)

                    # Special handling: bind Plan to session after successful creation
                    if name == "plan_operation" and isinstance(result, dict):
                        if result.get("success") and result.get("operation") == "create":
                            plan_id = result.get("plan_id")
                            if plan_id:
                                try:
                                    # Bind the newly created plan to the current session
                                    self.plan_session.bind(plan_id)
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
                                        "user_message": user_message,
                                        "chat_history": self.history,
                                        "recent_tool_results": self.extra_context.get(
                                            "recent_tool_results", []
                                        ),
                                    }
                                    decompose_result = await asyncio.to_thread(
                                        self._auto_decompose_plan,
                                        plan_id,
                                        wait_for_completion=False,
                                        session_context=session_ctx,
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
                                        elif decompose_result.get("job") is not None:
                                            logger.info(
                                                "[DeepThink] Auto-decomposition submitted for plan %s",
                                                plan_id,
                                            )
                                            result["decomposition_triggered"] = True
                                            result["decomposition_note"] = (
                                                "Automatic task decomposition has been submitted for background execution."
                                            )

                                    # Mark that a background decomposition was started
                                    # so the final SSE event can include background_category.
                                    deep_think_bg_category = "task_creation"

                                    # NOTE: Auto optimization loop is skipped when
                                    # decomposition runs in the background because it
                                    # depends on the completed task tree.  The user can
                                    # trigger plan review/optimize manually after
                                    # decomposition finishes.
                                    logger.info(
                                        "[DeepThink] Auto-bound plan %s to session "
                                        "(decomposition dispatched to background, "
                                        "auto-optimize skipped)",
                                        plan_id,
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
                    await queue.put({"type": "artifact", **meta})

                dt_agent = dt_agent_cls(
                    llm_client=self.llm_service,
                    available_tools=[
                        "web_search",
                        "graph_rag",
                        "sequence_fetch",
                        "claude_code",
                        "file_operations",
                        "document_reader",
                        "vision_reader",
                        "bio_tools",
                        "literature_pipeline",
                        "review_pack_writer",
                        "manuscript_writer",
                        "phagescope",
                        "deeppl",
                        "plan_operation",
                        "terminal_session",
                    ],
                    tool_executor=tool_wrapper,
                    max_iterations=24,
                    tool_timeout=120,  # 2-minute tool timeout.
                    on_thinking=on_thinking,
                    on_thinking_delta=on_thinking_delta,
                    on_final_delta=on_final_delta,
                    on_artifact=on_artifact,
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
                    "session_id": self.session_id,
                }

                # Run think
                result = await dt_agent.think(user_message, think_context)
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

        # Consume queue
        while True:
            item = await queue.get()
            if item is None:
                break

            event_type = item.get("type")
            if event_type in {
                "thinking_step",
                "thinking_delta",
                "delta",
                "control_ack",
                "tool_output",
                "artifact",
            }:
                yield _sse_message(item)
            elif event_type == "error":
                yield _sse_message({"type": "error", "message": item["error"]})
            elif event_type == "result":
                # Final result, yield as standard chat message
                res: DeepThinkResult = item["result"]
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
                        metadata_payload: Dict[str, Any] = {
                            "deep_think": True,
                            "iterations": res.total_iterations,
                            "tools_used": res.tools_used,
                            "confidence": res.confidence,
                            "thinking_process": {
                                "status": "completed",
                                "total_iterations": res.total_iterations,
                                "steps": [
                                    {
                                        "iteration": s.iteration,
                                        "thought": s.thought,
                                        "action": s.action,
                                        "action_result": s.action_result,
                                        "evidence": s.evidence,
                                        "status": "done"
                                        if s.status == "done"
                                        else "completed",  # Normalize status for history
                                        "started_at": s.started_at.isoformat()
                                        if getattr(s, "started_at", None)
                                        else None,
                                        "finished_at": s.finished_at.isoformat()
                                        if getattr(s, "finished_at", None)
                                        else None,
                                        "timestamp": s.timestamp.isoformat()
                                        if s.timestamp
                                        else None,
                                    }
                                    for s in res.thinking_steps
                                ],
                            },
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
                    except Exception as save_err:
                        logger.warning(
                            "[CHAT][DEEP_THINK] Failed to save response: %s",
                            save_err,
                        )

                # Note: final_answer was already streamed via on_final_delta callback
                # No need to yield it again here to avoid duplication
                bg_category = item.get("bg_category")
                final_metadata: Dict[str, Any] = {
                    "plan_id": self.plan_session.plan_id,  # Include plan_id so frontend can update
                    "deep_think": True,
                }
                if bg_category:
                    final_metadata["background_category"] = bg_category
                if result_job_id:
                    final_metadata["deep_think_job_id"] = result_job_id
                payload = {
                    "llm_reply": {"message": res.final_answer},
                    "actions": [],
                    "metadata": final_metadata,
                }
                yield _sse_message({"type": "final", "payload": payload})

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

    def _auto_decompose_plan(self, plan_id, *, wait_for_completion=False, session_context=None):
        return _auto_decompose_plan_fn(self, plan_id, wait_for_completion=wait_for_completion, session_context=session_context)

    def _persist_if_dirty(self):
        return _persist_if_dirty_fn(self)
