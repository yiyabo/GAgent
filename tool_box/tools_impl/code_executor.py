"""
Claude CLI Executor Tool

Integrates Anthropic's Claude Code CLI for local code execution with full file access.
Uses the official 'claude' command-line tool.
"""

import logging
import subprocess
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable, Sequence
import asyncio
from uuid import uuid4
from app.services.plans.artifact_validation import get_artifact_validation_prompt_specs
from app.services.plans.acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
)
from app.services.plans.decomposition_jobs import get_current_job, log_job_event
from app.services.session_paths import get_runtime_root, get_runtime_session_dir
from app.services.path_router import get_path_router
from app.services.resources.resource_registry import resolve_resources as _resolve_registered_resources
from app.config.executor_config import (
    DEFAULT_CODE_EXECUTION_DOCKER_IMAGE,
    DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME,
    resolve_code_execution_docker_image,
    resolve_code_execution_local_runtime,
)
from app.services.foundation.llm_config import is_production, platform_profile
from app.services.interpreter.runtime_guardrails import (
    ENV_GUARD_BIN as _ENV_GUARD_BIN,
    inject_env_mutation_guard as _inject_env_mutation_guard,
    looks_like_engineering_task as _looks_like_engineering_task,
)

# --- Extracted siblings (facade re-export; design/2026-09-24 §4.1) ---------
# Names below live in the sibling modules now; this module re-exports them so
# every existing import site (production + 53 private-name test imports) and
# every bare-name call site in this facade keep working unchanged. Siblings
# resolve cross-cluster calls back through THIS module (late binding).
from .code_executor_semantic import (
    _BLOCK_SCOPE_REASON,
    _BLOCK_SCOPE_STATUS,
    _SEMANTIC_FAILURE_DETAIL_RE,
    _SEMANTIC_FAILURE_STATUS_DETAILS,
    _SEMANTIC_FAILURE_STATUS_RE,
    _SKILL_GUIDANCE_MAX_CHARS,
    _SKILL_GUIDANCE_MAX_SKILLS,
    _apply_execution_failure_to_payload,
    _classify_execution_success,
    _clear_stale_contract_failure_state,
    _detect_execution_semantic_or_output_failure,
    _detect_missing_required_outputs,
    _detect_scope_blocked,
    _detect_semantic_execution_failure,
    _execution_failure_error_category,
    _extract_semantic_failure_from_text,
    _get_skill_guidance,
    _iter_structured_semantic_text,
    _normalize_csv_values,
    _required_output_is_present,
    _semantic_failure_error,
    _semantic_failure_kind,
)
from .code_executor_cli_parse import (
    _DEFAULT_TASK_SUBDIRECTORIES,
    _QWEN_DEBUG_ENABLED_LINE_RE,
    _QWEN_LOGGING_TO_LINE_RE,
    _build_summary_from_parsed_json,
    _compact_cli_text,
    _derive_task_subdirectories,
    _extract_deliverables_from_jsonl,
    _extract_qwen_debug_log_path,
    _extract_readable_error,
    _extract_result_from_jsonl,
    _format_directory_choices,
    _format_task_subdirectories,
    _is_qwen_truncated_tool_failure_text,
    _iter_stream_lines_unbounded,
    _partition_cli_stderr_lines,
    _qwen_truncated_tool_failure_note,
)
from .code_executor_qwen import (
    _PARTIAL_COMPLETION_PATTERNS,
    _QWEN_CLI_NO_OUTPUT_TIMEOUT_SECONDS,
    _QWEN_COMPLETED_OUTPUT_EXIT_CHECK_SECONDS,
    _QWEN_COMPLETED_OUTPUT_EXIT_GRACE_SECONDS,
    _QWEN_FATAL_DEBUG_SCAN_BYTES,
    _QWEN_PROCESS_EXIT_WAIT_SECONDS,
    _QWEN_PROCESS_KILL_WAIT_SECONDS,
    _QWEN_SHELL_FALLBACK_MAX_TIMEOUT_MS,
    _QWEN_SHELL_FALLBACK_TIMEOUT_MS,
    _QWEN_TRANSCRIPTS_ROOT,
    _WARNING_LINE_PATTERNS,
    _build_cli_failure_error,
    _build_qwen_code_command,
    _build_qwen_container_mounts,
    _detect_partial_completion_cli,
    _detect_qwen_debug_fatal_failure,
    _extract_pending_qwen_function_call,
    _extract_pending_qwen_shell_command,
    _find_unique_run_prefixed_contract_source,
    _materialize_contract_outputs_for_standard_paths,
    _materialize_contract_view_for_flat_outputs,
    _qwen_outputs_pass_contract_for_early_exit,
    _qwen_single_work_dir_passes_contract_for_early_exit,
    _read_qwen_debug_log_text,
    _read_qwen_transcript_text,
    _recover_pending_qwen_shell_call,
    _resolve_qwen_cli_no_output_timeout_seconds,
    _resolve_qwen_completed_output_exit_check_seconds,
    _resolve_qwen_completed_output_exit_grace_seconds,
    _resolve_qwen_process_exit_wait_seconds,
    _resolve_qwen_process_kill_wait_seconds,
    _run_subprocess_capture,
    _verify_contract_for_qwen_early_exit,
    _wait_for_cli_process_return_code,
    _wait_for_qwen_cli_drain_or_watchdog,
)
from .code_executor_contracts import (
    _append_contract_artifact_paths,
    _build_ad_hoc_execution_spec,
    _build_cli_contract_repair_task,
    _build_cli_task_contract,
    _build_execution_spec,
    _build_verification_artifact_paths,
    _contract_required_artifact_records,
    _extract_acceptance_criteria_from_node,
    _extract_code_workspace_metadata,
    _extract_task_referenced_read_dirs,
    _final_response_contract_prompt,
    _format_cli_acceptance_checks,
    _format_contract_diff_for_cli,
    _format_resolved_resources_for_prompt,
    _format_supervised_ml_contract_for_prompt,
    _generate_task_dir_name_llm,
    _is_allowed_task_read_path,
    _is_path_within,
    _is_path_within_lexical,
    _is_verification_only_task,
    _normalize_resolved_resources,
    _resource_read_dirs,
    _rerun_update_mode_prompt,
    _sanitize_task_dir_component,
    _summarize_dependency_blockers,
    _validate_scope_contract,
)

# Compat alias: the CLI-stdout partial-completion detector was renamed to
# _detect_partial_completion_cli in the qwen sibling (see its docstring) so it
# can never be confused with gating_probe's tool-result-layer
# _detect_partial_completion_in_tool_results. The historical facade name stays
# available for existing import sites and bare-name callers in this module.
_detect_partial_completion = _detect_partial_completion_cli

logger = logging.getLogger(__name__)

_TASK_READ_DIR_PREFIXES: Sequence[str] = (
    "app",
    "code",
    "data",
    "docker",
    "docs",
    "paper",
    "phagescope",
    "reference",
    "results",
    "runtime",
    "scripts",
    "test",
    "tests",
    "tool_box",
    "web-ui",
)
_TASK_PATH_TOKEN_RE = r"[^\s'\"`<>\(\)\[\]\{\},;:，。；：！？、（）【】《》「」『』“”‘’]+"
_DEFAULT_EXTERNAL_READ_ROOTS: Sequence[Path] = (Path("/mnt/sdm/zczhao"),)


# Project root directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Claude Code runtime directory
_RUNTIME_DIR = get_runtime_root()
_LOG_DIR = _RUNTIME_DIR / "code_executor_logs"

