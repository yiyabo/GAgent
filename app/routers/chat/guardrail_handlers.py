"""Guardrail methods that depend on agent instance state.

Each function receives the ``StructuredChatAgent`` instance (``agent``) as its
first argument so it can access ``agent.history``, ``agent.extra_context``, etc.
The class retains a thin one-line delegate that forwards ``self``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.services.llm.structured_response import LLMAction, LLMStructuredResponse

from .guardrails import (
    extract_task_ids_from_text,
    extract_review_topic,
    explicit_manuscript_request,
    extract_declared_absolute_paths,
    extract_task_id_from_text,
    is_generic_plan_confirmation,
    is_status_query_only,
    is_task_executable_status,
    literature_backed_review_request,
    local_manuscript_assembly_request,
    looks_like_completion_claim,
    reply_promises_execution,
    requests_abstract_only,
    should_force_plan_first,
)

if TYPE_CHECKING:
    from app.services.plans.plan_models import PlanTree


logger = logging.getLogger(__name__)

_EXPLORATORY_FILE_OPERATIONS = {"read", "list", "exists", "info"}
_FOLLOWTHROUGH_EXECUTE_TOKENS = (
    "run",
    "execute",
    "start",
    "continue",
    "retry",
    "rerun",
    "resume",
    "执行",
    "继续",
    "重试",
    "重新执行",
    "开始",
    "跑",
    "运行",
)
_LOCAL_MANUSCRIPT_PREP_TOOLS = frozenset(
    {
        "web_search",
        "graph_rag",
        "literature_pipeline",
        "file_operations",
        "document_reader",
        "vision_reader",
        "result_interpreter",
    }
)


# ---------------------------------------------------------------------------
# Async guardrails
# ---------------------------------------------------------------------------

async def apply_experiment_fallback(
    agent: Any, structured: LLMStructuredResponse
) -> LLMStructuredResponse:
    """Guardrail: keep writing tools scoped and recover review drafts from retrieval-only actions."""
    user_message = agent._current_user_message or ""
    if not user_message.strip():
        return structured

    writing_actions = [
        action
        for action in structured.actions
        if action.kind == "tool_operation"
        and action.name in {"manuscript_writer", "review_pack_writer"}
    ]

    explicit_request = explicit_manuscript_request(user_message)
    literature_review_request = literature_backed_review_request(user_message)
    extra_context = getattr(agent, "extra_context", {}) or {}
    task_bound = extra_context.get("current_task_id") is not None
    plan_bound = getattr(getattr(agent, "plan_session", None), "plan_id", None) is not None
    local_manuscript_request = local_manuscript_assembly_request(
        user_message,
        plan_bound=plan_bound,
        task_bound=task_bound,
    )

    logger.info(
        "[CHAT][GUARDRAIL][WRITE] explicit=%s literature_review=%s local_assembly=%s actions=%s",
        explicit_request,
        literature_review_request,
        local_manuscript_request,
        [action.name for action in structured.actions],
    )

    if not explicit_request:
        structured.actions = [
            action
            for action in structured.actions
            if not (
                action.kind == "tool_operation"
                and action.name in {"manuscript_writer", "review_pack_writer"}
            )
        ]
        logger.info(
            "[CHAT][GUARDRAIL][WRITE] stripped writing tools because request is not explicit"
        )
        return structured

    def _build_manuscript_action(source_action: Optional[LLMAction] = None) -> LLMAction:
        source_params = (
            dict(source_action.parameters)
            if isinstance(source_action, LLMAction)
            and isinstance(source_action.parameters, dict)
            else {}
        )
        params: Dict[str, Any] = {}
        task_text = str(source_params.get("task") or "").strip() or user_message.strip()
        if task_text:
            params["task"] = task_text
        output_path = str(source_params.get("output_path") or "").strip()
        if output_path:
            params["output_path"] = output_path
        if requests_abstract_only(user_message):
            params["sections"] = ["abstract"]
        else:
            sections = source_params.get("sections")
            if isinstance(sections, list):
                clean_sections = [
                    str(item).strip()
                    for item in sections
                    if isinstance(item, str) and str(item).strip()
                ]
                if clean_sections:
                    params["sections"] = clean_sections
        for key in (
            "max_revisions",
            "evaluation_threshold",
            "keep_workspace",
            "draft_only",
            "generation_model",
            "evaluation_model",
            "merge_model",
            "generation_provider",
            "evaluation_provider",
            "merge_provider",
        ):
            value = source_params.get(key)
            if value is not None:
                params[key] = value
        context_paths = source_params.get("context_paths")
        if isinstance(context_paths, list):
            clean_paths = [
                str(item).strip()
                for item in context_paths
                if isinstance(item, str) and str(item).strip()
            ]
            if clean_paths:
                params["context_paths"] = clean_paths
        elif isinstance(context_paths, str) and context_paths.strip():
            params["context_paths"] = [context_paths.strip()]
        analysis_path = str(source_params.get("analysis_path") or "").strip()
        if analysis_path:
            params["analysis_path"] = analysis_path
        if local_manuscript_request:
            params["draft_only"] = True
        return LLMAction(
            kind="tool_operation",
            name="manuscript_writer",
            parameters=params,
            order=source_action.order if isinstance(source_action, LLMAction) else 1,
            blocking=source_action.blocking if isinstance(source_action, LLMAction) else True,
            retry_policy=source_action.retry_policy if isinstance(source_action, LLMAction) else None,
            metadata=(
                dict(source_action.metadata)
                if isinstance(source_action, LLMAction) and isinstance(source_action.metadata, dict)
                else {}
            ),
        )

    tool_actions = [
        action for action in structured.actions if action.kind == "tool_operation"
    ]

    if local_manuscript_request:
        if writing_actions:
            rewritten_actions: List[LLMAction] = []
            rewrote_review_pack = False
            for action in structured.actions:
                if action.kind == "tool_operation" and action.name == "review_pack_writer":
                    rewritten_actions.append(_build_manuscript_action(action))
                    rewrote_review_pack = True
                    continue
                rewritten_actions.append(action)
            if rewrote_review_pack:
                structured.actions = rewritten_actions
                logger.info(
                    "[CHAT][GUARDRAIL][WRITE] rewrote non-review review_pack_writer action(s) to manuscript_writer"
                )
            else:
                logger.info(
                    "[CHAT][GUARDRAIL][WRITE] keeping existing manuscript_writer action(s) for local assembly"
                )
            return structured

        if tool_actions and not all(
            action.name in _LOCAL_MANUSCRIPT_PREP_TOOLS for action in tool_actions
        ):
            logger.info(
                "[CHAT][GUARDRAIL][WRITE] leaving actions unchanged because non-prep tool present for local assembly: %s",
                [action.name for action in tool_actions],
            )
            return structured

        structured.actions = [_build_manuscript_action()]
        logger.info(
            "[CHAT][GUARDRAIL][WRITE] rewrote prep-only actions to manuscript_writer for local assembly"
        )
        return structured

    if writing_actions:
        logger.info("[CHAT][GUARDRAIL][WRITE] keeping existing writing action(s)")
        return structured

    if not literature_review_request:
        logger.info(
            "[CHAT][GUARDRAIL][WRITE] explicit writing request but not literature-backed review"
        )
        return structured

    retrieval_only_tools = {"web_search", "graph_rag", "literature_pipeline"}
    if tool_actions and not all(action.name in retrieval_only_tools for action in tool_actions):
        logger.info(
            "[CHAT][GUARDRAIL][WRITE] leaving actions unchanged because non-retrieval tool present: %s",
            [action.name for action in tool_actions],
        )
        return structured

    topic = extract_review_topic(user_message) or user_message.strip()
    parameters: Dict[str, Any] = {"topic": topic}
    if requests_abstract_only(user_message):
        parameters["sections"] = ["abstract"]
    if topic and topic != user_message.strip():
        parameters["query"] = topic

    structured.actions = [
        LLMAction(
            kind="tool_operation",
            name="review_pack_writer",
            parameters=parameters,
            order=1,
            blocking=True,
        )
    ]
    logger.info(
        "[CHAT][GUARDRAIL][WRITE] rewrote retrieval-only actions to review_pack_writer topic=%s sections=%s",
        parameters.get("topic"),
        parameters.get("sections"),
    )
    return structured


# ---------------------------------------------------------------------------
# Sync guardrails
# ---------------------------------------------------------------------------

def apply_phagescope_fallback(
    agent: Any, structured: LLMStructuredResponse
) -> LLMStructuredResponse:
    user_message = agent._current_user_message or ""
    if not user_message.strip():
        return structured

    def _wants_results(text: str) -> bool:
        text_lower = text.lower()
        triggers = [
            "report",
            "result",
            "results",
            "quality",
            "evaluation",
            "metrics",
        ]
        avoid = [
            "list",
            "task list",
        ]
        return any(token in text_lower for token in triggers) and not any(
            token in text_lower for token in avoid
        )

    def _infer_result_kind(text: str) -> Optional[str]:
        text_lower = text.lower()
        if any(token in text_lower for token in ("quality", "evaluation", "checkv")):
            return "quality"
        if any(token in text_lower for token in ("protein", "proteins")):
            return "proteins"
        if any(token in text_lower for token in ("fasta", "sequence")):
            return "phagefasta"
        if any(token in text_lower for token in ("tree", "phylogenetic")):
            return "tree"
        if any(token in text_lower for token in ("detail", "details")):
            return "phage_detail"
        if any(token in text_lower for token in ("phage", "bacteriophage")):
            return "phage"
        return None

    def _wants_download(text: str) -> bool:
        tl = text.lower()
        triggers = [
            "download",
            "save",
            "export",
            "save_all",
            "saveall",
        ]
        return any(token in tl for token in triggers)

    def _wants_analysis(text: str) -> bool:
        tl = text.lower()
        triggers = [
            "interpret",
            "analyze",
            "analyse",
            "summarize",
            "summary",
        ]
        return any(token in tl for token in triggers)

    def _extract_accessions(text: str) -> List[str]:
        pattern = re.compile(r"\b[A-Za-z]{1,6}_?\d+(?:\.\d+)?\b")
        matches = pattern.findall(text or "")
        deduped: List[str] = []
        seen = set()
        for item in matches:
            key = item.upper()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _extract_taskid_from_text(text: str) -> Optional[str]:
        # Support patterns like taskid=36322 / task 36322.
        m = re.search(r"(?:taskid\s*=?\s*|task\s*)(\d{4,})", text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"\b(\d{4,})\b", text)
        if m:
            return m.group(1)
        return None

    # Deterministic sequence download guardrail:
    # when user requests FASTA download by accession, inject sequence_fetch.
    if _wants_download(user_message):
        history_text = " ".join(
            str(item.get("content") or "") for item in agent.history[-6:]
        )
        accession_candidates = _extract_accessions(user_message) or _extract_accessions(history_text)
        if accession_candidates:
            try:
                actions: List[LLMAction] = [
                    LLMAction(
                        kind="tool_operation",
                        name="sequence_fetch",
                        parameters={
                            "accessions": accession_candidates,
                            "database": "nuccore",
                            "format": "fasta",
                        },
                        blocking=True,
                        order=1,
                    )
                ]
                if _wants_analysis(user_message):
                    actions.append(
                        LLMAction(
                            kind="tool_operation",
                            name="bio_tools",
                            parameters={
                                "tool_name": "seqkit",
                                "operation": "stats",
                                "input_file": "{{ previous.output_file }}",
                            },
                            blocking=True,
                            order=2,
                        )
                    )
                structured.actions = actions
                if structured.llm_reply and structured.llm_reply.message:
                    if _wants_analysis(user_message):
                        structured.llm_reply.message = (
                            "I will first download FASTA via sequence_fetch, then run seqkit stats via bio_tools."
                        )
                    else:
                        structured.llm_reply.message = (
                            "I will download the requested FASTA using sequence_fetch and return the saved path."
                        )
                return structured
            except Exception:
                return structured

    # Guardrail: for long-running PhageScope workflows, prefer submit-only in this turn.
    # If the model emits submit + result/save_all/download in one response, keep submit only.
    phagescope_actions = [
        action
        for action in structured.actions
        if action.kind == "tool_operation" and action.name == "phagescope"
    ]
    submit_actions = [
        action
        for action in phagescope_actions
        if isinstance(action.parameters, dict)
        and str(action.parameters.get("action") or "").strip().lower() == "submit"
    ]
    if submit_actions and len(phagescope_actions) > 1:
        explicit_taskid = _extract_taskid_from_text(user_message)
        if not (explicit_taskid and (_wants_results(user_message) or _wants_download(user_message))):
            submit_action = sorted(
                submit_actions,
                key=lambda item: (item.order if isinstance(item.order, int) else 10**9),
            )[0]
            normalized_submit = LLMAction.model_validate(submit_action.model_dump())
            normalized_submit.order = 1
            normalized_submit.blocking = True
            structured.actions = [normalized_submit]
            if structured.llm_reply and structured.llm_reply.message:
                structured.llm_reply.message = (
                    "I will first submit the PhageScope task to the background and will not wait "
                    "for remote completion in this turn; after submission, taskid and background status will be returned."
                )
            return structured

    # One-shot UX: when the user asks to download+analyze, inject a deterministic action chain:
    # 1) phagescope save_all (partial 207 is acceptable)
    # 2) read key files from the saved output directory
    if _wants_download(user_message) and _wants_analysis(user_message):
        taskid_text = _extract_taskid_from_text(user_message)
        taskid_from_history = _extract_taskid_from_text(
            " ".join(str(item.get("content") or "") for item in agent.history[-6:])
        )
        taskid_value = taskid_text or taskid_from_history

        try:
            actions: List[LLMAction] = []
            save_params: Dict[str, Any] = {"action": "save_all"}
            if taskid_value:
                save_params["taskid"] = taskid_value
            actions.append(
                LLMAction(
                    kind="tool_operation",
                    name="phagescope",
                    parameters=save_params,
                    blocking=True,
                    order=1,
                )
            )

            # Read files (best-effort). Use placeholders from previous save_all result.
            read_targets = [
                # Use *_rel paths so file_operations can resolve them safely as relative paths.
                ("summary", "{{ previous.summary_file_rel }}"),
                ("quality", "{{ previous.output_directory_rel }}/metadata/quality.json"),
                ("phage_info", "{{ previous.output_directory_rel }}/metadata/phage_info.json"),
                ("proteins_tsv", "{{ previous.output_directory_rel }}/annotation/proteins.tsv"),
                ("proteins_json", "{{ previous.output_directory_rel }}/annotation/proteins.json"),
            ]
            for idx, (label, path_tpl) in enumerate(read_targets, start=2):
                actions.append(
                    LLMAction(
                        kind="tool_operation",
                        name="file_operations",
                        parameters={"operation": "read", "path": path_tpl},
                        blocking=True,
                        order=idx,
                        metadata={
                            "label": label,
                            "optional": True,
                            "use_anchor": True,
                            "preserve_previous": True,
                        },
                    )
                )

            structured.actions = actions
            if structured.llm_reply and structured.llm_reply.message:
                # Keep LLM wording but ensure it doesn't confuse users with tool details.
                structured.llm_reply.message = (
                    "I will first download PhageScope results locally (and continue even if some outputs are missing), "
                    "then read key files and provide a structured interpretation."
                )
            return structured
        except Exception:
            # Fall back to the model's original actions.
            return structured

    if not _wants_results(user_message):
        return structured

    history_text = " ".join(
        str(item.get("content") or "") for item in agent.history[-6:]
    )
    inferred_kind = _infer_result_kind(user_message) or _infer_result_kind(history_text)

    for action in structured.actions:
        if action.kind != "tool_operation" or action.name != "phagescope":
            continue
        params = action.parameters or {}
        action_value = params.get("action")
        # Important: do NOT rewrite explicit task_detail/task_list into result.
        # Users often ask for completion status, and converting to result_kind=phage_detail
        # can hit remote endpoints that are not ready / error-prone.
        if action_value == "result" and not params.get("result_kind"):
            params = dict(params)
            params["result_kind"] = inferred_kind or "quality"
            action.parameters = params

    return structured


def apply_task_execution_followthrough_guardrail(
    agent: Any,
    structured: LLMStructuredResponse,
) -> LLMStructuredResponse:
    # Compatibility default: disable automatic action injection unless explicitly
    # enabled by caller context.
    guardrail_enabled_raw = agent.extra_context.get("followthrough_guardrail_enabled")
    if isinstance(guardrail_enabled_raw, str):
        guardrail_enabled = guardrail_enabled_raw.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    else:
        guardrail_enabled = bool(guardrail_enabled_raw)
    if not guardrail_enabled:
        return structured

    actions_are_exploratory = _actions_are_only_exploratory_file_operations(
        structured.actions
    )
    actions_are_verification_only = _actions_are_only_task_verification(
        structured.actions
    )
    if structured.actions and not actions_are_exploratory and not actions_are_verification_only:
        return structured

    user_message = str(agent._current_user_message or "").strip()
    if not user_message:
        return structured

    reply_text = ""
    if structured.llm_reply and isinstance(structured.llm_reply.message, str):
        reply_text = structured.llm_reply.message.strip()

    lowered_user = user_message.lower()
    request_tier = str(agent.extra_context.get("request_tier") or "").strip().lower()
    intent_type = str(agent.extra_context.get("intent_type") or "").strip().lower()
    routed_execute_intent = (
        request_tier == "execute" or intent_type == "execute_task"
    )
    user_requests_execution = routed_execute_intent or any(
        token in lowered_user for token in _FOLLOWTHROUGH_EXECUTE_TOKENS
    )
    reply_promises = reply_promises_execution(reply_text)

    # For pure status queries, do not inject rerun unless reply explicitly promises execution.
    if is_status_query_only(user_message) and not reply_promises:
        return structured
    if not user_requests_execution and not reply_promises:
        return structured

    plan_id = getattr(agent.plan_session, "plan_id", None)
    if plan_id is None:
        return structured
    try:
        tree = agent.plan_session.repo.get_plan_tree(plan_id)
    except Exception:
        return structured

    target_task_id = resolve_followthrough_target_task_id(
        agent,
        tree=tree,
        user_message=user_message,
        reply_text=reply_text,
    )
    if target_task_id is None:
        return structured

    if actions_are_exploratory:
        logger.info(
            "[CHAT][GUARDRAIL][FOLLOWTHROUGH] replaced exploratory file_operations with rerun_task for task_id=%s",
            target_task_id,
        )
    elif actions_are_verification_only:
        logger.info(
            "[CHAT][GUARDRAIL][FOLLOWTHROUGH] replaced verification-only actions with rerun_task for task_id=%s",
            target_task_id,
        )
    else:
        logger.info(
            "[CHAT][GUARDRAIL][FOLLOWTHROUGH] injected rerun_task for task_id=%s",
            target_task_id,
        )
    structured.actions = [
        LLMAction(
            kind="task_operation",
            name="rerun_task",
            parameters={"task_id": int(target_task_id)},
            order=1,
            blocking=True,
        )
    ]
    return structured


def _actions_are_only_exploratory_file_operations(
    actions: list[LLMAction],
) -> bool:
    if not actions:
        return False
    for action in actions:
        if action.kind != "tool_operation" or action.name != "file_operations":
            return False
        operation = str((action.parameters or {}).get("operation") or "").strip().lower()
        if operation not in _EXPLORATORY_FILE_OPERATIONS:
            return False
    return True


def _actions_are_only_task_verification(
    actions: list[LLMAction],
) -> bool:
    if not actions:
        return False
    for action in actions:
        if action.kind != "task_operation" or action.name != "verify_task":
            return False
    return True


def resolve_followthrough_target_task_id(
    agent: Any,
    *,
    tree: "PlanTree",
    user_message: str,
    reply_text: str,
) -> Optional[int]:
    explicit_scope_ids = agent.extra_context.get("explicit_task_ids")
    if isinstance(explicit_scope_ids, list) and explicit_scope_ids:
        return resolve_explicit_task_scope_target(
            tree,
            explicit_scope_ids,
            allow_cascade_rerun=True,
            auto_include_dependency_closure=True,
        )

    explicit_task_ids = extract_task_ids_from_text(user_message)
    if explicit_task_ids:
        return resolve_explicit_task_scope_target(
            tree,
            explicit_task_ids,
            allow_cascade_rerun=True,
            auto_include_dependency_closure=True,
        )

    explicit_task_id = extract_task_id_from_text(user_message)
    if explicit_task_id is not None:
        if not tree.has_node(explicit_task_id):
            return None
        if not tree.children_ids(explicit_task_id):
            return explicit_task_id
        descendant = first_executable_atomic_descendant(tree, explicit_task_id)
        if descendant is not None:
            return descendant
        return None

    raw_current_task_id = agent.extra_context.get("current_task_id")
    try:
        current_task_id = int(raw_current_task_id) if raw_current_task_id is not None else None
    except (TypeError, ValueError):
        current_task_id = None
    if current_task_id is not None and tree.has_node(current_task_id):
        if not tree.children_ids(current_task_id):
            node = tree.get_node(current_task_id)
            if is_task_executable_status(node.status):
                return node.id
        descendant = first_executable_atomic_descendant(tree, current_task_id)
        if descendant is not None:
            return descendant

    keyword_text = "\n".join(
        part.strip()
        for part in (user_message, reply_text)
        if isinstance(part, str) and part.strip()
    )
    keyword_match = match_atomic_task_by_keywords(tree, keyword_text)
    if keyword_match is not None:
        return keyword_match

    for node in tree.nodes.values():
        if tree.children_ids(node.id):
            continue
        if is_task_executable_status(node.status):
            return node.id
    return None


_CASCADE_COMPLETION_MARKER = "completed as part of parent task"


def _is_cascade_completed(node: Any) -> bool:
    """Return True if the node was marked 'completed' via a parent-cascade rather
    than genuine individual execution.  Such tasks have never produced real
    outputs and should be treated as re-executable when the user explicitly
    requests them.
    """
    status = str(getattr(node, "status", "") or "").strip().lower()
    if status not in ("completed", "done", "success"):
        return False
    result = str(getattr(node, "execution_result", "") or "").strip().lower()
    return _CASCADE_COMPLETION_MARKER in result


def _expand_scope_with_dependency_closure(
    tree: "PlanTree",
    ordered_ids: List[int],
    *,
    allow_cascade_rerun: bool = False,
) -> List[int]:
    expanded: List[int] = []
    seen: set[int] = set()
    visiting: set[int] = set()

    def _visit(task_id: int) -> None:
        if task_id in seen or task_id in visiting or not tree.has_node(task_id):
            return
        visiting.add(task_id)
        node = tree.get_node(task_id)
        for dep_id in getattr(node, "dependencies", []) or []:
            try:
                dep_id_int = int(dep_id)
            except (TypeError, ValueError):
                continue
            if not tree.has_node(dep_id_int):
                continue
            dep_node = tree.get_node(dep_id_int)
            dep_needs_resolution = is_task_executable_status(dep_node.status) or (
                allow_cascade_rerun and _is_cascade_completed(dep_node)
            )
            if dep_needs_resolution:
                _visit(dep_id_int)
        visiting.remove(task_id)
        seen.add(task_id)
        expanded.append(task_id)

    for task_id in ordered_ids:
        _visit(task_id)
    return expanded or ordered_ids


def resolve_explicit_task_scope_target(
    tree: "PlanTree",
    explicit_task_ids: List[int],
    *,
    allow_cascade_rerun: bool = False,
    auto_include_dependency_closure: bool = False,
) -> Optional[int]:
    ordered_ids: List[int] = []
    seen: set[int] = set()
    for raw_task_id in explicit_task_ids:
        try:
            task_id = int(raw_task_id)
        except (TypeError, ValueError):
            continue
        if task_id <= 0 or task_id in seen or not tree.has_node(task_id):
            continue
        seen.add(task_id)
        ordered_ids.append(task_id)

    if not ordered_ids:
        return None

    # Expand any composite (non-leaf) explicit task to its atomic leaf
    # descendants.  Previous code only filtered *already-leaf* IDs and skipped
    # expansion entirely when every explicit ID was a composite task — meaning
    # "execute task 6" always returned None when task 6 had children.
    expanded_ids: List[int] = []
    expanded_seen: set[int] = set()
    for task_id in ordered_ids:
        children = tree.children_ids(task_id)
        if not children:
            if task_id not in expanded_seen:
                expanded_seen.add(task_id)
                expanded_ids.append(task_id)
        else:
            # Use DFS (stack) instead of BFS so that deep subtrees are fully
            # traversed before sibling subtrees.  This ensures that a
            # direct-child leaf (e.g. "3.2.4 visualization", depth=2) is NOT
            # selected ahead of grandchild pipeline tasks (e.g. tasks 34-44,
            # depth=3) that must logically run first.  With BFS the direct
            # child is appended to expanded_ids first and wrongly becomes
            # "current".
            stack = list(reversed(children))  # reversed so pop() yields original order
            while stack:
                child_id = stack.pop()
                if not tree.has_node(child_id):
                    continue
                child_children = tree.children_ids(child_id)
                if child_children:
                    stack.extend(list(reversed(child_children)))
                else:
                    if child_id not in expanded_seen:
                        expanded_seen.add(child_id)
                        expanded_ids.append(child_id)
    if expanded_ids:
        ordered_ids = expanded_ids
    if auto_include_dependency_closure:
        ordered_ids = _expand_scope_with_dependency_closure(
            tree,
            ordered_ids,
            allow_cascade_rerun=allow_cascade_rerun,
        )

    scope_ids = set(ordered_ids)
    ordered_with_deps: List[int] = []
    visiting: set[int] = set()
    visited: set[int] = set()

    def _visit(task_id: int) -> None:
        if task_id in visited or task_id in visiting or not tree.has_node(task_id):
            return
        visiting.add(task_id)
        node = tree.get_node(task_id)
        for dep_id in getattr(node, "dependencies", []) or []:
            try:
                dep_id_int = int(dep_id)
            except (TypeError, ValueError):
                continue
            if dep_id_int in scope_ids:
                _visit(dep_id_int)
        visiting.remove(task_id)
        visited.add(task_id)
        ordered_with_deps.append(task_id)

    for task_id in ordered_ids:
        _visit(task_id)

    for task_id in ordered_with_deps:
        node = tree.get_node(task_id)
        if tree.children_ids(task_id):
            continue
        executable = is_task_executable_status(node.status)
        if not executable:
            # Allow re-execution when the task was only cascade-completed (no real
            # outputs) and the caller has explicitly requested this task.
            if not (allow_cascade_rerun and _is_cascade_completed(node)):
                continue

        unmet_in_scope = False
        unmet_out_of_scope = False
        for dep_id in getattr(node, "dependencies", []) or []:
            try:
                dep_id_int = int(dep_id)
            except (TypeError, ValueError):
                continue
            if not tree.has_node(dep_id_int):
                continue
            dep_node = tree.get_node(dep_id_int)
            # dep_needs_resolution: True when the dep still needs to produce real
            # outputs — either it's pending/failed/skipped in the normal sense, or
            # it was cascade-completed (no real outputs yet) and the caller asked
            # for cascade reruns.  This is intentionally broader than
            # is_task_executable_status: we ask "does this dep still owe outputs?"
            # rather than "is it in a runnable status?".
            dep_needs_resolution = is_task_executable_status(dep_node.status) or (
                allow_cascade_rerun and _is_cascade_completed(dep_node)
            )
            if not dep_needs_resolution:
                continue
            if dep_id_int in scope_ids:
                unmet_in_scope = True
            else:
                unmet_out_of_scope = True
        if unmet_out_of_scope or unmet_in_scope:
            continue
        return task_id

    return None


def resolve_all_explicit_task_scope_targets(
    tree: "PlanTree",
    explicit_task_ids: List[int],
    *,
    allow_cascade_rerun: bool = False,
    auto_include_dependency_closure: bool = False,
) -> List[int]:
    """Return ALL executable leaf tasks for composite task expansion.

    Unlike ``resolve_explicit_task_scope_target`` (which returns only the first
    executable leaf), this function returns every ready-to-execute leaf in
    dependency order.  This enables the agent to execute all subtasks of a
    composite task within a single session, e.g. when the user says "complete
    task 8" and task 8 has children 19→20→21→22.

    Returns an empty list when no executable tasks are found.
    """
    ordered_ids: List[int] = []
    seen: set[int] = set()
    for raw_task_id in explicit_task_ids:
        try:
            task_id = int(raw_task_id)
        except (TypeError, ValueError):
            continue
        if task_id <= 0 or task_id in seen or not tree.has_node(task_id):
            continue
        seen.add(task_id)
        ordered_ids.append(task_id)

    if not ordered_ids:
        return []

    # Expand composites to leaves (DFS so subtree order is preserved)
    expanded_ids: List[int] = []
    expanded_seen: set[int] = set()
    for task_id in ordered_ids:
        children = tree.children_ids(task_id)
        if not children:
            if task_id not in expanded_seen:
                expanded_seen.add(task_id)
                expanded_ids.append(task_id)
        else:
            # DFS: go deep into first child before sibling children so that
            # deeper pipeline tasks are not overtaken by shallower leaves.
            stack = list(reversed(children))
            while stack:
                child_id = stack.pop()
                if not tree.has_node(child_id):
                    continue
                child_children = tree.children_ids(child_id)
                if child_children:
                    stack.extend(list(reversed(child_children)))
                else:
                    if child_id not in expanded_seen:
                        expanded_seen.add(child_id)
                        expanded_ids.append(child_id)
    if expanded_ids:
        ordered_ids = expanded_ids
    if auto_include_dependency_closure:
        ordered_ids = _expand_scope_with_dependency_closure(
            tree,
            ordered_ids,
            allow_cascade_rerun=allow_cascade_rerun,
        )

    scope_ids = set(ordered_ids)
    ordered_with_deps: List[int] = []
    visiting: set[int] = set()
    visited: set[int] = set()

    def _visit(task_id: int) -> None:
        if task_id in visited or task_id in visiting or not tree.has_node(task_id):
            return
        visiting.add(task_id)
        node = tree.get_node(task_id)
        for dep_id in getattr(node, "dependencies", []) or []:
            try:
                dep_id_int = int(dep_id)
            except (TypeError, ValueError):
                continue
            if dep_id_int in scope_ids:
                _visit(dep_id_int)
        visiting.remove(task_id)
        visited.add(task_id)
        ordered_with_deps.append(task_id)

    for task_id in ordered_ids:
        _visit(task_id)

    executable: List[int] = []
    for task_id in ordered_with_deps:
        node = tree.get_node(task_id)
        if tree.children_ids(task_id):
            continue
        is_exec = is_task_executable_status(node.status)
        if not is_exec:
            if not (allow_cascade_rerun and _is_cascade_completed(node)):
                continue

        # Check that all out-of-scope deps are resolved
        unmet_out_of_scope = False
        for dep_id in getattr(node, "dependencies", []) or []:
            try:
                dep_id_int = int(dep_id)
            except (TypeError, ValueError):
                continue
            if not tree.has_node(dep_id_int):
                continue
            if dep_id_int in scope_ids:
                continue  # in-scope deps will be executed before this task
            dep_node = tree.get_node(dep_id_int)
            dep_needs_resolution = is_task_executable_status(dep_node.status) or (
                allow_cascade_rerun and _is_cascade_completed(dep_node)
            )
            if dep_needs_resolution:
                unmet_out_of_scope = True
                break
        if unmet_out_of_scope:
            continue
        executable.append(task_id)

    return executable


def classify_explicit_scope_none_reason(
    tree: "PlanTree",
    explicit_task_ids: List[int],
) -> str:
    """Classify *why* resolve_explicit_task_scope_target returned None.

    Returns:
      "all_completed"  — every reachable leaf is genuinely completed (not cascade)
      "blocked_deps"   — at least one leaf is pending/blocked, OR all leaves are
                         cascade-completed (which means they were blocked by deps
                         even after allow_cascade_rerun — user should be told to
                         resolve upstream tasks first, not "already done")
      "empty"          — no valid task IDs found in the tree

    Note: cascade-completed leaves are intentionally NOT classified as
    "all_completed".  resolve_explicit_task_scope_target(allow_cascade_rerun=True)
    already handles the un-blocked cascade case by returning a valid task id.
    When we reach this function for cascade leaves it means they were blocked by
    unmet out-of-scope dependencies — "blocked_deps" is the right bucket.
    """
    valid_ids: List[int] = []
    for raw_id in explicit_task_ids:
        try:
            tid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if tree.has_node(tid):
            valid_ids.append(tid)

    if not valid_ids:
        return "empty"

    # Expand composite tasks to their atomic leaves.
    leaf_ids: List[int] = []
    for task_id in valid_ids:
        children = tree.children_ids(task_id)
        if not children:
            leaf_ids.append(task_id)
        else:
            queue = list(children)
            while queue:
                child_id = queue.pop(0)
                if not tree.has_node(child_id):
                    continue
                sub_children = tree.children_ids(child_id)
                if sub_children:
                    queue.extend(sub_children)
                else:
                    leaf_ids.append(child_id)

    if not leaf_ids:
        return "empty"

    for leaf_id in leaf_ids:
        node = tree.get_node(leaf_id)
        status = str(getattr(node, "status", "") or "").strip().lower()
        if status not in ("completed", "done", "success"):
            return "blocked_deps"
        # Cascade-completed tasks were never truly executed; if they ended up
        # here (scope resolver still returned None) they must have unmet upstream
        # deps — report as blocked, not done.
        if _is_cascade_completed(node):
            return "blocked_deps"

    return "all_completed"


def apply_completion_claim_guardrail(
    agent: Any,
    structured: LLMStructuredResponse,
) -> LLMStructuredResponse:
    if not structured.llm_reply or not isinstance(structured.llm_reply.message, str):
        return structured
    reply_text = structured.llm_reply.message
    if not looks_like_completion_claim(reply_text):
        return structured

    current_task_id = (getattr(agent, "extra_context", {}) or {}).get("current_task_id")
    plan_id = getattr(getattr(agent, "plan_session", None), "plan_id", None)
    if current_task_id is not None and plan_id is not None:
        try:
            task_id = int(current_task_id)
            tree = getattr(agent, "plan_tree", None)
            if tree is None or not getattr(tree, "has_node", lambda *_: False)(task_id):
                tree = agent.plan_session.repo.get_plan_tree(plan_id)
            if tree is not None and tree.has_node(task_id):
                node = tree.get_node(task_id)
                task_status = str(getattr(node, "status", "") or "").strip().lower()
                if task_status not in {"", "completed", "done", "success"}:
                    structured.llm_reply.message = (
                        f"The reply claimed task completion, but bound task [{task_id}] is currently "
                        f"`{task_status}` in plan state. Do not report completion. State the real "
                        "task status and blocker instead."
                    )
                    return structured
        except Exception:
            pass

    declared_paths = extract_declared_absolute_paths(reply_text)
    if not declared_paths:
        return structured

    missing_paths: List[str] = []
    for raw_path in declared_paths:
        try:
            if not Path(raw_path).exists():
                missing_paths.append(raw_path)
        except Exception:
            missing_paths.append(raw_path)

    if not missing_paths:
        return structured

    missing_block = "\n".join(f"- {path}" for path in missing_paths)
    structured.llm_reply.message = (
        "The reply claimed files were generated, but the following paths do not currently exist, "
        "so completion cannot be confirmed:\n"
        f"{missing_block}\n"
        "Please first verify files are actually written, or clearly state that execution is still in progress."
    )
    return structured


# ---------------------------------------------------------------------------
# Tree-traversal helpers (no agent state needed after static-method extraction)
# ---------------------------------------------------------------------------

def first_executable_atomic_descendant(
    tree: "PlanTree",
    parent_task_id: int,
) -> Optional[int]:
    queue = list(tree.children_ids(parent_task_id))
    while queue:
        node_id = queue.pop(0)
        if not tree.has_node(node_id):
            continue
        children = tree.children_ids(node_id)
        if children:
            queue.extend(children)
            continue
        node = tree.get_node(node_id)
        if is_task_executable_status(node.status):
            # When a task failed due to upstream dependency issues,
            # redirect to the incomplete upstream task instead.
            upstream = _find_blocked_upstream(tree, node)
            if upstream is not None:
                logger.info(
                    "[UPSTREAM_FALLBACK] Task %s blocked by upstream %s — "
                    "redirecting execution to upstream dependency",
                    node.id, upstream,
                )
                return upstream
            return node.id
    return None


# -- Blocked-upstream detection helpers --

_UPSTREAM_BLOCKER_SIGNALS = (
    "blocked_dependency",
    "fewer than",
    "missing filtered data",
    "requires the output from task",
    "ensure task",
    "上游产物不完整",
)


def _execution_result_indicates_blocked_upstream(node: "PlanNode") -> bool:
    """Return True if the node's last execution result suggests upstream data is missing."""
    if node.status != "failed":
        return False
    raw = str(getattr(node, "execution_result", "") or "")
    if not raw:
        return False
    # Fast path: check structured error_category stored in JSON metadata.
    try:
        parsed = json.loads(raw) if raw.strip().startswith("{") else {}
        meta = parsed.get("metadata") or {}
        if meta.get("error_category") == "blocked_dependency":
            return True
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    # Fallback: substring scan on the raw text.
    lowered = raw.lower()
    return any(sig in lowered for sig in _UPSTREAM_BLOCKER_SIGNALS)


