"""Tool-result payload extraction, validation and answer predicates for the
DeepThink agent (god-class split, behaviour zero-change).

Each function here is the body of the like-named DeepThinkAgent method with
`self` renamed to `agent` (`cls` kept); the class keeps thin wrappers with
the same decorators. Display-family helpers (detect_reasoning_language,
_localized_text, is_process_only_answer) stay in deep_think_agent and are
reached through the late-bound `_dta()` so their monkeypatch surface is
unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence

from app.services.deep_think.models import TaskExecutionContext, ThinkingStep
from app.services.response_style import sanitize_professional_response_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_MULTI_TOOL_RESULT_LINE_RE = re.compile(
    r"^\[(?P<tool>[^\]]+)\]\s+(?P<payload>\{.*\})$",
    re.DOTALL,
)


def _unwrap_tool_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the innermost result dict, handling nested {result: {...}} wrappers."""
    inner = payload.get("result")
    return inner if isinstance(inner, dict) else payload


def _extract_tool_result_payload(cls: Any, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = item.get("tool_result")
    if isinstance(payload, dict):
        return payload

    raw_text = item.get("tool_result_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
        return parsed["result"]
    return parsed if isinstance(parsed, dict) else None


def _tool_counts_as_real_execution(cls: Any, item: Dict[str, Any]) -> bool:
    tool_name = str(item.get("tool_name") or "").strip().lower()
    if tool_name not in cls._CODE_EXECUTION_TOOLS:
        return False
    if tool_name != "terminal_session":
        return True

    payload = cls._extract_tool_result_payload(item)
    if not isinstance(payload, dict):
        return False

    params = item.get("tool_params")
    operation = str(
        payload.get("operation")
        or (params.get("operation") if isinstance(params, dict) else "")
        or ""
    ).strip().lower()
    if operation != "write":
        return False

    verification_state = str(payload.get("verification_state") or "").strip().lower()
    return verification_state == "verified_success"


def _iter_tool_payload_dicts(cls: Any, payload: Any) -> Iterable[Dict[str, Any]]:
    current = payload
    visited: set[int] = set()
    while isinstance(current, dict) and id(current) not in visited:
        visited.add(id(current))
        yield current
        nested = current.get("result")
        if not isinstance(nested, dict):
            break
        current = nested


def _payload_dict_indicates_verified_success(cls: Any, candidate: Dict[str, Any]) -> bool:
    verification_state = str(candidate.get("verification_state") or "").strip().lower()
    if verification_state == "verified_success":
        return True
    verification_status = str(candidate.get("verification_status") or "").strip().lower()
    if verification_status == "passed":
        return True
    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        metadata_verification_status = str(
            metadata.get("verification_status") or ""
        ).strip().lower()
        if metadata_verification_status == "passed":
            return True
        verification = metadata.get("verification")
        if isinstance(verification, dict):
            verification_status = str(verification.get("status") or "").strip().lower()
            if verification_status == "passed":
                return True
    return False


def _tool_results_indicate_verified_success(cls: Any, tool_results: Sequence[Dict[str, Any]]) -> bool:
    for item in tool_results:
        payload = item.get("tool_result")
        for candidate in cls._iter_tool_payload_dicts(payload):
            if cls._payload_dict_indicates_verified_success(candidate):
                return True
    return False


def _collect_verified_output_refs_from_tool_results(
    cls: Any,
    tool_results: Sequence[Dict[str, Any]],
) -> List[str]:
    collected: List[str] = []
    seen: set[str] = set()
    for item in tool_results:
        payload = item.get("tool_result")
        for candidate in cls._iter_tool_payload_dicts(payload):
            if not cls._payload_dict_indicates_verified_success(candidate):
                continue
            artifact_verification = candidate.get("artifact_verification")
            if not isinstance(artifact_verification, dict):
                continue
            raw_outputs = artifact_verification.get("verified_outputs")
            if not isinstance(raw_outputs, list) or not raw_outputs:
                raw_outputs = artifact_verification.get("actual_outputs")
            if not isinstance(raw_outputs, list) or not raw_outputs:
                raw_outputs = artifact_verification.get("expected_deliverables")
            if not isinstance(raw_outputs, list) or not raw_outputs:
                continue

            output_location = candidate.get("output_location")
            base_dir_value = (
                (output_location.get("base_dir") if isinstance(output_location, dict) else None)
                or candidate.get("task_directory_full")
                or candidate.get("run_directory")
                or candidate.get("working_directory")
            )
            base_dir = (
                Path(str(base_dir_value).strip()).expanduser()
                if str(base_dir_value or "").strip()
                else None
            )

            for raw in raw_outputs:
                label = str(raw or "").strip().replace("\\", "/")
                if not label:
                    continue
                path = Path(label).expanduser()
                if not path.is_absolute():
                    if base_dir is None:
                        continue
                    path = base_dir / path
                try:
                    resolved = path.resolve(strict=False)
                except Exception:
                    resolved = path
                normalized = "/" + str(resolved).replace("\\", "/").lstrip("/")
                if (
                    not normalized
                    or normalized in seen
                    or cls._is_internal_artifact_path(normalized)
                ):
                    continue
                seen.add(normalized)
                collected.append(normalized)
                if len(collected) >= 8:
                    return collected
    return collected


def _collect_task_scoped_output_refs_from_tool_results(
    cls: Any,
    tool_results: Sequence[Dict[str, Any]],
    *,
    task_context: Optional[TaskExecutionContext],
) -> List[str]:
    collected: List[str] = []
    seen: set[str] = set()
    for item in tool_results:
        payload = item.get("tool_result")
        for candidate in cls._iter_tool_payload_dicts(payload):
            for key in ("artifact_paths", "session_artifact_paths", "produced_files"):
                values = candidate.get(key)
                if not isinstance(values, list):
                    continue
                for raw in values:
                    ref = str(raw or "").strip()
                    if (
                        not ref
                        or ref in seen
                        or cls._is_internal_artifact_path(ref)
                        or not cls._is_task_scoped_output_ref(ref, task_context)
                    ):
                        continue
                    seen.add(ref)
                    collected.append(ref)
                    if len(collected) >= 8:
                        return collected
    return collected


def _collect_output_refs_from_tool_results(
    cls: Any,
    tool_results: Sequence[Dict[str, Any]],
) -> List[str]:
    collected: List[str] = []
    seen: set[str] = set()
    for item in tool_results:
        payload = item.get("tool_result")
        for candidate in cls._iter_tool_payload_dicts(payload):
            for key in ("artifact_paths", "session_artifact_paths", "produced_files"):
                values = candidate.get(key)
                if not isinstance(values, list):
                    continue
                for raw in values:
                    ref = str(raw or "").strip()
                    normalized = "/" + ref.replace("\\", "/").lstrip("/") if ref else ""
                    if (
                        not normalized
                        or normalized in seen
                        or cls._is_internal_artifact_path(normalized)
                    ):
                        continue
                    seen.add(normalized)
                    collected.append(normalized)
                    if len(collected) >= 8:
                        return collected
    return collected


def _is_task_scoped_output_ref(
    cls: Any,
    ref: str,
    task_context: Optional[TaskExecutionContext],
) -> bool:
    normalized = "/" + str(ref or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized == "/" or cls._is_internal_artifact_path(normalized):
        return False
    if not task_context or task_context.task_id is None:
        return True
    try:
        task_id = int(task_context.task_id)
    except (TypeError, ValueError):
        return True
    return bool(
        re.search(
            rf"/(?:results/)?plan\d+_task{task_id}(?:/|$)",
            normalized,
            re.IGNORECASE,
        )
    )


def _summarize_tool_payload_for_clue(cls: Any, payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("summary", "error", "message"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value

    nested = payload.get("result")
    if isinstance(nested, dict):
        nested_summary = cls._summarize_tool_payload_for_clue(nested)
        if nested_summary:
            return nested_summary

    return ""


def _looks_like_blocked_dependency_answer(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            "blocked_dependency",
            "blocked dependency",
            "missing upstream",
            "missing prerequisite",
            "cannot proceed",
            "依赖缺失",
            "前置条件不满足",
            "当前 task 不能继续",
            "阻塞",
        )
    )


def _looks_like_missing_task_definition_answer(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if "plan68_task" in lowered and any(
        token in lowered
        for token in ("未见到", "未看到", "没有", "missing", "not found", "not see", "not seen")
    ):
        return True
    if any(
        token in lowered
        for token in (
            "task definition",
            "task details",
            "task description",
            "corresponding directory",
            "任务定义",
            "任务描述",
            "具体内容",
            "详细描述",
            "对应的目录",
        )
    ):
        if "task" in lowered or "任务" in lowered:
            return True
    if ("please provide" in lowered or "需要您提供" in lowered or "请提供" in lowered) and (
        "task" in lowered or "任务" in lowered
    ):
        return True
    return False


def _should_reject_missing_task_definition_answer(
    agent: "DeepThinkAgent",
    text: str,
    *,
    task_context: Optional[TaskExecutionContext],
) -> bool:
    return (
        agent._is_execute_task_request()
        and agent._has_bound_task_context(task_context)
        and agent._explicit_task_override_active(task_context)
        and agent._looks_like_missing_task_definition_answer(text)
    )


def _is_valid_final_answer(agent: "DeepThinkAgent", text: str, *, user_query: str) -> bool:
    cleaned = sanitize_professional_response_text(str(text or "").strip())
    if len(cleaned) < 4:
        return False
    return not _dta().is_process_only_answer(cleaned, user_query=user_query)


def _should_retry_external_tool(agent: "DeepThinkAgent", tool_name: str, *, success: bool) -> bool:
    return (tool_name or "").strip().lower() in agent.EXTERNAL_RETRIABLE_TOOLS and not success


def _try_parse_json_object(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_outcomes_from_step(cls: Any, step: ThinkingStep) -> List[Dict[str, Any]]:
    outcomes: List[Dict[str, Any]] = []
    for entry in cls._extract_tool_payloads_from_step(step):
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        inner = cls._unwrap_tool_result(payload)
        success, error = cls._normalize_tool_callback_outcome(payload)
        outcomes.append(
            {
                "tool": entry.get("tool"),
                "success": success,
                "error": error or payload.get("error"),
                "summary": payload.get("summary") or inner.get("summary") or inner.get("message"),
            }
        )
    return outcomes


def _extract_tool_payloads_from_step(cls: Any, step: ThinkingStep) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    action_payload = cls._try_parse_json_object(step.action)
    tool_names = cls._tool_names_from_payload(action_payload)
    action_result_text = str(step.action_result or "").strip()
    if not tool_names or not action_result_text:
        return entries

    matched_any = False
    for block in [part.strip() for part in action_result_text.split("\n\n") if part.strip()]:
        match = _MULTI_TOOL_RESULT_LINE_RE.match(block)
        if not match:
            continue
        payload = cls._try_parse_json_object(match.group("payload"))
        if not payload:
            continue
        entries.append(
            {
                "tool": match.group("tool").strip(),
                "payload": payload,
            }
        )
        matched_any = True

    if matched_any:
        return entries

    payload = cls._try_parse_json_object(action_result_text)
    if payload:
        entries.append(
            {
                "tool": tool_names[0],
                "payload": payload,
            }
        )
        return entries

    if len(tool_names) == 1 and action_result_text.lower().startswith("error"):
        entries.append(
            {
                "tool": tool_names[0],
                "payload": {
                    "success": False,
                    "error": action_result_text,
                    "summary": action_result_text,
                },
            }
        )
    return entries


def _collect_tool_failures_from_steps(cls: Any, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for step in steps:
        for outcome in cls._extract_outcomes_from_step(step):
            if outcome.get("success") is False:
                failures.append(
                    {
                        "tool": str(outcome.get("tool") or "").strip(),
                        "error": str(outcome.get("error") or "").strip(),
                        "summary": str(outcome.get("summary") or "").strip(),
                        "iteration": step.iteration,
                    }
                )
    return failures


def _search_verified_from_steps(agent: "DeepThinkAgent", steps: List[ThinkingStep]) -> bool:
    seen_external = False
    successful_external = False
    for step in steps:
        for outcome in agent._extract_outcomes_from_step(step):
            tool_name = str(outcome.get("tool") or "").strip().lower()
            if tool_name not in agent.EXTERNAL_RETRIABLE_TOOLS:
                continue
            seen_external = True
            if outcome.get("success") is True:
                successful_external = True
    return True if not seen_external else successful_external


def _apply_external_search_notice(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    user_query: str,
    tool_failures: List[Dict[str, Any]],
    search_verified: bool,
) -> str:
    text = str(answer or "").strip()
    if not text or search_verified or not agent._is_research_or_execute():
        return text

    failed_external = [
        item for item in tool_failures
        if str(item.get("tool") or "").strip().lower() in agent.EXTERNAL_RETRIABLE_TOOLS
    ]
    if not failed_external:
        return text

    language = _dta().detect_reasoning_language(user_query or text)
    tool_names = ", ".join(
        sorted(
            {
                str(item.get("tool") or "").strip()
                for item in failed_external
                if str(item.get("tool") or "").strip()
            }
        )
    ) or "external search"
    notice = _dta()._localized_text(
        language,
        f"说明：本轮外部检索未成功完成（{tool_names} 失败或超时），以下内容基于当前会话上下文和已有稳定知识整理，未经过本轮在线检索验证。建议稍后重试检索，或手动补充 PubMed / 网页来源后再核对。",
        f"Note: External retrieval did not complete successfully in this run ({tool_names} failed or timed out). The response below is based on the current session context and stable prior knowledge, and was not verified by live search during this run. Consider retrying later or checking PubMed / web sources manually.",
    )
    normalized_notice = sanitize_professional_response_text(notice)
    if text.startswith(normalized_notice):
        return text
    return f"{normalized_notice}\n\n{text}"