# Strict execution boundary: only a constrained subset of tools is allowed.
_HARD_ALLOWED_TOOL_NAMES: Sequence[str] = (
    "Bash",
    "BashOutput",
    "Edit",
    "MultiEdit",
    "Read",
    "Write",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "NotebookEdit",
)
_DEFAULT_ALLOWED_TOOL_NAMES: Sequence[str] = (
    "Bash",
    "BashOutput",
    "Edit",
    "MultiEdit",
    "Read",
    "Write",
    "Glob",
    "Grep",
    "LS",
)
_HARD_ALLOWED_TOOL_MAP = {name.lower(): name for name in _HARD_ALLOWED_TOOL_NAMES}
_DEFAULT_SETTING_SOURCES = "project,local"
_DEFAULT_API_SETTING_SOURCES = "project"
_DEFAULT_AUTH_MODE = "api_env"
_DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME = DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME
_DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE = DEFAULT_CODE_EXECUTION_DOCKER_IMAGE
_SUPPORTED_SETTING_SOURCES = {"user", "project", "local"}
_SUPPORTED_AUTH_MODES = {"claude_login", "api_env"}
_DEFAULT_API_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"
_DEFAULT_API_MODEL = "qwen3.7-max"
_DEFAULT_QC_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_CLAUDE_ENV_KEYS_FOR_LOGIN_MODE: Sequence[str] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_AUTH_TOKEN",
)
_CLAUDE_ENV_KEYS_FOR_API_MODE: Sequence[str] = (
    *_CLAUDE_ENV_KEYS_FOR_LOGIN_MODE,
    "CLAUDE_API_KEY",
    "CLAUDE_API_URL",
    "CLAUDE_BASE_URL",
    "CLAUDE_MODEL",
)
_CLAUDE_ENV_ALIAS_FOR_API_MODE: Sequence[tuple[str, str]] = (
    ("CLAUDE_CODE_API_KEY", "ANTHROPIC_API_KEY"),
    ("CLAUDE_CODE_BASE_URL", "ANTHROPIC_BASE_URL"),
    ("CLAUDE_CODE_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"),
    ("CLAUDE_CODE_API_MODEL", "ANTHROPIC_MODEL"),
    ("CLAUDE_CODE_SMALL_FAST_MODEL", "ANTHROPIC_SMALL_FAST_MODEL"),
)


from .code_executor_backend import (
    _CLAUDE_ENV_ALIAS_FOR_API_MODE,
    _CLAUDE_ENV_KEYS_FOR_API_MODE,
    _CLAUDE_ENV_KEYS_FOR_LOGIN_MODE,
    _DEFAULT_ALLOWED_TOOL_NAMES,
    _DEFAULT_API_BASE_URL,
    _DEFAULT_API_MODEL,
    _DEFAULT_API_SETTING_SOURCES,
    _DEFAULT_AUTH_MODE,
    _DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE,
    _DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME,
    _DEFAULT_EXTERNAL_READ_ROOTS,
    _DEFAULT_QC_BASE_URL,
    _DEFAULT_SETTING_SOURCES,
    _HARD_ALLOWED_TOOL_MAP,
    _HARD_ALLOWED_TOOL_NAMES,
    _SUPPORTED_AUTH_MODES,
    _SUPPORTED_SETTING_SOURCES,
    _build_claude_code_command,
    _build_claude_code_prompt,
    _build_code_executor_subprocess_env,
    _build_local_backend_result_payload,
    _build_qwen_code_subprocess_env,
    _build_qwen_execution_session_id,
    _coerce_positive_int,
    _ensure_writable_subprocess_cache_env,
    _estimate_cli_completion_tokens,
    _estimate_cli_prompt_tokens,
    _execute_task_locally,
    _FIGURE_INTENT_RE,
    _FIGURE_STYLE_PROMPT,
    _figure_style_prompt,
    _is_qwen_container_infrastructure_error,
    _is_qwen_no_output_timeout,
    _is_qwen_recoverable_cli_failure,
    _is_qwen_session_in_use_error,
    _parse_cli_usage_from_jsonl,
    _prepare_code_executor_read_context,
    _qwen_code_cli_available,
    _qwen_infrastructure_fallback_allowed,
    _record_external_cli_usage,
    _resolve_allowed_tools,
    _resolve_auth_mode,
    _resolve_cli_retry_policy,
    _resolve_code_executor_backend,
    _resolve_code_executor_docker_image,
    _resolve_code_executor_local_runtime,
    _resolve_promoted_output_files,
    _resolve_setting_sources,
    _should_fallback_from_qwen_infra_failure,
    _validate_api_mode_config,
    _validate_qwen_code_config,
)

from .code_executor_contracts import (
    _append_contract_artifact_paths,
    _build_ad_hoc_execution_spec,
    _build_cli_contract_repair_task,
    _build_cli_task_contract,
    _build_execution_spec,
    _build_verification_artifact_paths,
    _contract_required_artifact_records,
    _extract_acceptance_criteria_from_node,
    _extract_code_workspace_metadata,
    _extract_task_referenced_read_dirs,
    _final_response_contract_prompt,
    _format_cli_acceptance_checks,
    _format_contract_diff_for_cli,
    _format_resolved_resources_for_prompt,
    _format_supervised_ml_contract_for_prompt,
    _generate_task_dir_name_llm,
    _is_allowed_task_read_path,
    _is_path_within,
    _is_path_within_lexical,
    _is_verification_only_task,
    _normalize_resolved_resources,
    _resource_read_dirs,
    _rerun_update_mode_prompt,
    _sanitize_task_dir_component,
    _summarize_dependency_blockers,
    _validate_scope_contract,
    _COMPLETED_TASK_STATUSES,
)
from .code_executor_promotion import (
    _MAX_SESSION_PROMOTE_FILES,
    _MAX_SESSION_PROMOTE_FILE_BYTES,
    _MAX_STALE_SESSION_ROOT_FILE_BYTES,
    _RUN_PREFIX_RE,
    _UNIFIED_PROMOTE_EXCLUDE_PATTERNS,
    _build_search_and_generate_prompt,
    _collect_non_semantic_run_files,
    _collect_run_artifacts,
    _collapse_rooted_rel_path,
    _has_hidden_path_component,
    _iter_promotable_run_files,
    _prune_stale_session_root_results,
    _promote_external_contract_artifacts,
    _promote_project_level_strays,
    _promote_results_to_unified_dir,
    _promote_task_results_to_session_root,
    _reconcile_deliverables,
    _recover_files_from_historical_runs,
    _resolve_promoted_output_files,
    _resolve_runtime_session_dir,
    _should_skip_unified_promoted_file,
)

# Historical helper names remain import-compatible; canonical implementations
# live in the contracts sibling.
_legacy_format_supervised_ml_contract_for_prompt = _format_supervised_ml_contract_for_prompt
_legacy_build_cli_task_contract = _build_cli_task_contract
_legacy_final_response_contract_prompt = _final_response_contract_prompt
_legacy_rerun_update_mode_prompt = _rerun_update_mode_prompt
_legacy_format_contract_diff_for_cli = _format_contract_diff_for_cli
_legacy_is_verification_only_task = _is_verification_only_task
_legacy_build_cli_contract_repair_task = _build_cli_contract_repair_task
_legacy_validate_scope_contract = _validate_scope_contract