def _find_blocked_upstream(
    tree: "PlanTree",
    node: "PlanNode",
) -> Optional[int]:
    """If *node* failed because of an incomplete upstream dependency,
    return the id of that upstream task so execution can be redirected there.

    Only returns an upstream task if:
    - the failed node's execution_result contains a blocker signal, AND
    - an upstream dependency is marked "completed" but is an atomic leaf task
      (i.e. it can be re-run).
    """
    if not _execution_result_indicates_blocked_upstream(node):
        return None
    for dep_id in getattr(node, "dependencies", None) or []:
        try:
            dep_id_int = int(dep_id)
        except (TypeError, ValueError):
            continue
        if not tree.has_node(dep_id_int):
            continue
        dep_node = tree.get_node(dep_id_int)
        # Only redirect to leaf (atomic) tasks that are "completed" but
        # whose outputs were evidently insufficient.
        if tree.children_ids(dep_id_int):
            continue  # composite — skip
        dep_status = str(getattr(dep_node, "status", "") or "").strip().lower()
        if dep_status in ("completed", "done", "success"):
            return dep_id_int
    return None


def match_atomic_task_by_keywords(
    tree: "PlanTree",
    text: str,
) -> Optional[int]:
    merged = str(text or "").strip().lower()
    if not merged:
        return None

    keyword_groups: Dict[str, Tuple[str, ...]] = {
        "abstract": ("abstract",),
        "introduction": ("introduction", "intro"),
        "methods": ("method", "methods"),
        "experiment": ("experiment", "evaluation"),
        "result": ("result", "results"),
        "conclusion": ("conclusion", "summary"),
        "reference": ("reference", "references", "bib"),
    }
    requested_sections = [
        key
        for key, aliases in keyword_groups.items()
        if any(alias in merged for alias in aliases)
    ]
    if not requested_sections:
        return None

    candidates: List[Tuple[int, int]] = []
    for node in tree.nodes.values():
        if tree.children_ids(node.id):
            continue
        if not is_task_executable_status(node.status):
            continue
        node_text = f"{node.display_name()} {node.instruction or ''}".lower()
        score = 0
        for section in requested_sections:
            aliases = keyword_groups.get(section, ())
            if any(alias in node_text for alias in aliases):
                score += 1
        if score > 0:
            candidates.append((score, node.id))

    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates[0][1]


