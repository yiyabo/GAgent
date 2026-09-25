"""DeepThink task-context and manuscript-context cluster of ``agent``.

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
⑤ ``task_context.py``): the DeepThink iteration knobs, the observation-only /
read-only tool tables that gate task-status auto-sync, the manuscript-context
collector and the task-context builders handed to ``DeepThinkAgent``.  The
facade keeps ``_build_deep_think_task_context`` importable for
app/tests/tools/test_code_executor_unscoped_routing.py and re-exports every other
name, so the class call sites are unchanged.

Patch surface: **zero body deviations**.  None of these names is patched in
``app/`` or ``app/tests/``, no patched ``agent`` binding
(``plan_decomposition_jobs`` / job triple / ``execute_tool`` ...) is read here,
and the two facade aliases the cluster used (``_extract_task_artifact_paths_fn``,
``_collect_completed_task_outputs_fn`` — the facade's aliases of
``code_executor_helpers``) are imported directly from that source module under
the same alias names, so every call expression stays verbatim.

``_MANUSCRIPT_CONTEXT_ROOT = Path(__file__).resolve().parents[3]`` resolves to the
same repository root from this sibling (same directory as ``agent.py``).

No logger is used in this cluster; every payload key, status set and log-free
literal is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.deep_think_agent import TaskExecutionContext

from .code_executor_helpers import (
    collect_completed_task_outputs as _collect_completed_task_outputs_fn,
    extract_task_artifact_paths as _extract_task_artifact_paths_fn,
)

_DEEP_THINK_MAX_ITER_DEFAULT = 64
_DEEP_THINK_MAX_ITER_CAP = 128
_READ_ONLY_FILE_OPERATIONS = {"read", "list", "exists", "info", "profile", "census"}
_MANUSCRIPT_CONTEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".bib",
}
_MANUSCRIPT_CONTEXT_ROOT = Path(__file__).resolve().parents[3]
# Tools that only observe / retrieve information and must NOT trigger task-status
# sync regardless of their success flag.  Adding web_search / graph_rag /
# literature_pipeline here prevents a failed search from flipping a task to
# "failed" before the actual execution tool (code_executor) has run.
_OBSERVATION_ONLY_TOOLS = {
    "document_reader",
    "vision_reader",
    "result_interpreter",
    "web_search",
    "lightrag_query",
    "graph_rag",
    "literature_pipeline",
}


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


def _should_auto_sync_task_status(
    tool_name: str,
    params: Optional[Dict[str, Any]],
    result: Optional[Dict[str, Any]] = None,
) -> bool:
    """Skip task-status auto-sync for exploratory tool calls.

    Read-only file inspection is often used as a precursor to the real task
    execution. Treating these probe failures as task failures makes the plan UI
    flip to red even when a later code execution succeeds.
    """
    if tool_name in _OBSERVATION_ONLY_TOOLS:
        return False
    if tool_name == "terminal_session":
        if not isinstance(params, dict):
            return False
        operation = str(params.get("operation") or "").strip().lower()
        if operation != "write":
            return False
        verification_state = ""
        if isinstance(result, dict):
            verification_state = str(
                result.get("verification_state") or ""
            ).strip().lower()
        return verification_state == "verified_success"
    if tool_name != "file_operations" or not isinstance(params, dict):
        return True

    operation = str(params.get("operation") or "").strip().lower()
    return operation not in _READ_ONLY_FILE_OPERATIONS


def _task_supports_paper_writing(node: Any) -> bool:
    metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
    paper_meta = node.paper_metadata() if hasattr(node, "paper_metadata") else None
    acceptance = metadata.get("acceptance_criteria") if isinstance(metadata, dict) else None
    acceptance_category = (
        str(acceptance.get("category") or "").strip().lower()
        if isinstance(acceptance, dict)
        else ""
    )
    return bool(
        metadata.get("paper_mode")
        or getattr(paper_meta, "paper_section", None)
        or getattr(paper_meta, "paper_role", None)
        or getattr(paper_meta, "paper_context_paths", None)
        or acceptance_category == "paper"
    )


def _looks_like_manuscript_context_file(path: Any) -> bool:
    text = str(path or "").strip()
    if not text or text.endswith("/"):
        return False
    if any(token in text for token in ("*", "?", "[", "]")):
        return False
    _root, ext = os.path.splitext(text.replace("\\", "/"))
    return ext.lower() in _MANUSCRIPT_CONTEXT_EXTENSIONS


def _manuscript_context_exists(path: Any) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    try:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = (_MANUSCRIPT_CONTEXT_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
    except Exception:
        return False
    return candidate.is_file()


def _collect_completed_manuscript_context_paths(
    plan_tree: Any,
    *,
    current_task_id: Optional[int],
    max_paths: int = 24,
) -> List[str]:
    if plan_tree is None or not hasattr(plan_tree, "nodes"):
        return []

    ranked: List[Tuple[int, int, int, int, str]] = []
    seen: set[str] = set()

    for node in plan_tree.nodes.values():
        node_id = getattr(node, "id", None)
        if current_task_id is not None and node_id == current_task_id:
            continue
        status = str(getattr(node, "status", "") or "").strip().lower()
        if status not in {"completed", "done", "success"}:
            continue

        metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
        paper_meta = node.paper_metadata() if hasattr(node, "paper_metadata") else None
        candidate_paths: List[str] = []

        raw_paper_context_paths = (
            list(getattr(paper_meta, "paper_context_paths", []) or [])
            if paper_meta is not None
            else metadata.get("paper_context_paths")
        )
        if isinstance(raw_paper_context_paths, list):
            for item in raw_paper_context_paths:
                text = str(item or "").strip()
                if text:
                    candidate_paths.append(text)

        acceptance = metadata.get("acceptance_criteria") if isinstance(metadata, dict) else None
        checks = acceptance.get("checks") if isinstance(acceptance, dict) else None
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                for key in ("path", "glob"):
                    text = str(check.get(key) or "").strip()
                    if text:
                        candidate_paths.append(text)

        try:
            candidate_paths.extend(_extract_task_artifact_paths_fn(node))
        except Exception:
            pass

        paper_priority = 0 if _task_supports_paper_writing(node) else 1
        for raw_path in candidate_paths:
            text = str(raw_path or "").strip()
            if not _looks_like_manuscript_context_file(text) or text in seen:
                continue
            seen.add(text)
            lowered = text.replace("\\", "/").lower()
            path_priority = 0
            if "/manuscript/" in lowered or lowered.startswith("manuscript/"):
                path_priority = 0
            elif lowered.endswith(".md"):
                path_priority = 1
            elif lowered.endswith(".bib"):
                path_priority = 2
            else:
                path_priority = 3
            exists_priority = 0 if _manuscript_context_exists(text) else 1
            ranked.append((paper_priority, exists_priority, path_priority, -(int(node_id or 0)), text))

    ranked.sort()
    return [path for *_meta, path in ranked[:max_paths]]


def _build_deep_think_task_context(
    agent: "StructuredChatAgent",
    *,
    user_message: str,
) -> Optional[TaskExecutionContext]:
    raw_task_id = getattr(agent, "extra_context", {}).get("current_task_id")
    try:
        task_id = int(raw_task_id) if raw_task_id is not None else None
    except (TypeError, ValueError):
        task_id = None
    plan_id = getattr(getattr(agent, "plan_session", None), "plan_id", None)
    if task_id is None or plan_id is None:
        return None

    tree = getattr(agent, "plan_tree", None)
    if tree is None or not getattr(tree, "has_node", lambda *_: False)(task_id):
        try:
            tree = agent.plan_session.repo.get_plan_tree(plan_id)
        except Exception:
            return None
    if tree is None or not getattr(tree, "has_node", lambda *_: False)(task_id):
        return None

    try:
        node = tree.get_node(task_id)
    except Exception:
        return None

    task_name = ""
    try:
        task_name = str(node.display_name()).strip()
    except Exception:
        task_name = str(getattr(node, "name", "") or "").strip()
    task_instruction = str(getattr(node, "instruction", "") or "").strip() or str(user_message or "").strip()
    context_summary = str(getattr(node, "context_combined", "") or "").strip() or None
    context_sections = list(getattr(node, "context_sections", []) or [])
    def _build_task_output_entry(source_node: Any, source_task_id: int, *, relationship: str) -> Optional[Dict[str, Any]]:
        dep_status = str(getattr(source_node, "status", "") or "").strip().lower()
        result_text = str(getattr(source_node, "execution_result", "") or "").strip()
        if len(result_text) > 500:
            result_text = result_text[:500].rstrip() + "..."
        artifact_paths: List[str] = []
        try:
            artifact_paths = _extract_task_artifact_paths_fn(source_node)
        except Exception:
            artifact_paths = []
        if not artifact_paths and not result_text:
            return None
        return {
            "task_id": source_task_id,
            "task_name": str(source_node.display_name()).strip(),
            "status": dep_status,
            "execution_result": result_text,
            "artifact_paths": artifact_paths,
            "relationship": relationship,
        }

    def _expand_explicit_scope_leaf_ids() -> List[int]:
        raw_explicit = (getattr(agent, "extra_context", {}) or {}).get("explicit_task_ids", []) or []
        ordered: List[int] = []
        seen: set[int] = set()

        def _visit(task_id_to_visit: int) -> None:
            if task_id_to_visit in seen or not tree.has_node(task_id_to_visit):
                return
            children = tree.children_ids(task_id_to_visit)
            if not children:
                seen.add(task_id_to_visit)
                ordered.append(task_id_to_visit)
                return
            seen.add(task_id_to_visit)
            for child_id in children:
                _visit(child_id)

        for raw_task_id in raw_explicit:
            try:
                explicit_task_id = int(raw_task_id)
            except (TypeError, ValueError):
                continue
            _visit(explicit_task_id)
        return ordered

    dependency_outputs: List[Dict[str, Any]] = []
    seen_dependency_ids: set[int] = set()

    if bool((getattr(agent, "extra_context", {}) or {}).get("explicit_task_override")):
        ordered_scope_leaf_ids = _expand_explicit_scope_leaf_ids()
        if task_id in ordered_scope_leaf_ids:
            current_index = ordered_scope_leaf_ids.index(task_id)
            for prior_task_id in reversed(ordered_scope_leaf_ids[:current_index]):
                if len(dependency_outputs) >= 3:
                    break
                if not tree.has_node(prior_task_id):
                    continue
                prior_node = tree.get_node(prior_task_id)
                prior_status = str(getattr(prior_node, "status", "") or "").strip().lower()
                if prior_status not in {"completed", "done", "success"}:
                    continue
                prior_entry = _build_task_output_entry(
                    prior_node,
                    prior_task_id,
                    relationship="preceding_scope_task",
                )
                if prior_entry is None:
                    continue
                dependency_outputs.append(prior_entry)
                seen_dependency_ids.add(prior_task_id)

    raw_dependencies = getattr(node, "dependencies", []) or []
    for dep_id in raw_dependencies[:6]:
        try:
            dep_id_int = int(dep_id)
        except (TypeError, ValueError):
            continue
        if dep_id_int in seen_dependency_ids or not tree.has_node(dep_id_int):
            continue
        dep_node = tree.get_node(dep_id_int)
        dep_entry = _build_task_output_entry(
            dep_node,
            dep_id_int,
            relationship="declared_dependency",
        )
        if dep_entry is None:
            continue
        dependency_outputs.append(dep_entry)
        seen_dependency_ids.add(dep_id_int)

    completed_outputs_summary = ""
    try:
        completed_outputs_summary = _collect_completed_task_outputs_fn(tree, task_id) or ""
    except Exception:
        completed_outputs_summary = ""
    if completed_outputs_summary:
        addition = f"Completed task outputs:\n{completed_outputs_summary}"
        context_summary = f"{context_summary}\n\n{addition}" if context_summary else addition

    # Inject todo-list summary if available from cascade context
    _extra_ctx = getattr(agent, "extra_context", {}) or {}
    _todo_summary = _extra_ctx.get("todo_list_summary")
    if _todo_summary and isinstance(_todo_summary, str):
        _todo_block = f"Todo-list execution plan:\n{_todo_summary}"
        context_summary = f"{context_summary}\n\n{_todo_block}" if context_summary else _todo_block

    return TaskExecutionContext(
        task_id=task_id,
        task_name=task_name or None,
        task_instruction=task_instruction or None,
        dependency_outputs=dependency_outputs,
        plan_outline=str(getattr(tree, "title", "") or "").strip() or None,
        context_summary=context_summary,
        context_sections=context_sections,
        explicit_task_ids=[
            int(item)
            for item in (getattr(agent, "extra_context", {}) or {}).get("explicit_task_ids", []) or []
            if str(item).strip().isdigit()
        ],
        explicit_task_override=bool((getattr(agent, "extra_context", {}) or {}).get("explicit_task_override")),
    )


def _refresh_deep_think_runtime_context(
    agent: "StructuredChatAgent",
    *,
    dt_agent: Any = None,
    task_context: Optional[TaskExecutionContext],
    user_message: str,
) -> None:
    current_task_id = (getattr(agent, "extra_context", {}) or {}).get("current_task_id")
    pending_scope_task_ids = (getattr(agent, "extra_context", {}) or {}).get("pending_scope_task_ids")
    if dt_agent is not None and isinstance(getattr(dt_agent, "request_profile", None), dict):
        dt_agent.request_profile["current_task_id"] = current_task_id
        if isinstance(pending_scope_task_ids, list):
            dt_agent.request_profile["pending_scope_task_ids"] = list(pending_scope_task_ids)

    if task_context is None:
        return

    refreshed = _build_deep_think_task_context(agent, user_message=user_message)
    if refreshed is None:
        return

    task_context.task_id = refreshed.task_id
    task_context.task_name = refreshed.task_name
    task_context.task_instruction = refreshed.task_instruction
    task_context.dependency_outputs = list(refreshed.dependency_outputs)
    task_context.plan_outline = refreshed.plan_outline
    task_context.constraints = list(refreshed.constraints)
    task_context.skill_context = refreshed.skill_context
    task_context.context_summary = refreshed.context_summary
    task_context.context_sections = list(refreshed.context_sections)
    task_context.paper_context_paths = list(refreshed.paper_context_paths)
    task_context.explicit_task_ids = list(refreshed.explicit_task_ids)
    task_context.explicit_task_override = refreshed.explicit_task_override