async def code_executor_handler(
    task: str,
    allowed_tools: Optional[Any] = None,
    add_dirs: Optional[Any] = None,
    docker_image: Optional[str] = None,
    skip_permissions: bool = True,
    output_format: str = "json",
    session_id: Optional[str] = None,
    plan_id: Optional[int] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    model: Optional[str] = None,
    setting_sources: Optional[str] = None,
    auth_mode: Optional[str] = None,
    require_task_context: bool = True,
    on_stdout: Optional[Callable[[str], Awaitable[None]]] = None,
    on_stderr: Optional[Callable[[str], Awaitable[None]]] = None,
    tool_context: Optional[Any] = None,
    auto_fix: bool = True,
    resolved_resources: Optional[Dict[str, Any]] = None,
    execution_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a task using Claude Code (official CLI) with local file access.
    
    Args:
        task: Task description for Claude to complete
        allowed_tools: Comma-separated list of allowed tools (e.g. "Bash Edit")
        add_dirs: Comma-separated list of additional directories to allow access
        docker_image: Optional Docker image override for local Docker execution.
        skip_permissions: Skip permission checks (recommended for trusted environments)
        output_format: Output format: "text" or "json"
        session_id: Session ID for workspace isolation
        plan_id: Plan ID for workspace isolation
        task_id: Task ID for workspace isolation
        model: Optional explicit Claude model (or env CLAUDE_CODE_MODEL)
        setting_sources: Optional sources for Claude settings loading
        auth_mode: "api_env" (default) or "claude_login"
        require_task_context: Whether to require plan/task binding for strict atomic execution
        on_stdout: Async callback for stdout lines
        on_stderr: Async callback for stderr lines
        
    Returns:
        Dict containing execution results
    """
    selected_backend, execution_lane, execution_lane_reason = (
        _resolve_code_executor_backend(task, backend_override=execution_backend)
        if execution_backend is not None
        else _resolve_code_executor_backend(task)
    )
    use_local_backend = selected_backend == "local"
    use_qwen_code_backend = selected_backend == "qwen_code"

    log_file = None
    log_path = None
    log_lock = asyncio.Lock()
    logger.info(
        "code_executor selected backend=%s lane=%s reason=%s",
        selected_backend,
        execution_lane,
        execution_lane_reason,
    )

    try:
        try:
            resolved_plan_id = _coerce_positive_int(plan_id, field_name="plan_id")
            resolved_task_id = _coerce_positive_int(task_id, field_name="task_id")
        except ValueError as exc:
            return {
                "success": False,
                "error": f"Invalid task context: {exc}",
                "task": task,
            }

        scope_error = _validate_scope_contract(
            plan_id=resolved_plan_id,
            task_id=resolved_task_id,
            require_task_context=require_task_context,
        )
        if scope_error:
            return {
                "success": False,
                "error": scope_error,
                "blocked_by_scope_guardrail": True,
                "blocked_reason": "missing_atomic_context",
                "task": task,
            }

        # Ensure runtime directory exists
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        try:
            session_dir = _resolve_runtime_session_dir(session_id)
        except ValueError as exc:
            return {
                "success": False,
                "error": f"Invalid session_id: {exc}",
                "task": task,
            }
        _prune_stale_session_root_results(session_dir=session_dir)

        # Keep a stable per-task root and isolate each execution by run_<timestamp>.
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f") + f"_{uuid4().hex[:8]}"

        effective_session_id = session_id or "adhoc"

        # --- Unified output path: determine final output directory via PathRouter ---
        path_router = get_path_router()
        unified_output_dir: Optional[Path] = None
        if resolved_task_id is not None:
            unified_output_dir = path_router.get_task_output_dir(
                effective_session_id, resolved_task_id, ancestor_chain, create=True
            )
        else:
            unified_output_dir = path_router.get_tmp_output_dir(
                effective_session_id, run_id=run_id, create=True
            )

        # Legacy task_dir_base for scratch workspace naming (execution happens here)
        task_dir_base = None
        if resolved_task_id is not None:
            task_dir_base = f"task{resolved_task_id}"
            if resolved_plan_id is not None:
                task_dir_base = f"plan{resolved_plan_id}_{task_dir_base}"
        else:
            task_dir_name = await _generate_task_dir_name_llm(task)
            if resolved_plan_id is not None:
                task_dir_name = f"plan{resolved_plan_id}_{task_dir_name}"
            task_dir_base = task_dir_name

        execution_spec = _build_execution_spec(
            resolved_plan_id,
            resolved_task_id,
            task_text=task,
            session_id=session_id,
        )

        # Scratch workspace: execution happens here, results promoted to unified_output_dir
        scratch_root = session_dir / "_scratch"
        task_root_dir = scratch_root / task_dir_base
        task_root_dir.mkdir(parents=True, exist_ok=True)

        run_dir_name = f"run_{run_id}"
        task_work_dir = task_root_dir / run_dir_name
        task_work_dir.mkdir(parents=True, exist_ok=True)

        task_subdirs = _derive_task_subdirectories(execution_spec)
        for subdir in task_subdirs:
            (task_work_dir / subdir).mkdir(parents=True, exist_ok=True)

        file_prefix = run_dir_name

        logger.info(f"Using task workspace: {task_work_dir}")
        if unified_output_dir:
            logger.info(f"Unified output directory: {unified_output_dir}")

        debug_log_path: Optional[Path] = None

        effective_execution_session_id = str(session_id or "adhoc").strip() or "adhoc"

        try:
            job_id = get_current_job()
            if job_id:
                _LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_path = _LOG_DIR / f"{job_id}.log"
            else:
                log_path = task_work_dir / "results" / f"{file_prefix}_code_executor.log"
            debug_log_path = task_work_dir / "results" / f"{file_prefix}_claude_debug.log"

            log_file = open(log_path, "a", encoding="utf-8")
            log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code started\n")
            log_file.write(f"task: {task}\n")
            log_file.write(f"workspace: {task_work_dir}\n")
            log_file.write(f"debug_log: {debug_log_path}\n")
            log_file.flush()
            log_job_event("info", "Claude Code log file initialized.", {"log_path": str(log_path)})
            log_job_event("info", "Claude Code process starting.", {"workspace": str(task_work_dir)})
        except Exception as log_exc:
            logger.warning(f"Failed to initialize Claude Code log file: {log_exc}")

        # Normalize optional CLI params (supports both string and list inputs)
        normalized_allowed_tools = _resolve_allowed_tools(allowed_tools)
        if not normalized_allowed_tools:
            return {
                "success": False,
                "error": "No allowed tools remain after strict allowlist filtering.",
                "task": task,
            }
        read_context = _prepare_code_executor_read_context(
            task=task,
            add_dirs=add_dirs,
            resolved_resources=resolved_resources,
            require_task_context=require_task_context,
            execution_spec=execution_spec,
            session_dir=session_dir,
        )
        normalized_resources = read_context["normalized_resources"]
        allowed_dirs = read_context["allowed_dirs"]
        allowed_dirs_info = read_context["allowed_dirs_info"]
        local_data_dir = read_context["local_data_dir"]

        cli_task = _build_cli_task_contract(task, execution_spec, normalized_resources)

        if use_local_backend:
            local_result = await _execute_task_locally(
                task=task,
                work_dir=str(task_work_dir),
                data_dir=local_data_dir,
                extra_dirs=allowed_dirs,
                docker_image=docker_image,
                tool_context=tool_context,
                auto_fix=auto_fix,
                session_dir=str(session_dir),
                execution_spec=execution_spec,
                resolved_resources=normalized_resources,
            )
            produced_files = _collect_run_artifacts(
                run_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            code_directory, primary_code_file = _extract_code_workspace_metadata(
                run_dir=task_work_dir,
                produced_files=produced_files,
            )

            # --- Promote results to unified output directory ---
            unified_promoted_files: List[str] = []
            if unified_output_dir:
                unified_promoted_files = _promote_results_to_unified_dir(
                    scratch_dir=task_work_dir,
                    output_dir=unified_output_dir,
                    subdirs=task_subdirs,
                    session_dir=session_dir,
                )

            # Legacy promotion (deprecated - will be removed in Phase 7)
            logger.debug(
                "DEPRECATED: _promote_task_results_to_session_root called for task %s. "
                "This redundant copy will be removed in a future phase.",
                resolved_task_id,
            )
            session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            reconcile_report = _reconcile_deliverables(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                unified_output_dir=unified_output_dir,
            )
            if reconcile_report.get("missing"):
                logger.warning(
                    "[CODE_EXECUTOR_LOCAL] %d deliverables still missing after reconciliation: %s",
                    len(reconcile_report["missing"]),
                    reconcile_report["missing"][:5],
                )
            verification_artifact_paths = _build_verification_artifact_paths(
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
                produced_files=produced_files,
                session_artifact_paths=session_artifact_paths,
                session_dir=session_dir,
            )
            contract_artifacts = _contract_required_artifact_records(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                produced_files=produced_files,
            )
            _append_contract_artifact_paths(verification_artifact_paths, contract_artifacts)
            for skipped_path in _skipped_large:
                if skipped_path not in verification_artifact_paths:
                    verification_artifact_paths.append(skipped_path)
            if unified_output_dir and contract_artifacts:
                _promote_external_contract_artifacts(
                    contract_artifacts=contract_artifacts,
                    task_work_dir=task_work_dir,
                    unified_output_dir=unified_output_dir,
                )
                _promote_project_level_strays(
                    contract_artifacts=contract_artifacts,
                    unified_output_dir=unified_output_dir,
                    project_root=_PROJECT_ROOT,
                )
            result_payload = _build_local_backend_result_payload(
                task=task,
                local_result=local_result,
                resolved_plan_id=resolved_plan_id,
                resolved_task_id=resolved_task_id,
                require_task_context=require_task_context,
                task_dir_base=task_dir_base,
                task_work_dir=task_work_dir,
                task_root_dir=task_root_dir,
                run_id=run_id,
                file_prefix=file_prefix,
                task_subdirs=task_subdirs,
                session_dir=session_dir,
                execution_lane=execution_lane,
                execution_lane_reason=execution_lane_reason,
                log_path=log_path,
                normalized_allowed_tools=normalized_allowed_tools,
                code_directory=code_directory,
                primary_code_file=primary_code_file,
                produced_files=produced_files,
                verification_artifact_paths=verification_artifact_paths,
                contract_artifacts=contract_artifacts,
                session_artifact_paths=session_artifact_paths,
                unified_output_dir=unified_output_dir,
                unified_promoted_files=unified_promoted_files,
                effective_session_id=effective_session_id,
                ancestor_chain=ancestor_chain,
                execution_spec=execution_spec,
            )

            if log_file:
                try:
                    log_file.write(
                        f"[{datetime.utcnow().isoformat()}Z] Local code execution finished\n"
                    )
                    log_file.flush()
                    log_file.close()
                except Exception as log_err:
                    logger.warning("Failed to finalize local code executor log file: %s", log_err)

            return result_payload

        # ---- Build CLI command and environment ----
        _docker_container_name: Optional[str] = None  # set when running inside container
        _qwen_session_id: Optional[str] = None
        _container_execution_lock: Optional[asyncio.Lock] = None
        if use_qwen_code_backend:
            # Qwen Code path
            subprocess_env = _build_qwen_code_subprocess_env(
                model_provider=getattr(tool_context, 'model_provider', None) if tool_context else None
            )
            _ensure_writable_subprocess_cache_env(subprocess_env, task_work_dir)
            _inject_env_mutation_guard(subprocess_env, str(task_work_dir))
            qc_config_error = _validate_qwen_code_config(subprocess_env)
            if qc_config_error:
                return {"success": False, "error": qc_config_error, "task": task}
            _mp = (getattr(tool_context, 'model_provider', None) or {}) if tool_context else {}
            effective_model = (
                str(
                    model
                    or _mp.get("model", "")
                    or os.getenv("QWEN_CODE_MODEL", "")
                    or os.getenv("QWEN_MODEL", "")
                ).strip()
                or None
            )
            _qwen_session_id = _build_qwen_execution_session_id(
                effective_execution_session_id,
                run_id,
            )
            def _rebuild_cli_command(task_override: str) -> List[str]:
                bare_cmd = _build_qwen_code_command(
                    task=task_override,
                    work_dir=str(task_work_dir),
                    file_prefix=file_prefix,
                    output_format=output_format,
                    allowed_tools=normalized_allowed_tools,
                    allowed_dirs=allowed_dirs,
                    model=effective_model,
                    debug=debug_log_path is not None,
                    allowed_dirs_info=allowed_dirs_info,
                    qwen_session_id=_qwen_session_id,
                    task_subdirs=task_subdirs,
                    execution_spec=execution_spec,
                    resolved_resources=normalized_resources,
                )
                # Wrap with docker exec if running inside a container
                if _docker_container_name:
                    from app.services.terminal.docker_pty_backend import (
                        CONTAINER_EXEC_PATH,
                        QWEN_EXECUTABLE,
                    )
                    docker_exec_cmd = [
                        "docker",
                        "exec",
                        "-e",
                        f"PATH={CONTAINER_EXEC_PATH}",
                    ]
                    # The project .env may contain host-local proxy settings such
                    # as 127.0.0.1:7890. Inside the qwen_code container that
                    # address points at the container itself and breaks Node/OpenAI
                    # fetches. Clear proxy variables for the qwen process while
                    # preserving the container's OPENAI_* credentials.
                    for _proxy_key in (
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "ALL_PROXY",
                        "http_proxy",
                        "https_proxy",
                        "all_proxy",
                    ):
                        docker_exec_cmd.extend(["-e", f"{_proxy_key}="])
                    docker_exec_cmd.extend([
                        "-w",
                        str(task_work_dir),
                        _docker_container_name,
                        QWEN_EXECUTABLE,
                    ])
                    return docker_exec_cmd + bare_cmd[1:]
                return bare_cmd
            cmd = _rebuild_cli_command(task)
            _cli_label = "Qwen Code"
            _diag_key = subprocess_env.get("OPENAI_API_KEY", "")
            _diag_url = subprocess_env.get("OPENAI_BASE_URL", "")
            logger.info(
                "[CODE_EXECUTOR_DIAG] backend=qwen_code api_key_len=%d base_url=%s model=%s",
                len(_diag_key), _diag_url, effective_model or "(default)",
            )

            # --- Try Docker container execution for isolation + persistence ---
            try:
                from app.services.terminal.qwen_session_driver import get_qwen_session_driver
                _qc_driver = get_qwen_session_driver()
                _container = await _qc_driver.ensure_container(
                    effective_execution_session_id,
                    host_work_dir=str(task_work_dir),
                    extra_mounts=_build_qwen_container_mounts(
                        task_work_dir=task_work_dir,
                        session_dir=session_dir,
                        allowed_dirs=allowed_dirs,
                    ),
                )
                _docker_container_name = _container
                _container_execution_lock = _qc_driver.get_execution_lock(effective_execution_session_id)
                # Rebuild cmd — _rebuild_cli_command now wraps with docker exec
                cmd = _rebuild_cli_command(task)
                # Env is inside the container; host subprocess only needs docker binary
                subprocess_env = dict(os.environ)
                _ensure_writable_subprocess_cache_env(subprocess_env, task_work_dir)
                _cli_label = "Qwen Code (container)"
                logger.info(
                    "[CODE_EXECUTOR] Using Docker container %s for qwen_code execution",
                    _container,
                )
            except (RuntimeError, OSError, asyncio.TimeoutError) as _docker_err:
                logger.warning(
                    "[CODE_EXECUTOR] Docker container unavailable (%s), "
                    "falling back to host subprocess",
                    _docker_err,
                )
        else:
            # Legacy Claude Code path
            effective_auth_mode = _resolve_auth_mode(auth_mode)
            subprocess_env = _build_code_executor_subprocess_env(effective_auth_mode)
            _ensure_writable_subprocess_cache_env(subprocess_env, task_work_dir)
            _inject_env_mutation_guard(subprocess_env, str(task_work_dir))
            if effective_auth_mode == "api_env":
                api_mode_error = _validate_api_mode_config(subprocess_env)
                if api_mode_error:
                    return {"success": False, "error": api_mode_error, "task": task}
            effective_model = (
                str(
                    model
                    or os.getenv("CLAUDE_CODE_MODEL", "")
                    or os.getenv("CLAUDE_CODE_API_MODEL", "")
                    or subprocess_env.get("ANTHROPIC_MODEL", "")
                ).strip()
                or None
            )
            effective_setting_sources = _resolve_setting_sources(
                setting_sources, auth_mode=effective_auth_mode,
            )
            def _rebuild_cli_command(task_override: str) -> List[str]:
                return _build_claude_code_command(
                    task=task_override,
                    task_work_dir=task_work_dir,
                    file_prefix=file_prefix,
                    output_format=output_format,
                    normalized_allowed_tools=normalized_allowed_tools,
                    allowed_dirs=allowed_dirs,
                    task_subdirs=task_subdirs,
                    execution_spec=execution_spec,
                    resolved_resources=normalized_resources,
                    allowed_dirs_info=allowed_dirs_info,
                    debug_log_path=debug_log_path,
                    effective_model=effective_model,
                    effective_setting_sources=effective_setting_sources,
                    skip_permissions=skip_permissions,
                )
            cmd = _rebuild_cli_command(task)
            _cli_label = "Claude Code"
            _diag_key = subprocess_env.get("ANTHROPIC_API_KEY", "")
            _diag_url = subprocess_env.get("ANTHROPIC_BASE_URL", "")
            _diag_model = subprocess_env.get("ANTHROPIC_MODEL", "")
            _diag_small_fast_model = subprocess_env.get("ANTHROPIC_SMALL_FAST_MODEL", "")
            logger.info(
                "[CODE_EXECUTOR_DIAG] backend=claude_code api_key_len=%d base_url=%s model=%s "
                "small_fast_model=%s auth_mode=%s setting_sources=%s",
                len(_diag_key), _diag_url, _diag_model, _diag_small_fast_model,
                effective_auth_mode, effective_setting_sources,
            )

        logger.info("[CODE_EXECUTOR_CLI] Executing %s in: %s", _cli_label, task_work_dir)

        # Retry logic for transient provider / CLI failures.
        max_cli_retries, cli_retry_base_delay_s = _resolve_cli_retry_policy()
        qwen_shell_recovery: Optional[Dict[str, Any]] = None

        cli_progress = {"last_output_at": 0.0}
        cli_prompt_tokens_accumulated = 0

        async def _record_stream_line(decoded_line: str, lines, callback, stream_name: str):
            try:
                cli_progress["last_output_at"] = asyncio.get_running_loop().time()
            except RuntimeError:
                pass
            lines.append(decoded_line)
            formatted_line = f"[{stream_name}] {decoded_line}" if decoded_line else f"[{stream_name}]"
            if log_file:
                try:
                    async with log_lock:
                        log_file.write(formatted_line + "\n")
                        log_file.flush()
                except Exception as log_err:
                    logger.warning(f"Failed to write Claude Code log line: {log_err}")
            if callback:
                try:
                    capped_line = formatted_line
                    if len(capped_line) > 4000:
                        capped_line = capped_line[:3997] + "..."
                    await callback(capped_line)
                except Exception as e:
                    logger.error(f"Error in stream callback: {e}")

        async def _read_stream(stream, lines, callback, stream_name: str):
            async for decoded_line in _iter_stream_lines_unbounded(stream):
                await _record_stream_line(decoded_line, lines, callback, stream_name)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        return_code = -1

        @asynccontextmanager
        async def _maybe_hold_execution_lock():
            if _container_execution_lock is None:
                yield
                return
            async with _container_execution_lock:
                yield

        async def _run_cli_with_retry(
            task_override: str,
            *,
            phase: str = "primary",
        ) -> tuple[int, str, str, Optional[Dict[str, Any]]]:
            nonlocal _qwen_session_id, qwen_shell_recovery, cli_prompt_tokens_accumulated
            local_stdout_lines: list[str] = []
            local_stderr_lines: list[str] = []
            local_return_code = -1

            for _attempt in range(1, max_cli_retries + 2):
                if use_qwen_code_backend:
                    if _attempt == 1:
                        _qwen_session_id = _build_qwen_execution_session_id(
                            effective_execution_session_id,
                            run_id,
                            phase=phase,
                        )
                    command = _rebuild_cli_command(task_override)
                else:
                    command = _rebuild_cli_command(task_override)
                local_stdout_lines.clear()
                local_stderr_lines.clear()
                if use_qwen_code_backend:
                    cli_prompt_tokens_accumulated += _estimate_cli_prompt_tokens(command)

                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(task_work_dir),
                    env=subprocess_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    cli_progress["last_output_at"] = asyncio.get_running_loop().time()
                except RuntimeError:
                    pass

                stdout_task = asyncio.create_task(
                    _read_stream(process.stdout, local_stdout_lines, on_stdout, "stdout")
                )
                stderr_task = asyncio.create_task(
                    _read_stream(process.stderr, local_stderr_lines, on_stderr, "stderr")
                )

                try:
                    drain_task = asyncio.gather(stdout_task, stderr_task)
                    grace_deadline: Optional[float] = None
                    can_finish_from_contract_outputs = (
                        use_qwen_code_backend
                        and isinstance(execution_spec, dict)
                        and bool(execution_spec.get("acceptance_criteria"))
                    )
                    if use_qwen_code_backend:
                        return_code_override = await _wait_for_qwen_cli_drain_or_watchdog(
                            process=process,
                            drain_task=drain_task,
                            stdout_task=stdout_task,
                            stderr_task=stderr_task,
                            local_stdout_lines=local_stdout_lines,
                            local_stderr_lines=local_stderr_lines,
                            cli_progress=cli_progress,
                            can_finish_from_contract_outputs=can_finish_from_contract_outputs,
                            execution_spec=execution_spec,
                            task_work_dir=task_work_dir,
                            unified_output_dir=unified_output_dir,
                            cli_label=_cli_label,
                            container_name=_docker_container_name,
                            log_file=log_file,
                            log_lock=log_lock,
                        )
                        if return_code_override is not None:
                            local_return_code = return_code_override
                    else:
                        await drain_task
                    if local_return_code != 0:
                        local_return_code = await _wait_for_cli_process_return_code(
                            process,
                            backend_label=_cli_label,
                        )
                except (asyncio.CancelledError, Exception) as _wait_exc:
                    try:
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=10.0)
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                            timeout=10.0,
                        )
                    except asyncio.TimeoutError:
                        stdout_task.cancel()
                        stderr_task.cancel()
                        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                    if isinstance(_wait_exc, asyncio.CancelledError):
                        raise
                    raise

                if local_return_code == 0:
                    break

                _is_scope_block = _detect_scope_blocked("\n".join(local_stdout_lines), None)
                if _is_scope_block:
                    logger.info("[CODE_EXECUTOR_RETRY] Scope block detected, not retrying.")
                    break

                if use_qwen_code_backend and _is_qwen_truncated_tool_failure_text("\n".join(local_stderr_lines)):
                    logger.warning(
                        "[CODE_EXECUTOR_RETRY] Qwen truncated tool call detected; "
                        "skipping transcript shell recovery and retries."
                    )
                    break

                if use_qwen_code_backend and not _is_qwen_no_output_timeout("\n".join(local_stderr_lines)):
                    qwen_shell_recovery = await _recover_pending_qwen_shell_call(
                        qwen_session_id=_qwen_session_id,
                        container_name=_docker_container_name,
                        task_work_dir=str(task_work_dir),
                    )
                    if isinstance(qwen_shell_recovery, dict):
                        logger.warning(
                            "[CODE_EXECUTOR] Replaying pending qwen run_shell_command after CLI exit "
                            "(session=%s run=%s phase=%s attempt=%d)",
                            effective_execution_session_id,
                            run_id,
                            phase,
                            _attempt,
                        )
                        if log_file:
                            try:
                                async with log_lock:
                                    log_file.write(
                                        f"[{datetime.utcnow().isoformat()}Z] Replaying pending qwen shell call "
                                        f"(attempt={_attempt}, timeout_ms={qwen_shell_recovery.get('timeout_ms')})\n"
                                    )
                                    log_file.write(
                                        f"[recovery] command={qwen_shell_recovery.get('command')}\n"
                                    )
                                    log_file.flush()
                            except Exception:
                                pass
                        local_return_code = int(qwen_shell_recovery.get("exit_code") or 0)
                        local_stdout_lines.clear()
                        local_stderr_lines.clear()
                        recovered_stdout = str(qwen_shell_recovery.get("stdout") or "")
                        recovered_stderr = str(qwen_shell_recovery.get("stderr") or "")
                        for decoded_line in recovered_stdout.splitlines():
                            await _record_stream_line(decoded_line, local_stdout_lines, on_stdout, "stdout")
                        for decoded_line in recovered_stderr.splitlines():
                            await _record_stream_line(decoded_line, local_stderr_lines, on_stderr, "stderr")
                        break

                if _is_qwen_no_output_timeout("\n".join(local_stderr_lines)):
                    logger.warning(
                        "[CODE_EXECUTOR_RETRY] Qwen no-output watchdog fired; "
                        "skipping transcript recovery and retries."
                    )
                    break

                if _attempt <= max_cli_retries:
                    stderr_text = "\n".join(local_stderr_lines)
                    if use_qwen_code_backend and _is_qwen_session_in_use_error(stderr_text):
                        _qwen_session_id = _build_qwen_execution_session_id(
                            effective_execution_session_id,
                            run_id,
                            phase=phase,
                            retry_attempt=_attempt,
                        )
                        logger.warning(
                            "[CODE_EXECUTOR_RETRY] Rotating qwen session-id after in-use conflict "
                            "(session=%s run=%s phase=%s attempt=%d)",
                            effective_execution_session_id,
                            run_id,
                            phase,
                            _attempt,
                        )
                    retry_delay_s = min(cli_retry_base_delay_s * (2 ** (_attempt - 1)), 30.0)
                    logger.warning(
                        "[CODE_EXECUTOR_RETRY] CLI failed (attempt %d/%d, exit=%d). "
                        "Retrying in %.1fs... stderr_hint=%s",
                        _attempt, max_cli_retries + 1, local_return_code,
                        retry_delay_s, _extract_readable_error(stderr_text)[:200],
                    )
                    if log_file:
                        try:
                            log_file.write(
                                f"[{datetime.utcnow().isoformat()}Z] Retry {_attempt}/{max_cli_retries} "
                                f"after exit={local_return_code}, waiting {retry_delay_s:.1f}s\n"
                            )
                            log_file.flush()
                        except Exception:
                            pass
                    await asyncio.sleep(retry_delay_s)
                else:
                    logger.error(
                        "[CODE_EXECUTOR_RETRY] CLI failed after %d attempts (exit=%d).",
                        _attempt, local_return_code,
                    )

            local_stdout = "\n".join(local_stdout_lines)
            local_stderr = "\n".join(local_stderr_lines)
            local_output_data = None
            if output_format == "json" and local_stdout:
                try:
                    local_output_data = json.loads(local_stdout)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse JSON output, using raw text")
                    local_output_data = {"raw_output": local_stdout}
            return local_return_code, local_stdout, local_stderr, local_output_data

        async with _maybe_hold_execution_lock():
            return_code, stdout, stderr, output_data = await _run_cli_with_retry(task)

            success = return_code == 0
            is_no_output_timeout = _is_qwen_no_output_timeout(stderr, stdout)
            is_qwen_truncated_tool_failure = _is_qwen_truncated_tool_failure_text(f"{stderr}\n{stdout}")

            blocked_detail = _detect_scope_blocked(stdout, output_data)
            if blocked_detail:
                success = False

            qwen_infra_failure_detected = (
                use_qwen_code_backend
                and not success
                and (
                    _is_qwen_container_infrastructure_error(stderr, stdout)
                    or is_qwen_truncated_tool_failure
                )
            )
            qwen_infra_fallback_used = False
            fallback_result: Optional[Dict[str, Any]] = None
            if _should_fallback_from_qwen_infra_failure(
                use_qwen_code_backend=use_qwen_code_backend,
                success=success,
                stderr=stderr,
                stdout=stdout,
                execution_lane=execution_lane,
                execution_lane_reason=execution_lane_reason,
            ):
                logger.warning(
                    "[CODE_EXECUTOR_FALLBACK] qwen_code infrastructure failure detected; "
                    "retrying task with local executor (session=%s run=%s)",
                    effective_execution_session_id,
                    run_id,
                )
                if log_file:
                    try:
                        async with log_lock:
                            log_file.write(
                                f"[{datetime.utcnow().isoformat()}Z] "
                                "qwen_code infrastructure failure detected; retrying with local executor\n"
                            )
                            log_file.flush()
                    except Exception:
                        pass
                fallback_result = await _execute_task_locally(
                    task=task,
                    work_dir=str(task_work_dir),
                    data_dir=local_data_dir,
                    extra_dirs=allowed_dirs,
                    docker_image=docker_image,
                    tool_context=tool_context,
                    auto_fix=auto_fix,
                    session_dir=str(session_dir),
                    execution_spec=execution_spec,
                    resolved_resources=normalized_resources,
                )
                fallback_stdout = str(fallback_result.get("stdout") or "")
                fallback_stderr = str(fallback_result.get("stderr") or "")
                stdout = (stdout + "\n" if stdout else "") + "[fallback:local] " + fallback_stdout
                stderr = (stderr + "\n" if stderr else "") + fallback_stderr
                return_code = int(fallback_result.get("exit_code", 0 if fallback_result.get("success") else 1) or 0)
                success = bool(fallback_result.get("success", False))
                qwen_infra_fallback_used = True
                output_data = fallback_result.get("output_data") or output_data
                blocked_detail = _detect_scope_blocked(stdout, output_data)
                if blocked_detail:
                    success = False

            produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
            success, execution_failure = _classify_execution_success(
                stdout=stdout,
                output_data=output_data,
                execution_spec=execution_spec,
                produced_files=produced_files,
                success=success,
                task_work_dir=task_work_dir,
            )

            if (
                execution_failure
                and execution_failure.get("failure_kind") == "missing_required_outputs"
                and task_root_dir.exists()
            ):
                recovered = _recover_files_from_historical_runs(
                    task_root_dir=task_root_dir,
                    current_run_dir=task_work_dir,
                    execution_spec=execution_spec,
                    task_subdirs=task_subdirs,
                )
                if recovered:
                    logger.info(
                        "[CODE_EXECUTOR] Recovered %d files from historical runs, re-verifying",
                        len(recovered),
                    )
                    produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
                    success, execution_failure = _classify_execution_success(
                        stdout=stdout,
                        output_data=output_data,
                        execution_spec=execution_spec,
                        produced_files=produced_files,
                        success=success,
                        task_work_dir=task_work_dir,
                    )
                    if success:
                        logger.info("[CODE_EXECUTOR] Verification passed after historical file recovery")

            # --- Promote results to unified output directory ---
            unified_promoted_files_qwen: List[str] = []
            if unified_output_dir:
                unified_promoted_files_qwen = _promote_results_to_unified_dir(
                    scratch_dir=task_work_dir,
                    output_dir=unified_output_dir,
                    subdirs=task_subdirs,
                    session_dir=session_dir,
                )

            session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            reconcile_report = _reconcile_deliverables(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                unified_output_dir=unified_output_dir,
            )
            if reconcile_report.get("missing"):
                sg_prompt = _build_search_and_generate_prompt(
                    reconcile_report["missing"],
                    session_dir,
                    execution_spec,
                    is_timeout=is_no_output_timeout or is_qwen_truncated_tool_failure,
                )
                logger.info(
                    "[CODE_EXECUTOR] %d missing deliverables, delegating to Qwen Code for %s",
                    len(reconcile_report["missing"]),
                    "re-execution (previous timeout/fatal CLI failure)" if (is_no_output_timeout or is_qwen_truncated_tool_failure) else "search",
                )
                try:
                    sg_rc, sg_stdout, sg_stderr, sg_output = await asyncio.wait_for(
                        _run_cli_with_retry(
                            sg_prompt, phase="search_generate",
                        ),
                        timeout=300.0,
                    )
                    produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
                    if unified_output_dir:
                        unified_promoted_files_qwen = _promote_results_to_unified_dir(
                            scratch_dir=task_work_dir,
                            output_dir=unified_output_dir,
                            subdirs=task_subdirs,
                            session_dir=session_dir,
                        )
                    session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                        session_dir=session_dir,
                        task_work_dir=task_work_dir,
                        subdirs=task_subdirs,
                    )
                    reconcile_report = _reconcile_deliverables(
                        execution_spec=execution_spec,
                        task_work_dir=task_work_dir,
                        unified_output_dir=unified_output_dir,
                    )
                    logger.info(
                        "[CODE_EXECUTOR] %s complete: rc=%d still_missing=%d",
                        "re-execution" if is_no_output_timeout else "search",
                        sg_rc,
                        len(reconcile_report.get("missing", [])),
                    )
                except asyncio.TimeoutError:
                    logger.warning("[CODE_EXECUTOR] search/generate delegation timed out after 300s")
                except Exception as sg_exc:
                    logger.warning("[CODE_EXECUTOR] search/generate delegation failed: %s", sg_exc)
            verification_artifact_paths = _build_verification_artifact_paths(
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
                produced_files=produced_files,
                session_artifact_paths=session_artifact_paths,
                session_dir=session_dir,
            )
            contract_artifacts = _contract_required_artifact_records(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                produced_files=produced_files,
            )
            _append_contract_artifact_paths(verification_artifact_paths, contract_artifacts)
            for skipped_path in _skipped_large:
                if skipped_path not in verification_artifact_paths:
                    verification_artifact_paths.append(skipped_path)
            code_directory, primary_code_file = _extract_code_workspace_metadata(
                run_dir=task_work_dir,
                produced_files=produced_files,
            )
            execution_status = "completed" if return_code == 0 else "failed"
            verification_status: Optional[str] = None
            failure_kind: Optional[str] = None
            contract_diff: Optional[Dict[str, Any]] = None
            verification: Optional[Dict[str, Any]] = None
            repair_attempts = 0
            plan_patch_suggestion: Optional[str] = None
            contract_error_summary: Optional[str] = None
            contract_fix_guidance: Optional[str] = None

            if success and execution_spec and execution_spec.get("acceptance_criteria"):
                try:
                    from app.services.interpreter.code_execution import (
                        CodeExecutionSpec,
                        _extract_verification_state,
                        _format_verification_guidance,
                        _summarize_verification_failures,
                        _verify_execution_against_contract,
                    )

                    finalization = _verify_execution_against_contract(
                        execution_spec=CodeExecutionSpec(
                            plan_id=execution_spec.get("plan_id"),
                            task_id=execution_spec.get("task_id"),
                            task_name=execution_spec.get("task_name"),
                            task_instruction=execution_spec.get("task_instruction"),
                            acceptance_criteria=execution_spec.get("acceptance_criteria"),
                            dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
                            dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
                        ),
                        work_dir=str(task_work_dir),
                    )
                    verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion = (
                        _extract_verification_state(finalization)
                    )
                    if finalization.final_status == "failed" and auto_fix:
                        repair_attempts = 1
                        contract_error_summary = _summarize_verification_failures(verification)
                        contract_fix_guidance = _format_verification_guidance(verification)
                        repair_task = _build_cli_contract_repair_task(
                            task,
                            execution_spec,
                            contract_diff=contract_diff,
                            guidance=contract_fix_guidance,
                        )
                        return_code, stdout, stderr, output_data = await _run_cli_with_retry(
                            repair_task,
                            phase="repair",
                        )
                        success = return_code == 0
                        execution_status = "completed" if return_code == 0 else "failed"
                        blocked_detail = _detect_scope_blocked(stdout, output_data)
                        if blocked_detail:
                            success = False
                        produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
                        # Re-promote to unified output dir after repair
                        if unified_output_dir:
                            unified_promoted_files_qwen = _promote_results_to_unified_dir(
                                scratch_dir=task_work_dir,
                                output_dir=unified_output_dir,
                                subdirs=task_subdirs,
                                session_dir=session_dir,
                            )
                        session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                            session_dir=session_dir,
                            task_work_dir=task_work_dir,
                            subdirs=task_subdirs,
                        )
                        _reconcile_deliverables(
                            execution_spec=execution_spec,
                            task_work_dir=task_work_dir,
                            unified_output_dir=unified_output_dir,
                        )
                        verification_artifact_paths = _build_verification_artifact_paths(
                            task_work_dir=task_work_dir,
                            subdirs=task_subdirs,
                            produced_files=produced_files,
                            session_artifact_paths=session_artifact_paths,
                            session_dir=session_dir,
                        )
                        contract_artifacts = _contract_required_artifact_records(
                            execution_spec=execution_spec,
                            task_work_dir=task_work_dir,
                            produced_files=produced_files,
                        )
                        _append_contract_artifact_paths(verification_artifact_paths, contract_artifacts)
                        for skipped_path in _skipped_large:
                            if skipped_path not in verification_artifact_paths:
                                verification_artifact_paths.append(skipped_path)
                        success, execution_failure = _classify_execution_success(
                            stdout=stdout,
                            output_data=output_data,
                            execution_spec=execution_spec,
                            produced_files=produced_files,
                            success=success,
                            task_work_dir=task_work_dir,
                        )
                        if success:
                            finalization = _verify_execution_against_contract(
                                execution_spec=CodeExecutionSpec(
                                    plan_id=execution_spec.get("plan_id"),
                                    task_id=execution_spec.get("task_id"),
                                    task_name=execution_spec.get("task_name"),
                                    task_instruction=execution_spec.get("task_instruction"),
                                    acceptance_criteria=execution_spec.get("acceptance_criteria"),
                                    dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
                                    dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
                                ),
                                work_dir=str(task_work_dir),
                            )
                            verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion = (
                                _extract_verification_state(finalization)
                            )
                            artifact_summary = verification.get("artifact_verification") if isinstance(verification, dict) else {}
                            unresolved_required_outputs = (
                                artifact_summary.get("missing_required_outputs")
                                if isinstance(artifact_summary, dict)
                                else []
                            )
                            if (
                                finalization.final_status == "failed"
                                or verification_status == "failed"
                                or bool(unresolved_required_outputs)
                            ):
                                success = False
                                verification_payload = verification if isinstance(verification, dict) else {}
                                contract_error_summary = _summarize_verification_failures(verification_payload)
                                contract_fix_guidance = _format_verification_guidance(verification_payload)
                        else:
                            verification_status = "not_run"
                            failure_kind = "execution_failed"
                            contract_diff = None
                            verification = None
                            plan_patch_suggestion = None
                            contract_error_summary = None
                            contract_fix_guidance = None
                        if not success and not blocked_detail and contract_error_summary is None and verification_status == "failed":
                            verification_payload = verification if isinstance(verification, dict) else {}
                            contract_error_summary = _summarize_verification_failures(verification_payload)
                            contract_fix_guidance = _format_verification_guidance(verification_payload)
                    elif finalization.final_status == "failed":
                        success = False
                        verification_payload = verification if isinstance(verification, dict) else {}
                        contract_error_summary = _summarize_verification_failures(verification_payload)
                        contract_fix_guidance = _format_verification_guidance(verification_payload)
                except Exception as contract_exc:
                    logger.warning("CLI contract verification failed unexpectedly: %s", contract_exc)

            contract_error_summary, contract_fix_guidance = _clear_stale_contract_failure_state(
                success=success,
                verification_status=verification_status,
                contract_error_summary=contract_error_summary,
                contract_fix_guidance=contract_fix_guidance,
            )

        if unified_output_dir and contract_artifacts:
            _promote_external_contract_artifacts(
                contract_artifacts=contract_artifacts,
                task_work_dir=task_work_dir,
                unified_output_dir=unified_output_dir,
            )

        if log_file:
            try:
                log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code finished (exit={return_code})\n")
                log_file.flush()
            except Exception as log_err:
                logger.warning(f"Failed to finalize Claude Code log file: {log_err}")

        cli_usage = None
        if use_qwen_code_backend:
            real_usage = _parse_cli_usage_from_jsonl(stdout)
            cli_usage = _record_external_cli_usage(
                provider="qwen_code_cli",
                model=effective_model or os.getenv("QWEN_CODE_MODEL") or os.getenv("QWEN_MODEL") or "unknown",
                prompt_tokens=real_usage["prompt_tokens"] if real_usage else cli_prompt_tokens_accumulated,
                completion_tokens=real_usage["completion_tokens"] if real_usage else _estimate_cli_completion_tokens(stdout, stderr),
                session_id=effective_session_id,
                plan_id=resolved_plan_id,
                task_id=resolved_task_id,
                call_purpose="qwen_code_cli_execution",
            )

        # Build return result
        result_payload = {
            "tool": "code_executor",
            "task": task,
            "plan_id": resolved_plan_id,
            "task_id": resolved_task_id,
            "require_task_context": require_task_context,
            "task_directory": task_dir_base,
            "task_directory_full": str(task_work_dir),
            "task_root_directory": str(task_root_dir),
            "run_directory": str(task_work_dir),
            "run_id": run_id,
            "task_subdirectories": task_subdirs,
            "file_prefix": file_prefix,
            "session_directory": str(session_dir),
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "output_data": output_data,
            "exit_code": return_code,
            "execution_backend": "qwen_code" if use_qwen_code_backend else "claude_code",
            "execution_mode": "code_executor_local",
            "execution_lane": execution_lane,
            "execution_lane_reason": execution_lane_reason,
            "working_directory": str(task_work_dir),
            "log_path": str(log_path) if log_path else None,
            "debug_log_path": str(debug_log_path) if debug_log_path else None,
            "allowed_tools_effective": normalized_allowed_tools,
            "cli_model_effective": effective_model,
            "cli_backend": "qwen_code" if use_qwen_code_backend else "claude_code",
            "cli_usage": cli_usage,
            "code_directory": code_directory,
            "code_file": primary_code_file,
            "produced_files": produced_files,
            "produced_files_count": len(produced_files),
            "artifact_paths": verification_artifact_paths,
            "contract_artifacts": contract_artifacts,
            "session_artifact_paths": session_artifact_paths,
            "output_files": _resolve_promoted_output_files(
                unified_promoted_files_qwen, session_dir=session_dir, output_dir=unified_output_dir
            ),
            # Unified output path (new)
            "output_location": {
                "type": "task" if resolved_task_id is not None else "tmp",
                "session_id": effective_session_id,
                "task_id": resolved_task_id,
                "ancestor_chain": ancestor_chain,
                "base_dir": str(unified_output_dir) if unified_output_dir else None,
                "files": unified_promoted_files_qwen,
            },
            "execution_status": execution_status,
                "verification_status": verification_status,
                "failure_kind": failure_kind,
                "contract_diff": contract_diff,
                "verification": verification,
                "artifact_verification": (
                    verification.get("artifact_verification")
                    if isinstance(verification, dict)
                    else None
                ),
                "repair_attempts": repair_attempts,
                "plan_patch_suggestion": plan_patch_suggestion,
            }
        if use_qwen_code_backend and success:
            extracted_result = _extract_result_from_jsonl(stdout)
            if extracted_result:
                result_payload["result"] = extracted_result
            deliverables = _extract_deliverables_from_jsonl(stdout)
            if deliverables:
                result_payload["deliverable_submit"] = {
                    "publish": True,
                    "artifacts": [
                        {
                            "path": d["path"],
                            "module": d["module"],
                            "reason": d.get("description") or "Agent-marked deliverable",
                        }
                        for d in deliverables
                    ],
                }
        if qwen_infra_failure_detected and not qwen_infra_fallback_used:
            result_payload["runtime_failure"] = True
            result_payload["error_category"] = "executor_infrastructure"
            if is_qwen_truncated_tool_failure:
                result_payload["failure_kind"] = result_payload.get("failure_kind") or "qwen_tool_call_truncated"
                result_payload["error_summary"] = (
                    "Qwen Code produced a truncated tool call; retry with split file writes or smaller scripts."
                )
                result_payload["fix_guidance"] = (
                    "Ask Qwen Code to write large scripts in smaller chunks: first create a skeleton, "
                    "then use incremental edits/appends instead of one huge write_file call."
                )
            else:
                result_payload["error_summary"] = _extract_readable_error(stderr) or "qwen_code container infrastructure failure"
        if qwen_infra_fallback_used:
            result_payload["fallback_used"] = True
            fallback_payload = fallback_result or {}
            result_payload["fallback_backend"] = str(fallback_payload.get("execution_backend") or fallback_payload.get("execution_mode") or "local")
            result_payload["fallback_reason"] = "qwen_code_container_infrastructure_failure"
            for key in ("docker_image_effective", "runtime_failure", "error_category", "error_summary", "fix_guidance"):
                if key in fallback_payload and fallback_payload.get(key) is not None and key not in result_payload:
                    result_payload[key] = fallback_payload.get(key)
        if contract_error_summary:
            result_payload["error_category"] = "acceptance_criteria_failed"
            result_payload["error_summary"] = contract_error_summary
            if contract_fix_guidance:
                result_payload["fix_guidance"] = contract_fix_guidance
        if contract_error_summary and not blocked_detail:
            result_payload["error"] = contract_error_summary
        elif not success and not blocked_detail:
            result_payload["error"] = _build_cli_failure_error(
                return_code=return_code,
                stderr=stderr,
                stdout=stdout,
                backend_label=_cli_label,
            )
        _apply_execution_failure_to_payload(result_payload, execution_failure)
        if blocked_detail:
            result_payload["blocked_by_scope_guardrail"] = True
            result_payload["blocked_reason"] = blocked_detail
            result_payload["error"] = f"Blocked by scope guardrail: {blocked_detail}"

        # Detect partial completion signals even when exit_code==0
        completion_info = _detect_partial_completion(
            stdout, stderr, produced_files, success=success,
        )
        if completion_info:
            result_payload.update(completion_info)
            if completion_info.get("partial_completion_suspected"):
                logger.warning(
                    "[CODE_EXECUTOR] Partial completion suspected: ratio=%s warnings=%d files=%d",
                    completion_info.get("partial_ratio", "N/A"),
                    len(completion_info.get("output_warnings", [])),
                    len(produced_files),
                )

        return result_payload

    except subprocess.TimeoutExpired:
        # Should not trigger since timeout=None, but kept as a safeguard
        return {
            "success": False,
            "error": "Code agent CLI execution was interrupted unexpectedly",
            "task": task,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "Code agent CLI (claude/qwen) not found. Install claude-code or qwen-code.",
            "task": task,
        }
    except Exception as e:
        logger.exception(f"Code agent CLI execution failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "task": task,
        }
    finally:
        if log_file:
            try:
                log_file.flush()
                log_file.close()
            except Exception:
                pass


# ToolBox tool definition
code_executor_tool = {
    "name": "code_executor",
    "description": (
        "**PRIMARY TOOL FOR COMPLEX CODING TASKS** - Execute one atomic implementation task using Claude Code. "
        "The runtime enforces a strict tool allowlist and task-scoped workspace isolation. "
        "Use this for data analysis, code generation, model implementation, debugging, and multi-step engineering execution."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed task description for Claude to complete"
            },
            "allowed_tools": {
                "type": "string",
                "description": "Optional comma-separated allowlist request (e.g. 'Bash,Edit'). Values are always filtered by the hard allowlist."
            },
            "add_dirs": {
                "type": "string",
                "description": "Comma-separated list of additional directories to allow access (e.g. 'data/code_task,models')"
            },
        },
        "required": ["task"]
    },
    "handler": code_executor_handler,
}
