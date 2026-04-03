"""Code executor helpers for atomic task context, CSV normalization,
AMEM experience summarization, prompt composition, and placeholder resolution.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX, get_settings

from .guardrail_handlers import resolve_explicit_task_scope_target
from .session_helpers import _find_key_recursive

if TYPE_CHECKING:
    from app.services.llm.structured_response import LLMAction
    from app.services.plans.plan_models import PlanNode

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*previous\.([^\}]+)\s*\}\}")

# ---------------------------------------------------------------------------
# Conversation summary builder for CC context injection
# ---------------------------------------------------------------------------

_CC_CONVERSATION_BUDGET = 3_000  # characters


def build_conversation_summary_for_cc(
    history: List[Dict[str, Any]],
    budget: int = _CC_CONVERSATION_BUDGET,
) -> str:
    """Build a compact conversation summary from recent chat history.

    Iterates from newest to oldest, keeping messages until *budget* characters
    are exhausted.  Each message is capped at 240 chars to avoid a single huge
    reply blowing the budget.
    """
    if not history:
        return ""
    try:
        tail_n = max(
            1,
            min(
                CHAT_HISTORY_ABS_MAX,
                int(getattr(get_settings(), "chat_history_max_messages", 80)),
            ),
        )
    except Exception:
        tail_n = 80
    PER_MSG_CAP = 240
    lines: List[str] = []
    used = 0
    for msg in reversed(history[-tail_n:]):
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) > PER_MSG_CAP:
            content = content[:PER_MSG_CAP].rstrip() + "..."
        line = f"[{role}]: {content}"
        if used + len(line) > budget:
            break
        lines.insert(0, line)
        used += len(line)
    if not lines:
        return ""
    return "\n".join(lines)


def collect_completed_task_outputs(
    plan_tree: Any,
    current_task_id: int,
    max_chars: int = 4000,
) -> str:
    """Gather compact completed-task summaries plus explicit artifact paths."""
    if plan_tree is None or not hasattr(plan_tree, "nodes"):
        return ""
    parts: List[str] = []
    used = 0
    for node in plan_tree.nodes.values():
        if node.id == current_task_id:
            continue
        status = (node.status or "").strip().lower()
        if status not in ("completed", "done"):
            continue
        result_text = (node.execution_result or "").strip()
        if not result_text:
            continue
        snippet = result_text[:500]
        if len(result_text) > 500:
            snippet += "..."
        entry_lines = [f"- Task [{node.id}] {node.display_name()}: {snippet}"]
        artifact_paths = extract_task_artifact_paths(node)
        if artifact_paths:
            joined = "; ".join(artifact_paths[:4])
            if len(artifact_paths) > 4:
                joined += "; ..."
            entry_lines.append(f"  Artifact paths: {joined}")
        entry = "\n".join(entry_lines)
        if used + len(entry) > max_chars:
            break
        parts.append(entry)
        used += len(entry)
    return "\n".join(parts)


def _coerce_execution_payload(value: Any) -> Optional[Dict[str, Any]]:
    payload = value
    if hasattr(value, "execution_result"):
        payload = getattr(value, "execution_result")
    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _collect_candidate_artifact_paths(payload: Any, *, max_paths: int = 20) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        found.append(text)

    def _visit(value: Any, key: Optional[str] = None) -> None:
        if len(found) >= max_paths:
            return
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                lowered = str(item_key or "").strip().lower()
                if lowered in {"artifact_paths", "produced_files"} and isinstance(
                    item_value, (list, tuple, set)
                ):
                    for item in item_value:
                        _add(item)
                    continue
                if lowered in {"manifest_path", "preview_path", "report_path"}:
                    _add(item_value)
                    continue
                if isinstance(item_value, (dict, list, tuple, set)):
                    _visit(item_value, key=lowered)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _visit(item, key=key)

    _visit(payload)
    return found[:max_paths]


def _is_flat_session_results_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    if "/runtime/" not in normalized or "/results/" not in normalized:
        return False
    prefix, _, suffix = normalized.partition("/results/")
    if "/session_" not in prefix:
        return False
    if not suffix:
        return False
    return "/" not in suffix.strip("/")


def _is_stale_flat_session_results_path(path: str) -> bool:
    if not _is_flat_session_results_path(path):
        return False
    candidate = Path(str(path))
    try:
        return candidate.is_file() and candidate.stat().st_size <= 1
    except OSError:
        return False


def _is_flat_relative_results_alias(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized.startswith("results/"):
        return False
    suffix = normalized[len("results/") :].strip("/")
    return bool(suffix) and "/" not in suffix


def extract_task_artifact_paths(
    execution_result: Any,
    *,
    max_paths: int = 8,
) -> List[str]:
    payload = _coerce_execution_payload(execution_result)
    if payload is None:
        return []

    raw_paths = _collect_candidate_artifact_paths(payload, max_paths=max_paths * 3)
    absolute: List[str] = []
    relative: List[str] = []
    for item in raw_paths:
        text = str(item).strip()
        if (
            not text
            or _is_stale_flat_session_results_path(text)
            or _is_flat_relative_results_alias(text)
        ):
            continue
        if text.startswith("/"):
            absolute.append(text)
        else:
            relative.append(text)

    ordered: List[str] = []
    seen: set[str] = set()
    for bucket in (absolute, relative):
        for item in bucket:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
            if len(ordered) >= max_paths:
                return ordered
    return ordered


def resolve_code_executor_task_context(agent: Any) -> Tuple[Optional["PlanNode"], Optional[str]]:
    plan_id = agent.plan_session.plan_id
    if plan_id is None:
        return None, "missing_plan_binding"

    try:
        tree = agent.plan_session.repo.get_plan_tree(plan_id)
    except Exception:
        return None, "plan_tree_unavailable"

    explicit_task_ids = agent.extra_context.get("explicit_task_ids")
    if isinstance(explicit_task_ids, list) and explicit_task_ids:
        explicit_target = resolve_explicit_task_scope_target(tree, explicit_task_ids)
        if explicit_target is None:
            return None, "explicit_task_scope_blocked"
        agent.extra_context["current_task_id"] = int(explicit_target)
        agent.extra_context["task_id"] = int(explicit_target)
        agent.extra_context["_current_task_source"] = "request"
        task_id = explicit_target
    else:
        raw_task_id = agent.extra_context.get("current_task_id")
        if raw_task_id is None:
            return None, "missing_target_task"

        try:
            task_id = int(raw_task_id)
        except (TypeError, ValueError):
            return None, "invalid_target_task"

    if not tree.has_node(task_id):
        return None, "target_task_not_found"

    node = tree.get_node(task_id)
    task_source = str(agent.extra_context.get("_current_task_source") or "").strip().lower()
    explicit_task_selected = (
        agent.extra_context.get("task_id") is not None or task_source == "request"
    )
    if tree.children_ids(task_id):
        if task_source == "session" and not explicit_task_selected:
            # Session-persisted composite/root task ids are often stale for ad-hoc
            # prompts. Require explicit task selection instead of silent redirect.
            return None, "target_task_not_atomic"
        atomic_task_id = agent._first_executable_atomic_descendant(tree, task_id)
        if atomic_task_id is None:
            return None, "target_task_not_atomic"
        try:
            agent.extra_context["current_task_id"] = int(atomic_task_id)
        except (TypeError, ValueError):
            pass
        node = tree.get_node(atomic_task_id)
        logger.info(
            "[CODE_EXECUTOR] Redirected composite task %s to atomic descendant %s",
            task_id,
            atomic_task_id,
        )

    return node, None


def normalize_csv_arg(value: Any) -> Optional[str]:
    tokens: List[str] = []
    if value is None:
        return None
    if isinstance(value, str):
        raw_tokens = value.split(",")
        tokens = [token.strip() for token in raw_tokens if token.strip()]
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if item is None:
                continue
            item_text = str(item).strip()
            if not item_text:
                continue
            if "," in item_text:
                tokens.extend(part.strip() for part in item_text.split(",") if part.strip())
            else:
                tokens.append(item_text)
    else:
        item_text = str(value).strip()
        if item_text:
            tokens.append(item_text)

    if not tokens:
        return None

    deduped: List[str] = []
    seen = set()
    for token in tokens:
        normalized = token.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(token)
    return ",".join(deduped) if deduped else None


def summarize_amem_experiences_for_cc(
    experiences: List[Dict[str, Any]],
    *,
    max_items: int = 3,
) -> str:
    if not experiences:
        return ""

    lines: List[str] = []
    for exp in experiences[:max_items]:
        content = str(exp.get("content") or "")
        score = exp.get("score")
        score_text = ""
        try:
            if score is not None:
                score_text = f"{float(score):.2f}"
        except (TypeError, ValueError):
            score_text = ""

        status_match = re.search(r"status:\s*([^\n]+)", content, flags=re.IGNORECASE)
        key_match = re.search(r"##\s*key findings\s*([\s\S]+)", content, flags=re.IGNORECASE)
        key_text = ""
        if key_match:
            key_text = key_match.group(1).splitlines()[0].strip()
        if not key_text:
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                key_text = line
                break
        if not key_text:
            key_text = "No concise finding extracted."
        key_text = re.sub(r"\s+", " ", key_text)[:220]

        segments: List[str] = []
        if score_text:
            segments.append(f"score={score_text}")
        if status_match:
            segments.append(status_match.group(1).strip())
        header = f"[{' | '.join(segments)}] " if segments else ""
        lines.append(f"- {header}{key_text}")

    return "\n".join(lines)


def compose_code_executor_atomic_task_prompt(
    *,
    task_node: "PlanNode",
    original_task: str,
    amem_hints: str = "",
    data_context: Optional[str] = None,
    conversation_summary: Optional[str] = None,
) -> str:
    task_instruction = (task_node.instruction or "").strip() or original_task.strip()
    user_task_context = original_task.strip()
    if len(user_task_context) > 1200:
        user_task_context = user_task_context[:1200].rstrip() + "..."

    lines: List[str] = [
        "[OUTER AGENT EXECUTION CONTRACT]",
        "You are a code execution worker for ONE atomic task. Planning is forbidden.",
        f"Plan ID: {task_node.plan_id}",
        f"Task ID: {task_node.id}",
        f"Task Name: {task_node.display_name()}",
        "",
        "Atomic task objective:",
        task_instruction or "No instruction provided.",
        "",
        "Mandatory rules:",
        "- Execute ONLY this atomic task.",
        "- Do NOT create roadmap, decomposition, or extra tasks.",
        "- Do NOT execute sibling or downstream tasks.",
        "- Keep outputs scoped to the current task deliverables.",
        "- Do NOT assume prerequisite files live in the current run directory unless an explicit absolute path says so.",
        "- For immutable source inputs such as metadata tables or raw datasets, prefer the canonical absolute path from the task description or primary data directory over session-temp duplicates.",
        "- For upstream derived artifacts, prefer explicit absolute deliverable paths from previous steps or session results; do not reconstruct them heuristically.",
        "- If a session-temp copy conflicts with a canonical source file, prefer the canonical source for raw inputs and the explicit upstream deliverable path for derived outputs.",
        "- Treat flat files directly under a session root `results/` directory as convenience copies only; they may be stale and are not canonical raw inputs.",
        "- Ignore zero-byte or non-parseable session-temp duplicates when a canonical source path exists elsewhere.",
        "- For integration or aggregation tasks, if the explicit upstream artifact list is incomplete, report a blocked dependency instead of searching the session root for same-named replacements.",
        "- If this task still needs decomposition or broader planning, STOP and output exactly:",
        "  STATUS: BLOCKED_SCOPE",
        "  REASON: NEED_ATOMIC_TASK",
        "  DETAIL: <one sentence>",
    ]

    if user_task_context and user_task_context != task_instruction:
        lines.extend(
            [
                "",
                "User-provided context (reference only, do not expand scope):",
                user_task_context,
            ]
        )

    if data_context:
        lines.extend(
            [
                "",
                "Available data from previous steps (use these ABSOLUTE paths directly):",
                data_context,
            ]
        )

    if conversation_summary:
        lines.extend(
            [
                "",
                "Recent conversation context (reference only, do not expand scope):",
                conversation_summary,
            ]
        )

    if amem_hints:
        lines.extend(
            [
                "",
                "Historical execution hints (reference only, never expand scope):",
                amem_hints,
            ]
        )

    return "\n".join(lines)


def resolve_previous_path(
    previous_result: Dict[str, Any], path: str
) -> Optional[Any]:
    if not path:
        return None
    if path in {"taskid", "task_id"}:
        return _find_key_recursive(previous_result, "taskid") or _find_key_recursive(
            previous_result, "task_id"
        )
    current: Any = previous_result
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return None
            if 0 <= index < len(current):
                current = current[index]
                continue
        return None
    if current is not None:
        return current
    fallback_key = path.split(".")[-1]
    return _find_key_recursive(previous_result, fallback_key)


def resolve_placeholders_in_value(
    value: Any, previous_result: Dict[str, Any]
) -> Any:
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            token = match.group(1).strip()
            resolved = resolve_previous_path(previous_result, token)
            if resolved is None:
                return match.group(0)
            return str(resolved)

        return PLACEHOLDER_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {
            key: resolve_placeholders_in_value(item, previous_result)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_placeholders_in_value(item, previous_result)
            for item in value
        ]
    return value


def resolve_action_placeholders(
    action: "LLMAction", previous_result: Optional[Dict[str, Any]]
) -> "LLMAction":
    if not previous_result:
        return action
    if not isinstance(action.parameters, dict):
        return action
    resolved = resolve_placeholders_in_value(
        action.parameters, previous_result
    )
    action.parameters = resolved
    return action
