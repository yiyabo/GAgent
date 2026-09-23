"""Completion-claim detection, verified-execution finalization and
post-execution answers for the DeepThink agent (god-class split, behaviour
zero-change).

Each function here is the body of the like-named DeepThinkAgent method with
`self` renamed to `agent` (`cls` kept); the class keeps thin wrappers with
the same decorators. Display-family helpers (detect_reasoning_language,
_localized_text) stay in deep_think_agent and are reached through the
late-bound `_dta()` so their monkeypatch surface is unchanged. The
claim-text helpers below moved to the gating package with the only code
that referenced them; deep_think_agent re-imports them through the gating
facade so its module namespace (and the test import surface) is unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from app.services.deep_think.models import TaskExecutionContext, ThinkingStep

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_GLOBAL_SCOPE_WORD_RE = re.compile(r"\b(?:all|every|each)\b|全部|所有|每个", re.IGNORECASE)
_SUCCESS_WORD_RE = re.compile(
    r"\b(?:complete(?:d)?|success(?:ful|fully)?|succeeded|finished|done)\b|完成|成功|已完成|已成功",
    re.IGNORECASE,
)
_FAILURE_WORD_RE = re.compile(
    r"\b(?:fail(?:ed|ure|ures)?|incomplete|not\s+all|partial)\b|失败|不完整|未完成|并非全部|不是全部",
    re.IGNORECASE,
)
_NEGATED_GLOBAL_SUCCESS_RE = re.compile(
    r"\b(?:not|no)\s+(?:all|every|each)\b|并非(?:全部|所有|每个)|不是(?:全部|所有|每个)|并不是(?:全部|所有|每个)",
    re.IGNORECASE,
)


def _looks_like_completion_claim_text(reply_text: str) -> bool:
    lowered = str(reply_text or "").strip().lower()
    if not lowered:
        return False
    claim_tokens = (
        "completed",
        "all required files",
        "files have been created",
        "generated successfully",
        "已完成",
        "执行完毕",
        "已生成",
        "已导出",
        "准备就绪",
    )
    return any(token in lowered for token in claim_tokens)


def _looks_like_global_success_claim_text(reply_text: str) -> bool:
    text = str(reply_text or "").strip()
    if not text:
        return False
    if _NEGATED_GLOBAL_SUCCESS_RE.search(text):
        return False
    return bool(_GLOBAL_SCOPE_WORD_RE.search(text) and _SUCCESS_WORD_RE.search(text))


def _answer_acknowledges_failed_status_counts(
    reply_text: str,
    signals: Sequence[Dict[str, Any]],
) -> bool:
    text = str(reply_text or "").strip()
    if not text or not _FAILURE_WORD_RE.search(text):
        return False
    failed_counts: set[int] = set()
    for signal in signals:
        status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
        failed = status_counts.get("failed")
        if isinstance(failed, int) and failed > 0:
            failed_counts.add(failed)
        sources = signal.get("status_count_sources") if isinstance(signal.get("status_count_sources"), list) else []
        for source in sources:
            if not isinstance(source, dict) or source.get("kind") != "failure":
                continue
            entry_count = source.get("entry_count")
            if isinstance(entry_count, int) and entry_count > 0:
                failed_counts.add(entry_count)
    return bool(failed_counts and any(str(count) in text for count in failed_counts))


def _build_verified_execution_finalize_nudge(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    task_bits: List[str] = []
    if task_context and task_context.task_id is not None:
        task_bits.append(f"Task ID={task_context.task_id}")
    if task_context and task_context.task_name:
        task_bits.append(f"Task Name={task_context.task_name}")
    task_hint = "\n".join(task_bits)
    zh = (
        "当前绑定任务已经执行并验证通过，且当前显式任务链没有剩余待执行任务。\n"
        "下一步不要再调用 `file_operations`、`document_reader`、`plan_operation`、`web_search` 或再次执行代码。\n"
        "如果需要一句总结，可调用 `result_interpreter` 读取现有结果；否则请直接 `submit_final_answer`，总结最终状态、关键输出文件和结论。"
    )
    en = (
        "The current bound task has already executed and passed verification, and there are no remaining tasks in the explicit task chain.\n"
        "Do not call `file_operations`, `document_reader`, `plan_operation`, `web_search`, or run code again.\n"
        "If one short synthesis step is still useful, call `result_interpreter` on the existing outputs; otherwise call `submit_final_answer` now with the final status, key output files, and conclusions."
    )
    base = _dta()._localized_text(language, zh, en)
    if task_hint:
        return f"{base}\n{task_hint}"
    return base


def _should_force_verified_execution_finalization(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    tool_results: Sequence[Dict[str, Any]],
    had_real_execution_tool: bool = False,
) -> bool:
    if not agent._is_execute_task_request() or not agent._has_bound_task_context(task_context):
        return False
    request_task_id = agent._coerce_positive_int(agent.request_profile.get("current_task_id"))
    context_task_id = (
        agent._coerce_positive_int(task_context.task_id)
        if task_context and task_context.task_id is not None
        else None
    )
    if (
        request_task_id is not None
        and context_task_id is not None
        and request_task_id != context_task_id
        and agent._explicit_task_override_active(task_context)
    ):
        return False
    if agent._pending_scope_task_ids():
        return False
    if agent._tool_results_indicate_verified_success(tool_results):
        if (
            not had_real_execution_tool
            and agent._is_verification_only_tool_result_cycle(tool_results)
        ):
            return False
        return True
    if agent._collect_task_scoped_output_refs_from_tool_results(
        tool_results,
        task_context=task_context,
    ):
        return True
    if (
        bool(agent.request_profile.get("explicit_task_override"))
        and any(agent._tool_counts_as_real_execution(item) for item in tool_results)
    ):
        return bool(agent._collect_output_refs_from_tool_results(tool_results))
    return False


def _build_post_execution_probe_stop_answer(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    steps: Sequence[ThinkingStep],
    tool_results: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    observed_outputs: List[str] = []
    seen: set[str] = set()
    for step in reversed(list(steps)):
        for evidence in reversed(step.evidence or []):
            evidence_type = str((evidence or {}).get("type") or "").strip().lower()
            if evidence_type != "file":
                continue
            ref = str((evidence or {}).get("ref") or "").strip()
            if (
                not ref
                or ref in seen
                or not agent._is_task_scoped_output_ref(ref, task_context)
            ):
                continue
            seen.add(ref)
            observed_outputs.append(ref)
            if len(observed_outputs) >= 6:
                break
        if len(observed_outputs) >= 6:
            break

    for ref in agent._collect_task_scoped_output_refs_from_tool_results(
        tool_results or [],
        task_context=task_context,
    ):
        if ref in seen:
            continue
        seen.add(ref)
        observed_outputs.append(ref)
        if len(observed_outputs) >= 6:
            break

    task_label = ""
    if task_context and task_context.task_id is not None:
        task_label = f"Task {task_context.task_id}"
        if task_context.task_name:
            task_label += f" ({task_context.task_name})"
    elif task_context and task_context.task_name:
        task_label = task_context.task_name

    verified_success = agent._tool_results_indicate_verified_success(tool_results or [])
    if verified_success and not observed_outputs:
        for ref in agent._collect_verified_output_refs_from_tool_results(tool_results or []):
            if ref in seen:
                continue
            seen.add(ref)
            observed_outputs.append(ref)
            if len(observed_outputs) >= 6:
                break

    if language == "zh":
        lines = []
        if verified_success:
            if task_label:
                lines.append(f"{task_label} 的代码执行与验证实际上已完成，但模型在收尾总结阶段陷入重复只读观察，已自动停止继续探查。")
            else:
                lines.append("任务代码执行与验证实际上已完成，但模型在收尾总结阶段陷入重复只读观察，已自动停止继续探查。")
        elif task_label:
            lines.append(f"{task_label} 的代码执行已完成，但后续验证陷入重复观察循环，已自动停止继续探查。")
        else:
            lines.append("任务代码执行已完成，但后续验证陷入重复观察循环，已自动停止继续探查。")
        if observed_outputs:
            lines.append("已确认的稳定输出包括：" if verified_success else "最近已观察到的输出包括：")
            lines.extend(f"- {item}" for item in observed_outputs)
        elif task_label:
            lines.append("未在当前 task 作用域内观察到稳定输出文件。")
        if verified_success:
            lines.append("可直接基于这些结果继续后续任务，或重新发起一次“仅总结结果”的请求。")
        else:
            lines.append("请基于这些已有输出继续查看结果，或重新发起一次“仅总结结果”的请求。")
        return "\n".join(lines)

    lines = []
    if verified_success:
        if task_label:
            lines.append(f"{task_label} actually finished execution and verification, but the model got stuck in repeated read-only post-execution summarization and was stopped automatically.")
        else:
            lines.append("Task execution and verification actually completed, but the model got stuck in repeated read-only post-execution summarization and was stopped automatically.")
    elif task_label:
        lines.append(f"{task_label} finished execution, but post-execution verification entered a repeated observation loop and was stopped automatically.")
    else:
        lines.append("Task code finished execution, but post-execution verification entered a repeated observation loop and was stopped automatically.")
    if observed_outputs:
        lines.append("Confirmed stable outputs include:" if verified_success else "Recently observed outputs include:")
        lines.extend(f"- {item}" for item in observed_outputs)
    elif task_label:
        lines.append("No stable outputs were observed inside the current task scope.")
    if verified_success:
        lines.append("Use these outputs directly for the next task, or retry with a summary-only follow-up.")
    else:
        lines.append("Use these outputs directly, or retry with a summary-only follow-up.")
    return "\n".join(lines)


def _build_blocked_dependency_answer(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    tool_results: List[Dict[str, Any]],
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    task_label = ""
    if task_context:
        if task_context.task_name and task_context.task_id is not None:
            task_label = f"Task {task_context.task_id} ({task_context.task_name})"
        elif task_context.task_name:
            task_label = task_context.task_name
        elif task_context.task_id is not None:
            task_label = f"Task {task_context.task_id}"

    clues: List[str] = []
    for item in tool_results:
        clue = agent._extract_blocked_dependency_clue(item)
        if clue:
            clues.append(clue)
        if len(clues) >= 2:
            break
    clue_text = "\n".join(f"- {agent._clip_reference_text(clue, limit=220)}" for clue in clues[:2])

    zh_header = "BLOCKED_DEPENDENCY: 当前绑定任务缺少继续执行所需的上游交付物或关键输入。"
    en_header = "BLOCKED_DEPENDENCY: The bound task is missing required upstream deliverables or key inputs."
    if task_label:
        zh_header = f"BLOCKED_DEPENDENCY: {task_label} 缺少继续执行所需的上游交付物或关键输入。"
        en_header = f"BLOCKED_DEPENDENCY: {task_label} is missing required upstream deliverables or key inputs."

    zh = (
        f"{zh_header}\n"
        "我已经停止继续做只读探查，因为继续浏览目录/文档不会推进这个 task。\n"
        "按照当前执行边界，本轮不会把它自动改写成前序任务或全量预处理。请先补齐依赖产物，或在任务说明里明确授权补跑前置步骤后再继续。"
    )
    en = (
        f"{en_header}\n"
        "I stopped the repeated read-only probing because more directory or document browsing will not advance this task.\n"
        "Under the current execution boundary, this run will not silently rewrite the task into an upstream preprocessing step. Provide the missing prerequisite outputs, or explicitly authorize backfilling the prerequisite work in the task instruction before retrying."
    )
    base = _dta()._localized_text(language, zh, en)
    if clue_text:
        return f"{base}\nObserved clues:\n{clue_text}"
    return base


def _extract_blocked_dependency_clue(cls: Any, item: Dict[str, Any]) -> str:
    payload = item.get("tool_result")
    clue = cls._summarize_tool_payload_for_clue(payload)
    if clue:
        return clue

    result_text = str(item.get("tool_result_text") or "").strip()
    if not result_text:
        return ""
    try:
        parsed = json.loads(result_text)
    except Exception:
        parsed = None
    clue = cls._summarize_tool_payload_for_clue(parsed)
    if clue:
        return clue

    stripped = result_text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return ""
    return result_text
