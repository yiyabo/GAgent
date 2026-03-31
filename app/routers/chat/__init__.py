"""Chat router package entrypoints and public re-exports.

Primary chat implementation lives in this package. ``app.routers.chat_routes``
is kept as a compatibility facade that re-exports symbols from here.
"""

from .models import (  # noqa: F401
    ActionStatusResponse,
    AgentResult,
    AgentStep,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSessionAutoTitleBulkRequest,
    ChatSessionAutoTitleBulkResponse,
    ChatSessionAutoTitleRequest,
    ChatSessionAutoTitleResult,
    ChatSessionSettings,
    ChatSessionSummary,
    ChatSessionsResponse,
    ChatSessionUpdateRequest,
    ChatStatusResponse,
    ConfirmActionRequest,
    ConfirmActionResponse,
    StructuredReplyStreamParser,
)

from .confirmation import (  # noqa: F401
    ACTIONS_REQUIRING_CONFIRMATION,
    _cleanup_old_confirmations,
    _generate_confirmation_id,
    _get_pending_confirmation,
    _pending_confirmations,
    _remove_pending_confirmation,
    _requires_confirmation,
    _store_pending_confirmation,
)

from .background import (  # noqa: F401
    _BACKGROUND_PLAN_OPS,
    _BACKGROUND_TOOL_NAMES,
    _PHAGESCOPE_SYNC_ACTIONS,
    _classify_background_category,
    _sse_message,
)

from .tool_results import (  # noqa: F401
    append_recent_tool_result,
    drop_callables,
    normalize_dependencies,
    sanitize_tool_result,
    summarize_tool_result,
    truncate_large_fields,
)

from .guardrails import (  # noqa: F401
    explicit_manuscript_request,
    extract_task_id_from_text,
    extract_declared_absolute_paths,
    is_generic_plan_confirmation,
    is_status_query_only,
    is_task_executable_status,
    looks_like_completion_claim,
    reply_promises_execution,
    should_force_plan_first,
)

from .guardrail_handlers import (  # noqa: F401
    apply_completion_claim_guardrail,
    apply_experiment_fallback,
    apply_phagescope_fallback,
    apply_plan_first_guardrail,
    apply_task_execution_followthrough_guardrail,
    first_executable_atomic_descendant,
    infer_plan_seed_message,
    match_atomic_task_by_keywords,
    resolve_followthrough_target_task_id,
)

from .code_executor_helpers import (  # noqa: F401
    resolve_code_executor_task_context,
    normalize_csv_arg,
    summarize_amem_experiences_for_cc,
    compose_code_executor_atomic_task_prompt,
    resolve_previous_path,
    resolve_placeholders_in_value,
    resolve_action_placeholders,
)

from .prompt_builder import (  # noqa: F401
    should_use_deep_think,
    build_prompt,
    format_memories,
    compose_plan_status,
    compose_plan_catalog,
    compose_action_catalog,
    compose_guidelines,
    get_structured_agent_prompts,
    format_history,
    strip_code_fence,
)

from .action_handlers import (  # noqa: F401
    maybe_synthesize_phagescope_saveall_analysis,
    handle_tool_action,
    handle_plan_action,
    handle_task_action,
    handle_context_request,
    handle_system_action,
    handle_unknown_action,
)

from .plan_helpers import (  # noqa: F401
    build_suggestions,
    require_plan_bound,
    refresh_plan_tree,
    coerce_int,
    auto_decompose_plan,
    persist_if_dirty,
)

from .action_execution import (  # noqa: F401
    resolve_job_meta,
    log_action_event,
    truncate_summary_text,
    build_actions_summary,
    append_summary_to_reply,
    _generate_tool_analysis,
    _generate_tool_summary,
    _collect_created_tasks_from_steps,
    _generate_action_analysis,
    _build_brief_action_summary,
    _execute_action_run,
    get_action_status,
    retry_action_run,
    _build_action_status_payloads,
)

from .agent import StructuredChatAgent  # noqa: F401
from .routes import router  # noqa: F401
from .stream import chat_stream  # noqa: F401

from .session_helpers import (  # noqa: F401
    VALID_BASE_MODELS,
    VALID_LLM_PROVIDERS,
    VALID_SEARCH_PROVIDERS,
    _backfill_phagescope_submit_params,
    _build_phagescope_submit_background_summary,
    _convert_history_to_agent_format,
    _derive_conversation_id,
    _dump_metadata,
    _ensure_session_exists,
    _extract_phagescope_task_snapshot,
    _extract_session_settings,
    _extract_taskid_from_result,
    _fetch_session_info,
    _find_key_recursive,
    _get_llm_service_for_provider,
    _get_session_current_task,
    _get_session_settings,
    _is_empty_phagescope_param,
    _load_chat_history,
    _load_session_metadata_dict,
    _loads_metadata,
    _lookup_phagescope_task_memory,
    _lookup_plan_title,
    _merge_async_metadata,
    _normalize_base_model,
    _normalize_llm_provider,
    _normalize_modulelist_value,
    _normalize_search_provider,
    _record_phagescope_task_memory,
    _resolve_plan_binding,
    _row_to_session_info,
    _save_assistant_response,
    _save_chat_message,
    _set_session_plan_id,
    _update_message_content_by_tracking,
    _update_message_metadata_by_tracking,
    _update_session_metadata,
)
