"""Action handler functions extracted from StructuredChatAgent.

Each handler corresponds to a specific action kind (tool, plan, task,
context_request, system, unknown) and operates on an ``agent`` instance
passed as the first argument.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import inspect
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from app.services.llm.structured_response import LLMAction, LLMStructuredResponse
from app.services.plans.acceptance_criteria import derive_expected_deliverables
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_session import PlanSession
from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import (
    append_action_log_entry,
    record_decomposition_job,
    update_decomposition_job_status,
)
from app.services.plans.decomposition_jobs import (
    get_current_job,
    log_job_event,
    plan_decomposition_jobs,
    reset_current_job,
    set_current_job,
    start_decomposition_job_thread,
    start_phagescope_track_job_thread,
)
from app.services.plans.plan_decomposer import DecompositionResult, PlanDecomposer
from app.services.plans.plan_executor import (
    PlanExecutor,
    PlanExecutorLLMService,
    ExecutionConfig,
)
from app.services.plans.plan_generation import (
    create_plan_and_generate,
    ensure_plan_generation_ready,
)
from app.services.plans.artifact_preflight import ArtifactPreflightService
from app.services.plans.plan_optimizer import (
    auto_optimize_plan,
    capture_plan_optimization_outcome,
    resolve_plan_review_result,
)
from app.services.plans.task_verification import TaskVerificationService
from app.config import get_graph_rag_settings, get_search_settings
from app.config.tool_policy import get_tool_policy, is_tool_allowed
from app.config.decomposer_config import get_decomposer_settings
from app.config.executor_config import get_executor_settings
from app.services.foundation.settings import get_settings
from app.services.deliverables import (
    format_deliverable_submit_summary,
    get_deliverable_publisher,
)
from app.services.upload_storage import delete_session_storage
from app.services.tool_output_storage import store_tool_output
from tool_box import execute_tool

from .guardrails import explicit_manuscript_request, local_manuscript_assembly_request
from .models import AgentStep, AgentResult
from .action_phagescope import (
    _build_phagescope_research_seed_tasks,
    _looks_like_phagescope_research_paper_goal,
    maybe_synthesize_phagescope_saveall_analysis,
)
from .action_runtime_context import (
    _LOCAL_SUBJECT_TOOLS,
    _MUTATING_FILE_OPERATIONS,
    _RUNTIME_CONTEXT_KEYS,
    _collect_produced_artifacts,
    _current_user_turn,
    _extract_subject_from_tool_call,
    _infer_subject_action_class,
    _infer_subject_kind,
    _normalize_local_tool_params,
    _persist_runtime_context,
    _update_runtime_context_from_tool,
)
from .action_tool_params import (
    _BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY,
    _READ_ONLY_FILE_OPERATIONS,
    _SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY,
    _clean_existing_path_param,
    _coerce_inline_number,
    _extract_scientific_figure_inline_rows,
    _normalize_bio_tools_params,
    _normalize_code_executor_params,
    _normalize_deliverable_submit_params,
    _normalize_document_reader_params,
    _normalize_file_operations_params,
    _normalize_generate_experiment_card_params,
    _normalize_graph_rag_params,
    _normalize_lightrag_query_params,
    _normalize_literature_pipeline_params,
    _normalize_manuscript_writer_params,
    _normalize_paper_replication_params,
    _normalize_phagescope_params,
    _normalize_phagescope_research_params,
    _normalize_result_interpreter_params,
    _normalize_review_pack_writer_params,
    _normalize_scientific_figure_generator_params,
    _normalize_sequence_fetch_params,
    _normalize_url_fetch_params,
    _normalize_vision_reader_params,
    _normalize_web_search_params,
    _parse_json_list_param,
    _scientific_figure_label_key,
)
from .action_task_ops import handle_task_action
from .action_plan_ops import (
    _RERUN_TASK_EXECUTION_JOB_KEY,
    _artifact_preflight_failure_step,
    _build_plan_generation_session_context,
    _ensure_rerun_task_execution_job,
    _execute_rerun_task_with_job,
    _finalize_rerun_task_execution,
    _maybe_ensure_plan_generation_ready_for_agent,
    _prepare_rerun_task_execution,
    _should_run_artifact_preflight,
    handle_plan_action,
    handle_task_action_async,
)
from .artifact_gallery import (
    extract_artifact_gallery_from_result,
    update_recent_image_artifacts,
)
from .request_routing import get_all_tools
from .session_helpers import (
    _lookup_phagescope_task_memory,
    _normalize_search_provider,
    _resolve_phagescope_taskid_alias,
    _record_phagescope_task_memory,
    _set_session_plan_id,
    _update_session_metadata,
)
from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    normalize_tool_path,
    subject_identity_matches,
)
from .terminal_mutation_verify import (
    prepare_local_mutation_terminal_write,
    verify_local_mutation_terminal_write,
)
from .code_executor_helpers import extract_task_artifact_paths
from .tool_results import (
    sanitize_tool_result,
    summarize_tool_result,
    truncate_large_fields,
    drop_callables,
    normalize_dependencies,
    append_recent_tool_result,
)
from .background import (
    _BACKGROUND_TOOL_NAMES,
    _BACKGROUND_PLAN_OPS,
    _PHAGESCOPE_SYNC_ACTIONS,
)

logger = logging.getLogger(__name__)

_EXPLICIT_PLAN_TASK_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:task\s*)?(?P<num>\d{1,3})\s*[:.)-]\s*(?P<body>\S.*)$",
    re.IGNORECASE,
)

_EXPLICIT_PLAN_TASK_BLOCK_RE = re.compile(
    r"(?:^|\n|\s)(?:[-*]\s*)?task\s*(?P<num>\d{1,3})\s*[:.)-]\s*(?P<body>.*?)(?=(?:\s+(?:[-*]\s*)?task\s*\d{1,3}\s*[:.)-])|$)",
    re.IGNORECASE | re.DOTALL,
)


def _coerce_plan_description(description: Any, goal: Any) -> Optional[str]:
    if isinstance(description, str) and description.strip():
        return description.strip()
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    return None


def _extract_explicit_plan_tasks_from_goal(goal: Any) -> List[Dict[str, Any]]:
    if not isinstance(goal, str) or not goal.strip():
        return []

    extracted: List[Tuple[int, str]] = []
    for match in _EXPLICIT_PLAN_TASK_BLOCK_RE.finditer(goal):
        try:
            number = int(match.group("num"))
        except (TypeError, ValueError):
            continue
        body = re.sub(r"\s+", " ", str(match.group("body") or "")).strip()
        if body:
            extracted.append((number, body))

    if len(extracted) < 2:
        extracted = []
        for line in goal.splitlines():
            match = _EXPLICIT_PLAN_TASK_RE.match(line)
            if not match:
                continue
            try:
                number = int(match.group("num"))
            except (TypeError, ValueError):
                continue
            body = str(match.group("body") or "").strip()
            if body:
                extracted.append((number, body))

    if len(extracted) < 2:
        return []

    extracted.sort(key=lambda item: item[0])
    tasks: List[Dict[str, Any]] = []
    previous_name: Optional[str] = None
    for number, body in extracted:
        name_body = re.split(r"[.;]", body, maxsplit=1)[0].strip() or body
        name = f"Task {number}: {name_body}"
        if len(name) > 96:
            name = name[:93].rstrip() + "..."
        task: Dict[str, Any] = {
            "name": name,
            "instruction": body,
            "metadata": {
                "task_type": "composite",
                "source": "explicit_create_plan_goal",
                "explicit_task_number": number,
            },
            "dependencies": [previous_name] if previous_name else [],
        }
        tasks.append(task)
        previous_name = name
    return tasks


_PHAGESCOPE_RESEARCH_ACTIONS = {"audit", "research_plan", "prepare_metadata_table"}
_artifact_preflight_service = ArtifactPreflightService()

# ---------------------------------------------------------------------------
# Aliases matching the names used in the original StructuredChatAgent code
# ---------------------------------------------------------------------------
_sanitize_tool_result_fn = sanitize_tool_result
_summarize_tool_result_fn = summarize_tool_result
_drop_callables_fn = drop_callables
_normalize_dependencies_fn = normalize_dependencies
_append_recent_tool_result_fn = append_recent_tool_result
_task_verifier = TaskVerificationService()


def _append_unique_text(target: List[str], seen: set[str], value: Any, *, limit: int = 20) -> None:
    text = str(value or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    target.append(text)
    if len(target) > limit:
        del target[limit:]


def _resolve_bound_task_tree_and_node(agent: Any) -> Tuple[Optional[PlanTree], Optional[PlanNode]]:
    current_task_id = (getattr(agent, "extra_context", {}) or {}).get("current_task_id")
    plan_id = getattr(getattr(agent, "plan_session", None), "plan_id", None)
    if current_task_id is None or plan_id is None:
        return None, None

    try:
        task_id = int(current_task_id)
    except (TypeError, ValueError):
        return None, None

    tree = getattr(agent, "plan_tree", None)
    if tree is None or not getattr(tree, "has_node", lambda *_: False)(task_id):
        try:
            tree = agent.plan_session.repo.get_plan_tree(plan_id)
        except Exception:
            return None, None

    if tree is None or not getattr(tree, "has_node", lambda *_: False)(task_id):
        return None, None

    try:
        return tree, tree.get_node(task_id)
    except Exception:
        return None, None


def _align_manuscript_writer_params_with_bound_task(agent: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    tree, node = _resolve_bound_task_tree_and_node(agent)
    if node is None:
        return params

    aligned = dict(params)
    metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
    paper_meta = node.paper_metadata() if hasattr(node, "paper_metadata") else None
    acceptance = metadata.get("acceptance_criteria") if isinstance(metadata, dict) else None
    acceptance_category = (
        str(acceptance.get("category") or "").strip().lower()
        if isinstance(acceptance, dict)
        else ""
    )
    paper_mode = bool(
        metadata.get("paper_mode")
        or getattr(paper_meta, "paper_section", None)
        or getattr(paper_meta, "paper_role", None)
        or getattr(paper_meta, "paper_context_paths", None)
        or acceptance_category == "paper"
    )
    if not paper_mode:
        return aligned

    context_paths = aligned.get("context_paths") or []
    if isinstance(context_paths, str):
        context_paths = [context_paths]
    if not isinstance(context_paths, list):
        context_paths = []

    merged_context_paths: List[str] = []
    seen_paths: set[str] = set()
    for item in context_paths:
        _append_unique_text(merged_context_paths, seen_paths, item)

    raw_paper_context_paths = (
        list(getattr(paper_meta, "paper_context_paths", []) or [])
        if paper_meta is not None
        else metadata.get("paper_context_paths")
    )
    if isinstance(raw_paper_context_paths, list):
        for item in raw_paper_context_paths:
            _append_unique_text(merged_context_paths, seen_paths, item)

    for dep_id in (getattr(node, "dependencies", []) or [])[:6]:
        try:
            dep_id_int = int(dep_id)
        except (TypeError, ValueError):
            continue
        if tree is None or not tree.has_node(dep_id_int):
            continue
        dep_node = tree.get_node(dep_id_int)
        for artifact_path in extract_task_artifact_paths(dep_node):
            _append_unique_text(merged_context_paths, seen_paths, artifact_path)

    if merged_context_paths:
        aligned["context_paths"] = merged_context_paths

    expected_outputs = [
        str(item).replace("\\", "/").lstrip("./")
        for item in derive_expected_deliverables(
            metadata.get("acceptance_criteria") if isinstance(metadata, dict) else None,
            include_globs=False,
            relative_only=True,
        )
        if str(item).strip() and Path(str(item).strip()).suffix
    ]
    if len(expected_outputs) == 1:
        canonical_output = expected_outputs[0]
        if not canonical_output.startswith("manuscript/"):
            canonical_output = f"manuscript/{canonical_output}"
        aligned["output_path"] = canonical_output

    paper_section = str(
        getattr(paper_meta, "paper_section", None) or metadata.get("paper_section") or ""
    ).strip().lower()
    if paper_section and not aligned.get("sections"):
        aligned["sections"] = [paper_section]

    task_text = str(aligned.get("task") or "").strip()
    if not task_text:
        fallback_task = (
            str(getattr(node, "instruction", "") or "").strip()
            or str(getattr(node, "name", "") or "").strip()
        )
        if fallback_task:
            aligned["task"] = fallback_task
            task_text = fallback_task
    grounding_requirements: List[str] = []
    if paper_section:
        grounding_requirements.append(f"Focus only on the {paper_section} section.")
    if paper_mode:
        grounding_requirements.append(
            "Use exact method names, pipeline labels, and file-grounded facts from the provided evidence. "
            "Do not substitute alternative algorithms or generic defaults."
        )
    if merged_context_paths:
        evidence_names = [
            Path(str(item)).name
            for item in merged_context_paths
            if Path(str(item)).name
        ][:6]
        if evidence_names:
            grounding_requirements.append(
                "Ground the draft in these evidence files when relevant: "
                + ", ".join(evidence_names)
                + "."
            )
    if len(expected_outputs) == 1 and aligned.get("output_path"):
        grounding_requirements.append(
            f"Write the final deliverable to exactly: {aligned['output_path']}."
        )
    if task_text and grounding_requirements:
        aligned["task"] = task_text + "\n\nBound task requirements:\n- " + "\n- ".join(
            grounding_requirements
        )

    return aligned


def _capability_guard_failure(
    agent: Any,
    action: LLMAction,
    *,
    tool_name: str,
    params: Dict[str, Any],
    message: str,
    error_code: str,
) -> AgentStep:
    sanitized = {
        "success": False,
        "tool": tool_name,
        "error": message,
        "error_code": error_code,
    }
    try:
        _update_runtime_context_from_tool(
            agent,
            tool_name=tool_name,
            params=params,
            sanitized=sanitized,
            summary=message,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to update runtime context for blocked tool: %s", exc)
    return AgentStep(
        action=action,
        success=False,
        message=message,
        details={"error": error_code, "tool": tool_name, "result": sanitized},
    )


def _normalize_scientific_figure_raw_result(
    raw_result: Any,
    *,
    publish: bool,
) -> Any:
    """Return the canonical figure-generator payload for chat handling.

    The normal tool-box path returns the handler payload directly, while some
    executor/test paths wrap it as ``{"success": ..., "result": <handler>}``.
    Chat post-processing needs the handler payload at the top level so the
    deliverable publisher can see ``deliverable_submit`` and the sanitizer can
    expose ``figure_png``/QA paths consistently.
    """
    if not isinstance(raw_result, dict):
        return raw_result

    payload: Dict[str, Any] = raw_result
    inner = raw_result.get("result")
    if isinstance(inner, dict) and (
        str(inner.get("tool") or "").strip() == "scientific_figure_generator"
        or "deliverable_submit" in inner
        or isinstance(inner.get("result"), dict)
    ):
        payload = dict(inner)
        for key in (
            "success",
            "summary",
            "deliverables",
            "storage",
            "artifact_gallery",
            "deliverable_error",
        ):
            if key in raw_result and key not in payload:
                payload[key] = raw_result[key]
    else:
        payload = dict(raw_result)

    payload.setdefault("tool", "scientific_figure_generator")
    figure_result_keys = (
        "title",
        "output_dir",
        "figure_png",
        "figure_pdf",
        "legend_md",
        "provenance_tsv",
        "qa_json",
        "qa_passed",
        "panels",
        "generated_files",
    )
    flat_result = {key: payload[key] for key in figure_result_keys if key in payload}
    result_block = payload.get("result")
    if flat_result:
        if isinstance(result_block, dict):
            merged_result = dict(flat_result)
            merged_result.update(result_block)
            payload["result"] = merged_result
            result_block = merged_result
        else:
            payload["result"] = dict(flat_result)
            result_block = payload["result"]

    if isinstance(result_block, dict) and "deliverable_submit" not in payload:
        artifact_specs = [
            ("figure_png", "image_tabular", "Final scientific composite figure PNG"),
            ("figure_pdf", "image_tabular", "Final scientific composite figure PDF"),
            ("legend_md", "docs", "Figure legend"),
            ("provenance_tsv", "image_tabular", "Figure provenance table"),
            ("qa_json", "image_tabular", "Figure QA report"),
        ]
        artifacts: List[Dict[str, str]] = []
        for key, module, reason in artifact_specs:
            path_value = result_block.get(key)
            if isinstance(path_value, str) and path_value.strip():
                artifacts.append(
                    {
                        "path": path_value.strip(),
                        "module": module,
                        "reason": reason,
                    }
                )
        if artifacts:
            payload["deliverable_submit"] = {
                "publish": bool(publish),
                "artifacts": artifacts,
            }

    return payload


def _build_chat_tool_context(agent: Any, tool_name: str) -> Optional[Any]:
    session_id = getattr(agent, "session_id", None)
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir
        from tool_box.context import ToolContext
    except Exception:
        return None

    plan_id: Optional[int] = None
    raw_plan_id = getattr(getattr(agent, "plan_session", None), "plan_id", None)
    if raw_plan_id is not None:
        try:
            plan_id = int(raw_plan_id)
        except (TypeError, ValueError):
            plan_id = None

    task_id: Optional[int] = None
    raw_task_id = (getattr(agent, "extra_context", {}) or {}).get("current_task_id")
    if raw_task_id is not None:
        try:
            task_id = int(raw_task_id)
        except (TypeError, ValueError):
            task_id = None

    task_name: Optional[str] = None
    if task_id is not None and getattr(getattr(agent, "plan_session", None), "repo", None) is not None and plan_id is not None:
        try:
            tree = agent.plan_session.repo.get_plan_tree(plan_id)
            if tree.has_node(task_id):
                task_name = tree.get_node(task_id).display_name()
        except Exception:
            task_name = None

    work_dir = get_runtime_session_dir(session_id, create=True) / "raw_files" / "chat_tools" / tool_name
    owner_id = (getattr(agent, "extra_context", {}) or {}).get("owner_id")
    return ToolContext(
        session_id=session_id.strip(),
        plan_id=plan_id,
        task_id=task_id,
        task_name=task_name,
        job_id=get_current_job(),
        owner_id=str(owner_id).strip() if owner_id is not None and str(owner_id).strip() else None,
        work_dir=str(work_dir),
    )


def _enforce_capability_guard(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Optional[AgentStep]:
    # All tools are always available — the LLM decides which to use.
    # The tool-name allowlist check is kept for defense-in-depth against
    # unregistered or unknown tool names.
    allowed_tools = set(get_all_tools())
    if tool_name not in allowed_tools:
        return _capability_guard_failure(
            agent,
            action,
            tool_name=tool_name,
            params=params,
            message=f"Tool '{tool_name}' is not a registered tool.",
            error_code="tool_not_available",
        )

    # Mutation awareness: log file-mutating operations for observability.
    # The old intent-type-based blocking was removed in Phase 1 (LLM-first),
    # but we keep audit logging so anomalous mutation patterns can be detected.
    if tool_name == "file_operations":
        operation = str(params.get("operation") or "").strip().lower()
        if operation in _MUTATING_FILE_OPERATIONS:
            logger.info(
                "[MUTATION_AUDIT] file_operations.%s path=%s intent=%s",
                operation,
                params.get("path", "<unknown>"),
                agent.extra_context.get("intent_type", "unknown"),
            )

    return None


# ---------------------------------------------------------------------------
# handle_tool_action
# ---------------------------------------------------------------------------

async def handle_tool_action(agent: Any, action: LLMAction) -> AgentStep:
    tool_name = (action.name or "").strip()
    if not tool_name:
        return AgentStep(
            action=action,
            success=False,
            message="Tool action is missing a name.",
            details={"error": "missing_tool_name"},
        )
    params = dict(action.parameters or {})
    action_value = str(params.get("action") or "").strip().lower()
    if tool_name == "phagescope" and action_value in _PHAGESCOPE_RESEARCH_ACTIONS:
        logger.info(
            "[CHAT][TOOL_ALIAS] routing phagescope.%s to phagescope_research",
            action_value,
        )
        tool_name = "phagescope_research"
        try:
            action.name = tool_name
        except Exception:
            pass

    policy = get_tool_policy()
    if not is_tool_allowed(tool_name, policy):
        return AgentStep(
            action=action,
            success=False,
            message=f"Tool '{tool_name}' is not allowed by policy.",
            details={"error": "tool_not_allowed", "tool": tool_name},
        )

    params = _normalize_local_tool_params(agent, tool_name, params)
    original_task: Optional[str] = None

    # 🔄 If LLM specified target_task_id, prioritize it for task-status updates.
    target_task_id = params.pop("target_task_id", None)
    if target_task_id is not None:
        try:
            agent.extra_context["current_task_id"] = int(target_task_id)
        except (TypeError, ValueError):
            pass

    capability_block = _enforce_capability_guard(agent, action, tool_name, params)
    if capability_block is not None:
        return capability_block

    if tool_name == "web_search":
        normalized = _normalize_web_search_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "file_operations":
        normalized = _normalize_file_operations_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "lightrag_query":
        normalized = _normalize_lightrag_query_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "graph_rag":
        normalized = _normalize_graph_rag_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "literature_pipeline":
        normalized = _normalize_literature_pipeline_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "review_pack_writer":
        normalized = _normalize_review_pack_writer_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "sequence_fetch":
        normalized = _normalize_sequence_fetch_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "url_fetch":
        normalized = _normalize_url_fetch_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "code_executor":
        normalized = await _normalize_code_executor_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params, original_task = normalized

    elif tool_name == "document_reader":
        normalized = _normalize_document_reader_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "vision_reader":
        normalized = _normalize_vision_reader_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "paper_replication":
        normalized = _normalize_paper_replication_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "generate_experiment_card":
        normalized = _normalize_generate_experiment_card_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "bio_tools":
        normalized = _normalize_bio_tools_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "phagescope":
        normalized = _normalize_phagescope_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "phagescope_research":
        normalized = _normalize_phagescope_research_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "manuscript_writer":
        normalized = _normalize_manuscript_writer_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "terminal_session":
        operation_value = params.get("operation", "")
        if not isinstance(operation_value, str) or not operation_value.strip():
            return AgentStep(
                action=action,
                success=False,
                message="terminal_session requires a non-empty `operation` string.",
                details={"error": "missing_operation", "tool": tool_name},
            )
        clean_params: Dict[str, Any] = {"operation": operation_value.strip().lower()}
        for key in ("terminal_id", "data", "encoding", "mode", "approval_id"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                clean_params[key] = value if key == "data" else value.strip()
        for int_key in ("cols", "rows", "limit"):
            if int_key in params and params.get(int_key) is not None:
                try:
                    clean_params[int_key] = int(params[int_key])
                except (TypeError, ValueError):
                    pass
        session_id_value = params.get("session_id")
        if isinstance(session_id_value, str) and session_id_value.strip():
            clean_params["session_id"] = session_id_value.strip()
        elif isinstance(agent.session_id, str) and agent.session_id.strip():
            clean_params["session_id"] = agent.session_id.strip()

        # Auto-ensure: if write needs terminal_id but none provided, call ensure first
        op = clean_params["operation"]
        needs_tid = op in ("write", "resize")
        has_tid = bool(clean_params.get("terminal_id"))
        if needs_tid and not has_tid:
            ensure_sid = clean_params.get("session_id") or (
                agent.session_id if isinstance(agent.session_id, str) and agent.session_id.strip() else None
            )
            if not ensure_sid:
                return AgentStep(
                    action=action,
                    success=False,
                    message="terminal_session write requires a session_id to auto-create a terminal.",
                    details={"error": "missing_session_id", "tool": tool_name},
                )
            try:
                ensure_result = await execute_tool(
                    "terminal_session", operation="ensure", session_id=ensure_sid
                )
                if isinstance(ensure_result, dict) and ensure_result.get("terminal_id"):
                    clean_params["terminal_id"] = ensure_result["terminal_id"]
            except Exception:
                pass  # let downstream report the missing terminal_id error

        params = clean_params

    elif tool_name == "result_interpreter":
        normalized = _normalize_result_interpreter_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "scientific_figure_generator":
        normalized = _normalize_scientific_figure_generator_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    elif tool_name == "deliverable_submit":
        normalized = _normalize_deliverable_submit_params(agent, action, tool_name, params)
        if isinstance(normalized, AgentStep):
            return normalized
        params = normalized

    else:
        return AgentStep(
            action=action,
            success=False,
            message=f"Tool {tool_name} is not supported yet.",
            details={"error": "unsupported_tool", "tool": tool_name},
        )

    pre_terminal_mutation_snapshot: Optional[Dict[str, Any]] = None
    mutation_marker_id: Optional[int] = None
    mutation_original_command: Optional[str] = None

    try:
        # PhageScope: provide elegant progress during wait/poll (job_update -> stats.tool_progress)
        if tool_name == "phagescope":
            action_value = str(params.get("action") or "").strip().lower()
            wait_value = params.get("wait") is True
            taskid_value = params.get("taskid")
            if wait_value and action_value in {"result", "quality"} and taskid_value:
                import time as _time
                import json as _json

                def _extract_task_status(detail_result: Any) -> str:
                    if not isinstance(detail_result, dict):
                        return "unknown"
                    payload = detail_result.get("data")
                    if isinstance(payload, dict):
                        results = payload.get("results")
                        if isinstance(results, dict):
                            for k in ("status", "task_status", "state", "taskstatus"):
                                v = results.get(k)
                                if isinstance(v, str) and v.strip():
                                    return v.strip()
                    return "unknown"

                def _extract_task_detail_dict(detail_result: Any) -> Optional[Dict[str, Any]]:
                    if not isinstance(detail_result, dict):
                        return None
                    payload = detail_result.get("data")
                    if not isinstance(payload, dict):
                        return None
                    # phagescope_handler attaches parsed_task_detail when possible
                    parsed = payload.get("parsed_task_detail")
                    if isinstance(parsed, dict):
                        return parsed
                    # sometimes nested under results.task_detail
                    results = payload.get("results")
                    if isinstance(results, dict):
                        td = results.get("task_detail")
                        if isinstance(td, dict):
                            return td
                        if isinstance(td, str) and td.strip():
                            try:
                                parsed_td = _json.loads(td)
                                if isinstance(parsed_td, dict):
                                    return parsed_td
                            except Exception:
                                return None
                    return None

                def _module_status_upper(value: Any) -> Optional[str]:
                    if not isinstance(value, str):
                        return None
                    v = value.strip()
                    return v.upper() if v else None

                poll_timeout = float(params.get("poll_timeout") or 120.0)
                poll_interval = float(params.get("poll_interval") or 2.0)
                start = _time.monotonic()

                # Avoid the tool's internal polling; we do it here so we can stream progress
                attempt_params = dict(params)
                attempt_params["wait"] = False

                raw_result = None
                last_status = "queued"
                while True:
                    elapsed = _time.monotonic() - start
                    denom = poll_timeout if poll_timeout > 0 else 1.0
                    time_percent = int(max(0.0, min(1.0, elapsed / denom)) * 100)

                    # best-effort task status
                    modules_payload: Optional[List[Dict[str, Any]]] = None
                    counts_payload: Optional[Dict[str, int]] = None
                    try:
                        detail = await execute_tool(
                            "phagescope",
                            action="task_detail",
                            taskid=str(taskid_value),
                            base_url=params.get("base_url"),
                            token=params.get("token"),
                            timeout=min(float(params.get("timeout") or 60.0), 40.0),
                        )
                        last_status = _extract_task_status(detail)
                        task_detail = _extract_task_detail_dict(detail)
                        if isinstance(task_detail, dict):
                            queue = task_detail.get("task_que")
                            if isinstance(queue, list) and queue:
                                modules: List[Dict[str, Any]] = []
                                done = 0
                                total = 0
                                for item in queue:
                                    if not isinstance(item, dict):
                                        continue
                                    name = item.get("module")
                                    if not isinstance(name, str) or not name.strip():
                                        continue
                                    status_raw = (
                                        item.get("module_satus")
                                        or item.get("module_status")
                                        or item.get("status")
                                    )
                                    status_upper = _module_status_upper(status_raw) or "UNKNOWN"
                                    is_done: Optional[bool] = None
                                    if status_upper in {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}:
                                        is_done = True
                                    elif status_upper in {"FAILED", "ERROR"}:
                                        is_done = False
                                    modules.append(
                                        {
                                            "name": name.strip(),
                                            "status": str(status_raw) if status_raw is not None else status_upper,
                                            "done": is_done,
                                        }
                                    )
                                    total += 1
                                    if is_done is True:
                                        done += 1
                                if total > 0:
                                    modules_payload = modules
                                    counts_payload = {"done": done, "total": total}
                    except Exception:
                        # keep last_status
                        pass

                    # Prefer module-based percent when available; otherwise fallback to time-based percent.
                    percent = time_percent
                    if counts_payload and counts_payload.get("total"):
                        percent = int(round((counts_payload["done"] / max(1, counts_payload["total"])) * 100))
                        percent = max(0, min(100, percent))

                    plan_decomposition_jobs.update_stats_from_context(
                        {
                            "tool_progress": {
                                "tool": "phagescope",
                                "taskid": str(taskid_value),
                                "percent": percent,
                                "status": last_status,
                                "phase": "poll",
                                **({"modules": modules_payload} if modules_payload is not None else {}),
                                **({"counts": counts_payload} if counts_payload is not None else {}),
                            }
                        }
                    )

                    # try fetch result
                    raw_result = await execute_tool(tool_name, **attempt_params)
                    if isinstance(raw_result, dict) and raw_result.get("success") is True:
                        plan_decomposition_jobs.update_stats_from_context(
                            {
                                "tool_progress": {
                                    "tool": "phagescope",
                                    "taskid": str(taskid_value),
                                    "percent": 100,
                                    "status": last_status or "Success",
                                    "phase": "done",
                                }
                            }
                        )
                        break

                    upper = str(last_status or "").strip().upper()
                    if upper in {"FAILED", "ERROR"}:
                        break
                    if elapsed >= poll_timeout:
                        raw_result = {
                            "success": False,
                            "status_code": 408,
                            "action": action_value,
                            "taskid": str(taskid_value),
                            "error": f"Result not ready within {poll_timeout:.0f}s. Retry later with taskid={taskid_value}.",
                            "polling": {
                                "waited": True,
                                "poll_timeout": poll_timeout,
                                "poll_interval": poll_interval,
                            },
                        }
                        break
                    await asyncio.sleep(max(0.2, poll_interval))
            else:
                (
                    params,
                    pre_terminal_mutation_snapshot,
                    mutation_marker_id,
                    mutation_original_command,
                ) = await prepare_local_mutation_terminal_write(agent, tool_name, params)
                raw_result = await execute_tool(tool_name, **params)
        else:
            (
                params,
                pre_terminal_mutation_snapshot,
                mutation_marker_id,
                mutation_original_command,
            ) = await prepare_local_mutation_terminal_write(agent, tool_name, params)
            raw_result = await execute_tool(tool_name, **params)
            if tool_name == "scientific_figure_generator":
                raw_result = _normalize_scientific_figure_raw_result(
                    raw_result,
                    publish=bool(params.get("publish", True)),
                )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Tool %s execution failed for session %s: %s",
            tool_name,
            agent.session_id,
            exc,
        )
        return AgentStep(
            action=action,
            success=False,
            message=f"{tool_name} failed: {exc}",
            details={"error": str(exc), "tool": tool_name},
        )

    sanitized = _sanitize_tool_result_fn(tool_name, raw_result)
    # For optional local file reads, keep failure semantics but annotate the
    # result so callers can decide whether to continue.
    try:
        is_optional = (
            isinstance(action.metadata, dict) and bool(action.metadata.get("optional"))
        )
        if (
            tool_name == "file_operations"
            and is_optional
            and isinstance(params, dict)
            and params.get("operation") == "read"
            and isinstance(sanitized, dict)
            and sanitized.get("success") is False
        ):
            patched = dict(sanitized)
            patched["optional"] = True
            patched["optional_error"] = patched.get("error") or "read_failed"
            sanitized = patched
    except Exception:
        pass

    if (
        tool_name == "terminal_session"
        and str(params.get("operation") or "").strip().lower() == "write"
        and mutation_marker_id is not None
        and mutation_original_command is not None
        and isinstance(sanitized, dict)
        and sanitized.get("success") is not False
    ):
        try:
            sanitized = await verify_local_mutation_terminal_write(
                agent,
                sanitized=sanitized,
                params=params,
                pre_snapshot=pre_terminal_mutation_snapshot,
                marker_id=mutation_marker_id,
                original_command=mutation_original_command,
            )
        except Exception as exc:
            logger.debug("local_mutation terminal verification failed: %s", exc)
        if isinstance(sanitized, dict):
            sanitized.setdefault("operation", str(params.get("operation") or "write"))

    if tool_name == "sequence_fetch":
        if (
            isinstance(raw_result, dict)
            and raw_result.get("success") is False
            and raw_result.get("no_claude_fallback") is True
        ):
            blocked_summary = str(
                raw_result.get("error")
                or "sequence_fetch failed and code_executor fallback is blocked."
            ).strip()
            agent.extra_context[_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY] = {
                "summary": blocked_summary,
                "blocked_reason": "sequence_fetch_failed_no_fallback",
                "error_code": raw_result.get("error_code"),
                "error_stage": raw_result.get("error_stage"),
                "accessions": raw_result.get("accessions"),
                "provider": raw_result.get("provider"),
            }
        elif sanitized.get("success") is not False:
            agent.extra_context.pop(_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY, None)

    if tool_name == "bio_tools":
        if (
            isinstance(raw_result, dict)
            and raw_result.get("success") is False
            and raw_result.get("no_claude_fallback") is True
        ):
            blocked_summary = str(
                raw_result.get("error")
                or "bio_tools input preparation failed; code_executor fallback is blocked."
            ).strip()
            agent.extra_context[_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY] = {
                "summary": blocked_summary,
                "blocked_reason": "bio_tools_input_preparation_failed",
                "error_code": raw_result.get("error_code"),
                "error_stage": raw_result.get("error_stage"),
                "tool_name": raw_result.get("tool"),
                "operation": raw_result.get("operation"),
            }
        elif sanitized.get("success") is not False:
            agent.extra_context.pop(_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY, None)

    base_summary = _summarize_tool_result_fn(tool_name, sanitized)
    summary = base_summary
    success = sanitized.get("success", True)
    deliverable_report = None
    deliverable_error = None
    if agent.session_id:
        publish_task_id: Optional[int] = None
        publish_task_name: Optional[str] = None
        publish_task_instruction: Optional[str] = None
        try:
            current_task_id = agent.extra_context.get("current_task_id")
            if current_task_id is not None:
                publish_task_id = int(current_task_id)
        except (TypeError, ValueError):
            publish_task_id = None

        if publish_task_id is not None and agent.plan_session.plan_id is not None:
            try:
                tree = agent.plan_session.repo.get_plan_tree(agent.plan_session.plan_id)
                if tree.has_node(publish_task_id):
                    task_node = tree.get_node(publish_task_id)
                    publish_task_name = task_node.display_name()
                    publish_task_instruction = task_node.instruction
            except Exception as exc:  # pragma: no cover - best-effort
                logger.debug(
                    "Unable to resolve task context for deliverable publish in session %s: %s",
                    agent.session_id,
                    exc,
                )

        try:
            publish_payload = _drop_callables_fn(raw_result)
            deliverable_report = get_deliverable_publisher().publish_from_tool_result(
                session_id=agent.session_id,
                tool_name=tool_name,
                raw_result=publish_payload,
                summary=base_summary,
                source={
                    "channel": "chat",
                    "action_kind": action.kind,
                    "action_name": action.name,
                    "step_order": action.order,
                },
                job_id=get_current_job(),
                plan_id=agent.plan_session.plan_id,
                task_id=publish_task_id,
                task_name=publish_task_name,
                task_instruction=publish_task_instruction,
                publish_status="final" if success is not False else "draft",
            )
        except Exception as exc:  # pragma: no cover - defensive
            deliverable_error = str(exc)
            logger.warning(
                "Failed to publish deliverables for session %s tool %s: %s",
                agent.session_id,
                tool_name,
                exc,
            )

        if deliverable_report is not None:
            try:
                from app.services.artifacts.projector import get_registry_projector

                get_registry_projector().record_chat_publish(
                    session_id=agent.session_id,
                    tool_name=tool_name,
                    report=deliverable_report,
                    job_id=get_current_job(),
                    plan_id=agent.plan_session.plan_id,
                    task_id=publish_task_id,
                    task_name=publish_task_name,
                )
            except Exception:
                logger.debug(
                    "artifact event stream record failed for tool %s",
                    tool_name,
                    exc_info=True,
                )

    if tool_name == "deliverable_submit":
        submit_summary = format_deliverable_submit_summary(deliverable_report)
        if submit_summary:
            summary = submit_summary

    if deliverable_error:
        sanitized["deliverable_error"] = deliverable_error
        if isinstance(raw_result, dict):
            raw_result.setdefault("deliverable_error", deliverable_error)

    storage_info = None
    if agent.session_id:
        action_payload = {
            "kind": action.kind,
            "name": action.name,
            "order": action.order,
            "blocking": action.blocking,
            "parameters": _drop_callables_fn(params),
        }
        try:
            storage_info = store_tool_output(
                session_id=agent.session_id,
                job_id=get_current_job(),
                action=action_payload,
                tool_name=tool_name,
                raw_result=raw_result,
                summary=summary,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to store tool output for %s in session %s: %s",
                tool_name,
                agent.session_id,
                exc,
            )

    # Attach stored output paths back to agent/tool result (no manual copy needed)
    if storage_info is not None and agent.session_id:
        try:
            from app.services.upload_storage import ensure_session_dir
            from pathlib import Path

            session_root = ensure_session_dir(agent.session_id)

            def _abs(rel: Optional[str]) -> Optional[str]:
                if not rel:
                    return None
                try:
                    return str((session_root / Path(rel)).resolve())
                except Exception:
                    return str(session_root / Path(rel))

            storage_payload: Dict[str, Any] = {
                "session_id": agent.session_id,
                "job_id": get_current_job(),
                "tool": tool_name,
                "step_order": action.order,
                "output_dir": _abs(getattr(storage_info, "output_dir", None)),
                "result_path": _abs(getattr(storage_info, "result_path", None)),
                "manifest_path": _abs(getattr(storage_info, "manifest_path", None)),
                "preview_path": _abs(getattr(storage_info, "preview_path", None)),
            }
            # Also keep relative paths for portability (optional)
            storage_payload_rel: Dict[str, Any] = {
                "output_dir": getattr(storage_info, "output_dir", None),
                "result_path": getattr(storage_info, "result_path", None),
                "manifest_path": getattr(storage_info, "manifest_path", None),
                "preview_path": getattr(storage_info, "preview_path", None),
            }
            storage_payload["relative"] = storage_payload_rel

            if isinstance(raw_result, dict):
                raw_result.setdefault("storage", storage_payload)
            if isinstance(sanitized, dict):
                sanitized.setdefault("storage", storage_payload)

            # Persist latest output location for later retrieval
            def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
                metadata["phagescope_last_output"] = storage_payload
                items = metadata.get("phagescope_recent_outputs")
                if not isinstance(items, list):
                    items = []
                # de-dup by result_path
                rp = storage_payload.get("result_path")
                items = [it for it in items if not (isinstance(it, dict) and it.get("result_path") == rp)]
                items.insert(0, storage_payload)
                metadata["phagescope_recent_outputs"] = items[:10]
                return metadata

            if tool_name == "phagescope":
                _update_session_metadata(agent.session_id, _updater)
                # Make it available to the current agent loop immediately
                agent.extra_context["phagescope_last_output"] = storage_payload
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug("Failed to attach phagescope storage paths: %s", exc)

    artifact_gallery = extract_artifact_gallery_from_result(
        sanitized,
        session_id=agent.session_id,
        source_tool=tool_name,
        tracking_id=get_current_job(),
        created_at=datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat(),
    )
    if artifact_gallery:
        sanitized["artifact_gallery"] = artifact_gallery
        if isinstance(raw_result, dict):
            raw_result.setdefault("artifact_gallery", artifact_gallery)

    _append_recent_tool_result_fn(agent.extra_context, tool_name, summary, sanitized)

    try:
        _update_runtime_context_from_tool(
            agent,
            tool_name=tool_name,
            params=params,
            sanitized=sanitized,
            summary=summary,
            storage_info=storage_info,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to update runtime evidence for tool %s: %s", tool_name, exc)

    if tool_name == "phagescope" and agent.session_id:
        action_value = params.get("action")
        if action_value == "submit" and sanitized.get("success"):
            try:
                _record_phagescope_task_memory(agent.session_id, params, sanitized)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Failed to record phagescope task memory for %s: %s",
                    agent.session_id,
                    exc,
                )

    if success is False:
        message = summary or f"{tool_name} failed to execute."
    else:
        message = summary or f"{tool_name} finished execution."

    # 💾 A-mem integration: save execution result asynchronously without blocking.
    if tool_name == "code_executor":
        try:
            from app.services.amem_client import get_amem_client
            amem_client = get_amem_client()

            if amem_client.enabled:
                # Save to A-mem asynchronously.
                asyncio.create_task(
                    amem_client.save_execution(
                        task=original_task,  # Use original task description.
                        result=sanitized,
                        session_id=agent.session_id,
                        plan_id=agent.plan_session.plan_id,
                        key_findings=summary  # Use summary as key findings.
                    )
                )
                logger.info("[AMEM] Scheduled execution result save")
        except Exception as amem_err:
            logger.warning(f"[AMEM] Failed to schedule save: {amem_err}")
            # Do not affect main flow.

    agent._sync_task_status_after_tool_execution(
        tool_name=tool_name,
        success=success,
        summary=summary,
        message=message,
        params=params,
        result=sanitized,
        extra_metadata={
            "storage": storage_info.__dict__ if storage_info else None,
            "deliverables": deliverable_report.to_dict() if deliverable_report else None,
        },
    )

    return AgentStep(
        action=action,
        success=bool(success),
        message=message,
        details={
            "tool": tool_name,
            "parameters": _drop_callables_fn(params),
            "result": sanitized,
            "summary": summary,
            "storage": storage_info.__dict__ if storage_info else None,
            "deliverables": deliverable_report.to_dict() if deliverable_report else None,
        },
    )


# ---------------------------------------------------------------------------
# handle_task_action
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# handle_context_request
# ---------------------------------------------------------------------------

def handle_context_request(agent: Any, action: LLMAction) -> AgentStep:
    if action.name != "request_subgraph":
        return handle_unknown_action(agent, action)
    params = action.parameters or {}
    tree = agent._require_plan_bound()
    node_id_value = params.get("logical_id") or params.get("task_id")
    node_id = agent._coerce_int(node_id_value, "task_id")
    max_depth_raw = params.get("max_depth")
    max_depth = (
        agent._coerce_int(max_depth_raw, "max_depth")
        if max_depth_raw is not None
        else 2
    )
    agent._refresh_plan_tree(force_reload=False)
    graph_tree = agent.plan_tree or agent.plan_session.ensure()
    try:
        nodes = graph_tree.subgraph_nodes(node_id, max_depth=max_depth)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    outline = graph_tree.subgraph_outline(node_id, max_depth=max_depth)
    details = {
        "plan_id": tree.id,
        "root_node": node_id,
        "max_depth": max_depth,
        "outline": outline,
        "nodes": [node.model_dump() for node in nodes],
    }
    message = f"Returned a subgraph preview for node {node_id}."
    return AgentStep(action=action, success=True, message=message, details=details)


# ---------------------------------------------------------------------------
# handle_system_action
# ---------------------------------------------------------------------------

def handle_system_action(agent: Any, action: LLMAction) -> AgentStep:
    if action.name == "help":
        message = (
            "System help: you can create/list/delete plans or perform CRUD and restructuring actions on the current plan. "
            "For subgraph queries and similar operations, bind a plan first by calling create_plan or list_plans."
        )
        return AgentStep(action=action, success=True, message=message, details={})
    return handle_unknown_action(agent, action)


# ---------------------------------------------------------------------------
# handle_unknown_action
# ---------------------------------------------------------------------------

def handle_unknown_action(agent: Any, action: LLMAction) -> AgentStep:
    message = f"Unrecognized action kind or name: {action.kind}/{action.name}."
    return AgentStep(action=action, success=False, message=message, details={})
