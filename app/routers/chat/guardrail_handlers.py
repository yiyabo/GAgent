"""Guardrail methods that depend on agent instance state.

Each function receives the ``StructuredChatAgent`` instance (``agent``) as its
first argument so it can access ``agent.history``, ``agent.extra_context``, etc.
The class retains a thin one-line delegate that forwards ``self``.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.services.llm.structured_response import LLMAction, LLMStructuredResponse

from .guardrails import (
    explicit_manuscript_request,
    extract_declared_absolute_paths,
    extract_task_id_from_text,
    is_generic_plan_confirmation,
    is_status_query_only,
    is_task_executable_status,
    looks_like_completion_claim,
    reply_promises_execution,
    should_force_plan_first,
)

if TYPE_CHECKING:
    from app.services.plans.plan_models import PlanTree


# ---------------------------------------------------------------------------
# Async guardrails
# ---------------------------------------------------------------------------

async def apply_experiment_fallback(
    agent: Any, structured: LLMStructuredResponse
) -> LLMStructuredResponse:
    """Guardrail: only allow manuscript_writer when the user explicitly asks to write a paper."""
    user_message = agent._current_user_message or ""
    if not user_message.strip():
        return structured

    manuscript_actions = [
        action
        for action in structured.actions
        if action.kind == "tool_operation" and action.name == "manuscript_writer"
    ]
    if not manuscript_actions:
        return structured

    if not explicit_manuscript_request(user_message):
        structured.actions = [
            action
            for action in structured.actions
            if not (action.kind == "tool_operation" and action.name == "manuscript_writer")
        ]
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

    def _extract_taskid_from_text(text: str) -> Optional[str]:
        # Support patterns like taskid=36322 / task 36322.
        m = re.search(r"(?:taskid\s*=?\s*|task\s*)(\d{4,})", text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"\b(\d{4,})\b", text)
        if m:
            return m.group(1)
        return None

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

    if structured.actions:
        return structured

    user_message = str(agent._current_user_message or "").strip()
    if not user_message:
        return structured

    reply_text = ""
    if structured.llm_reply and isinstance(structured.llm_reply.message, str):
        reply_text = structured.llm_reply.message.strip()

    lowered_user = user_message.lower()
    execute_tokens = (
        "run",
        "execute",
        "start",
        "continue",
        "retry",
        "rerun",
        "resume",
    )
    user_requests_execution = any(token in lowered_user for token in execute_tokens)
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


def resolve_followthrough_target_task_id(
    agent: Any,
    *,
    tree: "PlanTree",
    user_message: str,
    reply_text: str,
) -> Optional[int]:
    explicit_task_id = extract_task_id_from_text(user_message)
    if explicit_task_id is not None and tree.has_node(explicit_task_id):
        if not tree.children_ids(explicit_task_id):
            return explicit_task_id
        descendant = first_executable_atomic_descendant(tree, explicit_task_id)
        if descendant is not None:
            return descendant

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


def apply_completion_claim_guardrail(
    agent: Any,
    structured: LLMStructuredResponse,
) -> LLMStructuredResponse:
    _ = agent
    if not structured.llm_reply or not isinstance(structured.llm_reply.message, str):
        return structured
    reply_text = structured.llm_reply.message
    if not looks_like_completion_claim(reply_text):
        return structured

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
            return node.id
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