def infer_plan_seed_message(agent: Any, current_message: str) -> Optional[str]:
    current = str(current_message or "").strip()
    history = agent.history or []
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        if role != "user":
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if current and content == current:
            continue
        if is_generic_plan_confirmation(content):
            continue
        return content
    return None


def apply_plan_first_guardrail(
    agent: Any,
    structured: LLMStructuredResponse,
) -> LLMStructuredResponse:
    if getattr(agent.plan_session, "plan_id", None) is not None:
        return structured

    user_message = str(agent._current_user_message or "").strip()
    if not user_message:
        return structured

    seed_message = user_message
    if is_generic_plan_confirmation(user_message):
        # Compatibility: when the model has already produced explicit actions,
        # keep them as-is for generic confirmations (e.g., "okay, create it").
        if structured.actions:
            return structured
        inferred = infer_plan_seed_message(agent, user_message)
        if inferred:
            seed_message = inferred

    if not should_force_plan_first(seed_message, list(structured.actions or [])):
        return structured

    normalized = re.sub(r"\s+", " ", seed_message).strip()
    title_source = normalized
    if "," in title_source:
        title_source = title_source.split(",", 1)[0]
    elif "." in title_source:
        title_source = title_source.split(".", 1)[0]
    title = (title_source or normalized or "New Research Plan")[:80]
    goal = normalized or title

    structured.actions = [
        LLMAction(
            kind="plan_operation",
            name="create_plan",
            parameters={"title": title, "goal": goal},
            order=1,
            blocking=True,
        )
    ]
    return structured


def apply_explicit_plan_review_guardrail(
    agent: Any,
    structured: LLMStructuredResponse,
) -> LLMStructuredResponse:
    plan_id = getattr(getattr(agent, "plan_session", None), "plan_id", None)
    if plan_id is None:
        return structured

    review_required = bool(agent.extra_context.get("requires_plan_review")) or bool(
        agent.extra_context.get("requires_plan_optimize")
    )
    if not review_required:
        return structured

    actions = list(structured.actions or [])
    has_review = any(
        action.kind == "plan_operation" and action.name == "review_plan"
        for action in actions
    )
    if has_review:
        return structured

    review_action = LLMAction(
        kind="plan_operation",
        name="review_plan",
        parameters={"plan_id": int(plan_id)},
        order=1,
        blocking=True,
    )
    optimize_actions = [
        action for action in actions
        if action.kind == "plan_operation" and action.name == "optimize_plan"
    ]
    if optimize_actions:
        rewritten = [review_action]
        next_order = 2
        for action in actions:
            copied = action.model_copy(deep=True)
            copied.order = next_order
            rewritten.append(copied)
            next_order += 1
        structured.actions = rewritten
        logger.info(
            "[CHAT][GUARDRAIL][PLAN_REVIEW] prepended review_plan before optimize_plan for explicit review/optimize request"
        )
        return structured

    structured.actions = [review_action]
    logger.info(
        "[CHAT][GUARDRAIL][PLAN_REVIEW] replaced actions with review_plan for explicit review request"
    )
    return structured
