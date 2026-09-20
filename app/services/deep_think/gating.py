"""Execution gating, follow-through nudges, truth barriers and plan-contract
checks for the DeepThink agent (god-class split, behaviour zero-change).

Each function here is the body of the like-named DeepThinkAgent method with
`self` renamed to `agent` (`cls` kept); the class keeps thin wrappers with
the same decorators. Display-family helpers (detect_reasoning_language,
_localized_text, is_process_only_answer) stay in deep_think_agent and are
reached through the late-bound `_dta()` so their monkeypatch surface is
unchanged. The claim-text module helpers (_looks_like_completion_claim_text,
_looks_like_global_success_claim_text, _answer_acknowledges_failed_status_counts)
and the regex/constants block below moved here with the only code that
referenced them; deep_think_agent re-imports the three helpers so its module
namespace (and the test import surface) is unchanged.

Sanctioned deviation from verbatim: _extract_recommended_tool_from_instruction
reads the class attribute _FOLLOWTHROUGH_TOOL_CANDIDATES through
_dta().DeepThinkAgent because the attribute stays on DeepThinkAgent and a
runtime import of the class here would be circular; the lookup still happens
at call time on the same class object, so behaviour is identical.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
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
_DIRECTORY_DATASET_REQUEST_RE = re.compile(
    r"\b(?:analy[sz]e|inspect|audit|summari[sz]e|review|look|profile|census|folder|directory|dataset|data\s+folder|output\s+folder)\b|分析|查看|检查|审计|总结|目录|文件夹|数据集",
    re.IGNORECASE,
)
_PHAGESCOPE_DATASET_REQUEST_RE = re.compile(
    r"\b(?:phagescope|phage[-\s]?host|host\s+prediction|data\s+splitting|model\s+selection|benchmarking|biological\s+validation)\b|噬菌体|宿主预测|数据划分|模型选择|生物学验证",
    re.IGNORECASE,
)
_PHAGESCOPE_ANALYSIS_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|inspect|audit|summari[sz]e|review|look|profile|explore|start|dataset|data)\b|分析|查看|看看|检查|审计|总结|探索|数据集|数据",
    re.IGNORECASE,
)
# Generic spreadsheet/tabular files (Excel/CSV/Parquet) under a "phagescope"
# directory are NOT PhageScope datasets. ``.tsv``/``.txt`` are deliberately
# excluded here because those ARE real PhageScope metadata formats.
_NON_PHAGESCOPE_TABULAR_FILE_EXTS = (
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
    ".parquet",
    ".feather",
)
# Match real absolute filesystem paths without treating the slash inside
# relative output paths such as ``results/figures/目录`` as ``/figures/目录``.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s`\"'<>，。；;])+")
_UNVERIFIED_SAMPLE_DIRECTORY_CLAIM_RE = re.compile(
    r"(?:total\s+sample\s+director(?:y|ies)|sample\s+director(?:y|ies)\s*[:|]\s*[\d,]+|样本目录\s*[:：|]?\s*[\d,]+)",
    re.IGNORECASE,
)
_UNVERIFIED_EACH_FILE_STRUCTURE_RE = re.compile(
    r"(?:each|every|all)\s+(?:completed\s+)?(?:sample|sample\s+directory|sample\s+folder)[^\n.]{0,120}\b(?:contain|contains|has|have)\b|每个(?:已完成)?样本[^\n。]{0,80}(?:包含|含有|都有)",
    re.IGNORECASE,
)
_UNSUPPORTED_RETRY_RERUN_RE = re.compile(
    r"\b(?:retr(?:y|ied|ies)|rerun|re-run|multiple\s+pipeline\s+runs|logged\s+across\s+multiple)\b|重试|重新运行|多次运行|多轮运行",
    re.IGNORECASE,
)
_MULTI_TOOL_RESULT_LINE_RE = re.compile(
    r"^\[(?P<tool>[^\]]+)\]\s+(?P<payload>\{.*\})$",
    re.DOTALL,
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


def _detect_partial_completion_in_tool_results(
    tool_results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Scan tool_results for a code_executor call that flagged partial completion.

    Returns a dict with failure context when partial completion is detected;
    otherwise ``None``.
    """
    for item in tool_results:
        if item.get("tool_name") != "code_executor":
            continue
        raw_text = item.get("tool_result_text") or ""
        try:
            payload = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        except (JSONDecodeError, TypeError):
            continue
        inner = payload
        if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
            inner = payload["result"]
        if not isinstance(inner, dict):
            continue
        if inner.get("partial_completion_suspected"):
            stderr = str(inner.get("stderr") or "")
            error_summary = str(inner.get("error_summary") or "")
            contract_error_summary = str(inner.get("contract_error_summary") or "")
            contract_diff = inner.get("contract_diff")
            missing_outputs: List[str] = []
            if isinstance(contract_diff, dict):
                missing_outputs = list(contract_diff.get("missing_required_outputs") or [])
            verification = inner.get("verification")
            verification_failures: List[str] = []
            if isinstance(verification, dict):
                for f in verification.get("failures", []):
                    if isinstance(f, dict):
                        msg = str(f.get("message") or f.get("type") or "")
                        if msg:
                            verification_failures.append(msg[:200])
            error_snippet = stderr[:500] if stderr else ""
            if error_summary:
                error_snippet = error_summary + ("\n" + error_snippet if error_snippet else "")
            if contract_error_summary and contract_error_summary not in error_snippet:
                error_snippet = contract_error_summary + ("\n" + error_snippet if error_snippet else "")
            return {
                "partial_ratio": inner.get("partial_ratio"),
                "produced_files": inner.get("produced_files", []),
                "task_directory_full": inner.get("task_directory_full", ""),
                "error_snippet": error_snippet[:800],
                "missing_outputs": missing_outputs[:10],
                "verification_failures": verification_failures[:5],
                "failure_kind": str(inner.get("failure_kind") or ""),
                "exit_code": inner.get("exit_code"),
            }
    return None


def _build_partial_completion_retry_nudge(
    agent: "DeepThinkAgent",
    partial_info: Dict[str, Any],
    *,
    task_context: Optional["TaskExecutionContext"],
    user_query: str,
    retry_count: int,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    ratio = partial_info.get("partial_ratio", "?/?")
    produced = partial_info.get("produced_files", [])
    task_dir = partial_info.get("task_directory_full", "")
    error_snippet = str(partial_info.get("error_snippet") or "")
    missing_outputs = partial_info.get("missing_outputs", [])
    verification_failures = partial_info.get("verification_failures", [])
    failure_kind = str(partial_info.get("failure_kind") or "")

    produced_hint = ""
    if produced:
        names = [p.rsplit("/", 1)[-1] if "/" in p else p for p in produced[:15]]
        produced_hint = ", ".join(names)
        if len(produced) > 15:
            produced_hint += f" … ({len(produced)} total)"

    missing_hint = ""
    if missing_outputs:
        missing_hint = ", ".join(missing_outputs[:10])
    elif verification_failures:
        missing_hint = "; ".join(verification_failures[:3])

    task_bits: List[str] = []
    if task_context:
        if task_context.task_id is not None:
            task_bits.append(f"Task ID={task_context.task_id}")
        if task_context.task_name:
            task_bits.append(f"Task Name={task_context.task_name}")
    task_label = " | ".join(task_bits) if task_bits else ""

    error_block = ""
    if error_snippet:
        error_block = f"\n🔴 **上次错误信息**:\n```\n{error_snippet}\n```"
    if missing_hint:
        error_block += f"\n❌ **缺失产出**: {missing_hint}"

    if retry_count >= 3:
        zh = (
            f"⚠️ 这是第 {retry_count} 次重试，之前的方案持续失败。\n"
            f"{error_block}\n"
            f"请**停止重试相同的方法**。要么：\n"
            f"1. 用完全不同的技术路线完成任务（换库、换算法、简化流程）\n"
            f"2. 如果任务确实无法完成，提交最终答案，说明失败原因和已尝试的方法，标记任务为失败。\n"
        )
        en = (
            f"⚠️ Retry #{retry_count}. Previous approaches keep failing.\n"
            f"{error_block}\n"
            f"**Stop retrying the same approach.** Either:\n"
            f"1. Use a fundamentally different technique (different library, algorithm, or simplified pipeline).\n"
            f"2. If the task is genuinely infeasible, submit a final answer explaining what was attempted, "
            f"why it failed, and mark the task as failed.\n"
        )
    elif retry_count >= 2:
        zh = (
            f"⚠️ 同样的错误第 2 次出现。上次代码执行只完成了 {ratio} 项。\n"
            f"{error_block}\n"
            f"请**先分析错误原因**，然后**尝试不同的方法**（不要重复相同的代码）。\n"
            f"已产出文件: {produced_hint or '无'}\n"
            f"如果当前方法行不通，考虑替代方案。\n"
        )
        en = (
            f"⚠️ Same error appeared twice. Last run only completed {ratio} items.\n"
            f"{error_block}\n"
            f"**Analyze the error first**, then **try a different approach** (do NOT repeat the same code).\n"
            f"Files produced so far: {produced_hint or 'none'}\n"
            f"Consider alternative techniques if the current one is not working.\n"
        )
    else:
        zh = (
            f"⚠️ 上次 code_executor 执行检测到**部分完成**：仅处理了 {ratio} 项。\n"
            f"{error_block}\n"
            f"已产出文件: {produced_hint or '无'}\n"
            f"请按以下步骤操作：\n"
            f"1. 先**诊断失败原因**（查看上面的错误信息），理解为什么部分产出缺失。\n"
            f"2. 修复根因后调用 `code_executor`，仅处理尚未完成的剩余项。\n"
            f"3. 新结果追加到 results/ 目录，不要覆盖已有文件。\n"
        )
        en = (
            f"⚠️ Previous code_executor run: **partial completion** ({ratio} items).\n"
            f"{error_block}\n"
            f"Files produced: {produced_hint or 'none'}\n"
            f"Steps:\n"
            f"1. **Diagnose the failure** (see error above) — understand WHY some outputs are missing.\n"
            f"2. Fix the root cause, then call `code_executor` for remaining items only.\n"
            f"3. Append new results to results/ — do NOT overwrite existing files.\n"
        )
    base = _dta()._localized_text(language, zh, en)
    if task_dir:
        base += f"\nWork directory: {task_dir}"
    if task_label:
        base += f"\n{task_label}"
    return base


def _is_probe_only_execution_cycle(
    agent: "DeepThinkAgent",
    tool_results: List[Dict[str, Any]],
    *,
    task_context: Optional[TaskExecutionContext],
) -> bool:
    if not tool_results:
        return False
    if not agent._is_execute_task_request() or not agent._has_bound_task_context(task_context):
        return False
    return all(agent._is_observation_only_tool_call(item) for item in tool_results)


def _is_verification_only_tool_result_cycle(
    tool_results: Sequence[Dict[str, Any]],
) -> bool:
    if not tool_results:
        return False
    return all(
        str(item.get("tool_name") or "").strip().lower() == "verify_task"
        for item in tool_results
    )


def _verification_only_cycle_replacement_task_id(
    agent: "DeepThinkAgent",
    executable_calls: Sequence[Any],
    *,
    task_context: Optional[TaskExecutionContext],
    had_real_execution_tool: bool,
) -> Optional[int]:
    if had_real_execution_tool or not executable_calls:
        return None
    if not agent._is_execute_task_request() or not agent._has_bound_task_context(task_context):
        return None
    if not all(
        str(getattr(call, "name", "") or "").strip().lower() == "verify_task"
        for call in executable_calls
    ):
        return None
    return agent._current_bound_task_id(task_context)


def _build_probe_only_followthrough_nudge(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    stage: int = 1,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    task_bits: List[str] = []
    if task_context:
        if task_context.task_id is not None:
            task_bits.append(f"Task ID={task_context.task_id}")
        if task_context.task_name:
            task_bits.append(f"Task Name={task_context.task_name}")
        if task_context.task_instruction:
            task_bits.append(
                f"Task Instruction={agent._clip_reference_text(task_context.task_instruction, limit=400)}"
            )
    task_hint = "\n".join(task_bits)
    is_explicit_override = agent._explicit_task_override_active(task_context)
    if stage >= 2:
        if is_explicit_override:
            # For explicit task override requests, do NOT suggest BLOCKED_DEPENDENCY.
            # The user explicitly asked for this task — execute it, incorporating
            # prerequisite work if needed.
            zh = (
                "这是连续第 2 次只做观察型探查而没有真正执行任务。\n"
                "用户已明确要求执行这个任务，不允许返回 BLOCKED_DEPENDENCY。\n"
                "下一步必须立即调用真正的执行工具（如 `code_executor` 或任务对应的分析工具）。\n"
                "如果上游数据或前置产物不存在，请在本次执行中一并生成或补齐所需的前置数据。\n"
                "不要继续做目录清点、文档浏览或结果浏览式探查。"
            )
            en = (
                "This is the second consecutive observation-only probe without actually executing the bound task.\n"
                "The user explicitly requested this task execution — you MUST NOT return BLOCKED_DEPENDENCY.\n"
                "Your next step MUST call a real execution tool such as `code_executor` or the task-specific analysis tool immediately.\n"
                "If upstream data or prerequisite outputs are not available, incorporate the prerequisite processing steps within this execution.\n"
                "Do not continue with directory inventory, document browsing, or result-browsing probes."
            )
        else:
            zh = (
                "这是连续第 2 次只做观察型探查而没有真正执行任务。\n"
                "下一步必须二选一：\n"
                "1. 立即调用真正的执行工具（如 `code_executor` 或任务对应的分析工具）；\n"
                "2. 若缺少上游交付物或关键输入，直接给出 `BLOCKED_DEPENDENCY` 结论，明确说明缺什么、为什么当前 task 不能继续。\n"
                "不要继续做目录清点、文档浏览或结果浏览式探查。\n"
                "不要把当前 task 偷偷改写成前序任务或全量预处理，除非 task 指令明确授权补跑前置步骤。"
            )
            en = (
                "This is the second consecutive observation-only probe without actually executing the bound task.\n"
                "Your next step MUST do exactly one of the following:\n"
                "1. Call a real execution tool such as `code_executor` or the task-specific analysis tool immediately.\n"
                "2. If required upstream deliverables or key inputs are missing, return a `BLOCKED_DEPENDENCY` conclusion that states what is missing and why the current task cannot proceed.\n"
                "Do not continue with directory inventory, document browsing, or result-browsing probes.\n"
                "Do not silently rewrite the current task into an upstream preprocessing task unless the task instruction explicitly authorizes backfilling the prerequisite work."
            )
    else:
        zh = (
            "这是一个已绑定任务的执行请求。你刚才只做了只读目录/文件探查。\n"
            "下一步不要继续做目录清点式 `file_operations` 或只读文档探查。\n"
            "请直接推进任务执行：优先调用真正的执行工具（如 `code_executor` 或该任务对应的分析工具）。\n"
            "如果确实还缺一个关键文件，最多再读取一个直接相关的具体文件，然后立刻执行。\n"
            "不要以“继续探查目录”“需要先看看结构”为理由结束本轮。"
        )
        en = (
            "This is a bound task execution request and you only performed observation-only probing.\n"
            "Do not continue with directory-inventory style `file_operations` checks or read-only document probing on the next step.\n"
            "Advance the task now by calling a real execution tool such as `code_executor` or the task-specific analysis tool.\n"
            "If exactly one critical file still must be inspected, read that specific file once and then execute immediately.\n"
            "Do not end this run with another directory-probing report."
        )
    base = _dta()._localized_text(language, zh, en)
    if task_hint:
        return f"{base}\n{task_hint}"
    return base


def _task_context_upstream_artifact_paths(
    agent: "DeepThinkAgent",
    task_context: Optional[TaskExecutionContext],
) -> List[str]:
    if task_context is None:
        return []
    collected: List[str] = []
    seen: set[str] = set()
    for dep in list(getattr(task_context, "dependency_outputs", None) or [])[:6]:
        if not isinstance(dep, dict):
            continue
        raw_paths = []
        for key in ("artifact_paths", "output_directories"):
            values = dep.get(key)
            if isinstance(values, list):
                raw_paths.extend(values)
        for raw in raw_paths:
            path = str(raw or "").strip()
            if not path or path in seen or agent._is_internal_artifact_path(path):
                continue
            seen.add(path)
            collected.append(path)
            if len(collected) >= 8:
                return collected
    return collected


def _can_force_probe_followthrough_execution(
    agent: "DeepThinkAgent",
    task_context: Optional[TaskExecutionContext],
) -> bool:
    if not (
        agent._is_execute_task_request()
        and agent._has_bound_task_context(task_context)
        and agent._explicit_task_override_active(task_context)
        and "code_executor" in agent.available_tools
    ):
        return False
    # When upstream artifact paths are available, always allow forced
    # execution.  When they are NOT available but the user explicitly
    # requested this task (explicit_task_override), still allow forced
    # execution — the task instruction alone provides enough context, and
    # the code_executor should incorporate any prerequisite work itself
    # rather than reporting BLOCKED_DEPENDENCY to the user.
    return True


def _build_forced_probe_followthrough_task(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    tool_name: str = "code_executor",
) -> str:
    artifact_paths = agent._task_context_upstream_artifact_paths(task_context)
    task_label = "the current bound task"
    task_instruction = str(user_query or "").strip()
    if task_context is not None:
        if task_context.task_id is not None:
            task_label = f"Task {task_context.task_id}"
        if task_context.task_name:
            task_label = f"{task_label} ({task_context.task_name})"
        if task_context.task_instruction:
            task_instruction = str(task_context.task_instruction).strip()

    lines = [
        task_instruction or str(user_query or "").strip() or "Execute the current bound task.",
        "",
        "Execution followthrough requirement:",
        f"- Continue {task_label} now with real execution via {tool_name}.",
    ]
    if artifact_paths:
        lines.extend(
            [
                "- Authoritative upstream deliverables are already available; do not stop with BLOCKED_DEPENDENCY before attempting execution.",
                "- Use the upstream artifact paths/directories below directly instead of browsing more directories or documents.",
                "- Produce only the current task's outputs.",
                "",
                "Authoritative upstream artifact paths/directories:",
                *[f"- {path}" for path in artifact_paths],
            ]
        )
    else:
        lines.extend(
            [
                "- Do NOT report BLOCKED_DEPENDENCY. The user explicitly requested this task execution.",
                "- If upstream data or prerequisite outputs are not directly available, incorporate the necessary preprocessing steps within this execution.",
                "- Use the task instruction above and any available session context to produce the required outputs.",
            ]
        )
    return "\n".join(lines).strip()


def _extract_recommended_tool_from_instruction(instruction: str) -> Optional[str]:
    """Extract a tool name mentioned in the task instruction.

    If the instruction explicitly says "使用 literature_pipeline" or
    "use web_search", return that tool name so the forced followthrough
    uses the right tool instead of defaulting to code_executor.
    """
    if not instruction:
        return None
    lowered = instruction.lower()
    # Check each candidate tool — return the first one mentioned
    for tool in _dta().DeepThinkAgent._FOLLOWTHROUGH_TOOL_CANDIDATES:
        if tool in lowered:
            return tool
    # Also check Chinese tool references
    _TOOL_ALIASES = {
        "文献检索": "literature_pipeline",
        "文献搜索": "literature_pipeline",
        "搜索文献": "web_search",
        "网络搜索": "web_search",
        "序列获取": "sequence_fetch",
        "下载链接": "url_fetch",
        "下载文件": "url_fetch",
        "生物工具": "bio_tools",
    }
    for alias, tool in _TOOL_ALIASES.items():
        if alias in instruction:
            return tool
    return None


async def _execute_forced_probe_followthrough(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    iteration: int,
    probe_only_execution_cycles: int,
) -> Dict[str, Any]:
    # Determine the best tool based on task instruction
    task_instruction = ""
    if task_context is not None and task_context.task_instruction:
        task_instruction = str(task_context.task_instruction).strip()

    recommended_tool = agent._extract_recommended_tool_from_instruction(task_instruction)
    tool_name = recommended_tool or "code_executor"

    forced_task = agent._build_forced_probe_followthrough_task(
        task_context=task_context,
        user_query=user_query,
        tool_name=tool_name,
    )
    logger.warning(
        "[DEEP_THINK_NATIVE] Forcing %s after probe-only cycles=%s task_id=%s (recommended=%s)",
        tool_name,
        probe_only_execution_cycles,
        getattr(task_context, "task_id", None),
        recommended_tool or "default",
    )

    # Build tool-specific arguments
    if tool_name == "code_executor":
        arguments = {"task": forced_task}
    elif tool_name == "literature_pipeline":
        arguments = {"query": task_instruction[:500], "session_id": getattr(agent, "_session_id", None)}
    elif tool_name == "web_search":
        arguments = {"query": task_instruction[:200]}
    elif tool_name == "manuscript_writer":
        arguments = {"task": forced_task}
    else:
        # Generic: pass the task description
        arguments = {"task": forced_task}

    forced_call = SimpleNamespace(
        name=tool_name,
        id=f"forced_probe_followthrough_{iteration}_{probe_only_execution_cycles}",
        arguments=arguments,
    )
    return await agent._execute_native_tool_call(
        tc=forced_call,
        iteration=iteration,
        index=9999,
    )


def _build_post_execution_summary_nudge(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    stage: int = 1,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    task_bits: List[str] = []
    if task_context:
        if task_context.task_id is not None:
            task_bits.append(f"Task ID={task_context.task_id}")
        if task_context.task_name:
            task_bits.append(f"Task Name={task_context.task_name}")
    task_hint = "\n".join(task_bits)
    can_interpret = any(
        str(tool).strip().lower() == "result_interpreter" for tool in agent.available_tools
    )
    if stage >= 2:
        zh = (
            "任务代码已经执行过，当前进入了连续第 2 次只读观察。\n"
            "下一步不要再调用 `file_operations`、`document_reader` 或 `plan_operation`。\n"
            "请立即二选一：\n"
            "1. 基于现有结果直接 `submit_final_answer`，总结关键输出、主要文件和结论；\n"
            f"2. {'调用 `result_interpreter` 解释已有结果后立刻提交最终答案；' if can_interpret else '如果确实还缺一条结论，就基于现有证据直接给出最终答案；'}\n"
            "不要继续做目录浏览、文件点读或计划编辑。"
        )
        en = (
            "Task code has already executed and this is the second consecutive read-only post-execution probe.\n"
            "Do not call `file_operations`, `document_reader`, or `plan_operation` again.\n"
            "Your next step must do exactly one of the following:\n"
            "1. Call `submit_final_answer` now with the key outputs, major files, and conclusions.\n"
            f"2. {'Call `result_interpreter` on the existing outputs and then immediately submit the final answer.' if can_interpret else 'Use the evidence already gathered and submit the final answer now.'}\n"
            "Do not continue browsing directories, peeking at files, or editing the plan."
        )
    else:
        zh = (
            "任务代码已经执行过。当前这一步只是只读观察，不能再作为继续探索的理由。\n"
            "下一步请停止目录/文件浏览，直接收尾："
            f"{'优先调用 `result_interpreter` 解释已有结果，然后立刻 `submit_final_answer`。' if can_interpret else '直接 `submit_final_answer`，总结主要输出和结论。'}\n"
            "不要再调用 `plan_operation`。"
        )
        en = (
            "Task code has already executed. This step is only read-only observation and should not start another exploration loop.\n"
            f"On the next step, stop browsing files and wrap up directly: {'use `result_interpreter` on the existing outputs, then immediately `submit_final_answer`.' if can_interpret else 'call `submit_final_answer` now with the main outputs and conclusions.'}\n"
            "Do not call `plan_operation` again."
        )
    base = _dta()._localized_text(language, zh, en)
    if task_hint:
        return f"{base}\n{task_hint}"
    return base


def _build_task_handoff_execution_nudge(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    previous_task_id: int,
    next_task_id: int,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    task_bits: List[str] = [f"Task ID={next_task_id}"]
    if task_context and task_context.task_name:
        task_bits.append(f"Task Name={task_context.task_name}")
    if task_context and task_context.task_instruction:
        task_bits.append(
            f"Task Instruction={agent._clip_reference_text(task_context.task_instruction, limit=400)}"
        )
    task_hint = "\n".join(task_bits)
    zh = (
        f"Task {previous_task_id} 已经执行完成，当前绑定任务已自动推进到 Task {next_task_id}。\n"
        "下一步不要继续总结上一个任务，也不要浏览旧目录或旧结果。\n"
        "请立即执行当前绑定任务：优先调用真正的执行工具（如 `code_executor` 或该任务对应的分析工具）。\n"
        "可以使用上一个任务已经生成的现有产物，但不要把请求回退成前一个任务的总结或 BLOCKED_DEPENDENCY。"
    )
    en = (
        f"Task {previous_task_id} has finished and the bound task has automatically advanced to Task {next_task_id}.\n"
        "Do not keep summarizing the previous task or browsing its old directories/results.\n"
        "Execute the current bound task immediately using a real execution tool such as `code_executor` or the task-specific analysis tool.\n"
        "You may use outputs already produced by the previous task, but do not fall back to a summary or BLOCKED_DEPENDENCY for the previous task."
    )
    base = _dta()._localized_text(language, zh, en)
    if task_hint:
        return f"{base}\n{task_hint}"
    return base


def _can_force_handoff_followthrough_execution(
    agent: "DeepThinkAgent",
    task_context: Optional[TaskExecutionContext],
    *,
    next_task_id: Optional[int],
) -> bool:
    current_task_id = agent._current_bound_task_id(task_context)
    return (
        agent._is_execute_task_request()
        and agent._has_bound_task_context(task_context)
        and agent._explicit_task_override_active(task_context)
        and "code_executor" in agent.available_tools
        and next_task_id is not None
        and current_task_id == agent._coerce_positive_int(next_task_id)
    )


def _build_forced_handoff_followthrough_task(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    previous_task_id: int,
    next_task_id: int,
) -> str:
    artifact_paths = agent._task_context_upstream_artifact_paths(task_context)
    task_label = f"Task {next_task_id}"
    task_instruction = str(user_query or "").strip()
    if task_context is not None:
        if task_context.task_name:
            task_label = f"{task_label} ({task_context.task_name})"
        if task_context.task_instruction:
            task_instruction = str(task_context.task_instruction).strip()

    lines = [
        task_instruction or str(user_query or "").strip() or "Execute the current bound task.",
        "",
        "Task handoff followthrough requirement:",
        f"- Task {previous_task_id} is already completed. Continue {task_label} now with real execution via code_executor.",
        "- The current bound task is already known from plan context; do not ask for task definitions and do not infer blocking from a missing parent task directory name.",
        "- Reuse authoritative upstream outputs directly when relevant instead of falling back to a prose status summary.",
        "- Produce only the current bound task's outputs.",
    ]
    if artifact_paths:
        lines.extend(
            [
                "",
                "Authoritative upstream artifact paths:",
                *[f"- {path}" for path in artifact_paths],
            ]
        )
    return "\n".join(lines).strip()


async def _execute_forced_handoff_followthrough(
    agent: "DeepThinkAgent",
    *,
    task_context: Optional[TaskExecutionContext],
    user_query: str,
    iteration: int,
    previous_task_id: int,
    next_task_id: int,
    reason: str,
) -> Dict[str, Any]:
    # Determine the best tool based on the next task's instruction
    task_instruction = ""
    if task_context is not None and task_context.task_instruction:
        task_instruction = str(task_context.task_instruction).strip()
    recommended_tool = agent._extract_recommended_tool_from_instruction(task_instruction)
    tool_name = recommended_tool or "code_executor"

    forced_task = agent._build_forced_handoff_followthrough_task(
        task_context=task_context,
        user_query=user_query,
        previous_task_id=previous_task_id,
        next_task_id=next_task_id,
    )
    logger.warning(
        "[DEEP_THINK_NATIVE] Forcing %s after task handoff previous=%s next=%s reason=%s iteration=%s",
        tool_name,
        previous_task_id,
        next_task_id,
        reason,
        iteration,
    )

    if tool_name == "code_executor":
        arguments = {"task": forced_task}
    elif tool_name == "literature_pipeline":
        arguments = {"query": task_instruction[:500], "session_id": getattr(agent, "_session_id", None)}
    elif tool_name == "web_search":
        arguments = {"query": task_instruction[:200]}
    else:
        arguments = {"task": forced_task}

    forced_call = SimpleNamespace(
        name=tool_name,
        id=f"forced_handoff_followthrough_{iteration}_{next_task_id}_{reason}",
        arguments=arguments,
    )
    return await agent._execute_native_tool_call(
        tc=forced_call,
        iteration=iteration,
        index=9998,
    )


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


def _collect_execute_truth_events(
    cls: Any,
    steps: Sequence[ThinkingStep],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    order = 0
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            tool_name = str(entry.get("tool") or "").strip().lower()
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            if not isinstance(inner, dict):
                continue

            raw_success = inner.get("success")
            if raw_success is None:
                raw_success = payload.get("success")
            success = bool(raw_success) if raw_success is not None else False

            operation = str(
                inner.get("operation")
                or payload.get("operation")
                or ""
            ).strip().lower()
            task_type = str(inner.get("task_type") or "").strip().lower()
            execution_status = str(inner.get("execution_status") or "").strip().lower()
            verification_state = str(inner.get("verification_state") or "").strip().lower()

            kind: Optional[str] = None
            trusted = False
            if tool_name == "result_interpreter":
                if operation in {"profile", "metadata"} or (
                    operation == "analyze" and task_type == "text_only"
                ):
                    kind = "profile"
                    trusted = success
                elif operation in {"execute", "analyze"}:
                    kind = "execution"
                    trusted = success and (
                        execution_status == "success" or not execution_status
                    )
            elif tool_name == "terminal_session":
                if operation == "write":
                    kind = "execution"
                    trusted = success and verification_state == "verified_success"
            elif tool_name in cls._CODE_EXECUTION_TOOLS:
                kind = "execution"
                trusted = success

            if kind is None:
                continue

            summary_text = ""
            profile_payload = inner.get("profile")
            if isinstance(profile_payload, dict):
                profile_summary = profile_payload.get("summary")
                if isinstance(profile_summary, str) and profile_summary.strip():
                    summary_text = profile_summary.strip()
            if not summary_text:
                execution_output = inner.get("execution_output")
                if isinstance(execution_output, str) and execution_output.strip():
                    summary_text = execution_output.strip()
            if not summary_text:
                for key in ("summary", "error", "execution_error", "message"):
                    candidate = str(
                        inner.get(key) or payload.get(key) or ""
                    ).strip()
                    if candidate:
                        summary_text = candidate
                        break

            error_text = str(
                inner.get("error")
                or inner.get("execution_error")
                or payload.get("error")
                or ""
            ).strip()

            events.append(
                {
                    "order": order,
                    "iteration": step.iteration,
                    "tool": tool_name,
                    "operation": operation,
                    "kind": kind,
                    "success": success,
                    "trusted": trusted,
                    "summary_text": summary_text,
                    "error": error_text,
                }
            )
            order += 1
    return events


def _build_execute_failure_warning(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    failed_event: Dict[str, Any],
) -> str:
    """Soft warning prepended to the model's answer when execution failed
    but the model produced substantive content from read-only tools."""
    language = _dta().detect_reasoning_language(user_query or "")
    tool_name = str(failed_event.get("tool") or "execution tool").strip()
    failure_detail = str(
        failed_event.get("error")
        or failed_event.get("summary_text")
        or "unknown failure"
    ).strip()
    return _dta()._localized_text(
        language,
        (
            f"> ⚠️ **注意**：本轮主执行工具未成功（{tool_name} 失败：{failure_detail}）。"
            "以下内容基于文件读取工具的输出，统计数值未经代码验证，仅供参考。"
        ),
        (
            f"> ⚠️ **Warning**: The main execution tool failed in this run "
            f"({tool_name}: {failure_detail}). "
            f"The content below is based on file-reading tools; "
            f"statistical figures are not code-verified and should be treated as approximate."
        ),
    )


def _build_execute_failure_truth_barrier(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    failed_event: Dict[str, Any],
    profile_text: Optional[str] = None,
) -> str:
    language = _dta().detect_reasoning_language(user_query or profile_text or "")
    tool_name = str(failed_event.get("tool") or "execution tool").strip()
    failure_detail = str(
        failed_event.get("error")
        or failed_event.get("summary_text")
        or "unknown failure"
    ).strip()

    if profile_text:
        return _dta()._localized_text(
            language,
            (
                f"说明：本轮真正的执行工具未成功完成（{tool_name} 失败：{failure_detail}）。"
                "下面只保留本轮已验证的确定性数据 profile 结果，不把它当作完整分析已完成：\n\n"
                f"{profile_text}"
            ),
            (
                f"Note: The main execution tool did not complete successfully in this run "
                f"({tool_name} failed: {failure_detail}). The content below is limited to "
                f"verified deterministic dataset profiling from this run and should not be "
                f"treated as a completed full analysis.\n\n{profile_text}"
            ),
        )

    return _dta()._localized_text(
        language,
        (
            f"本轮真正的执行工具未成功完成（{tool_name} 失败：{failure_detail}）。"
            "因此不能把后续分析性表述视为已验证结论。当前只能确认执行被该错误阻塞；"
            "如需继续，请先修复该失败原因后再重新运行。"
        ),
        (
            f"The main execution tool did not complete successfully in this run "
            f"({tool_name} failed: {failure_detail}). Any later analysis-style narrative "
            f"cannot be treated as verified. At this point the run is blocked by that error; "
            f"fix the failure first and rerun to obtain a trustworthy result."
        ),
    )


def _apply_execute_failure_truth_barrier(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> str:
    text = str(answer or "").strip()
    if not text or not agent._is_execute_task_request():
        return text

    events = agent._collect_execute_truth_events(steps)
    failed_execution_events = [
        event
        for event in events
        if event.get("kind") == "execution" and not event.get("success")
    ]
    if not failed_execution_events:
        return text

    last_failure = failed_execution_events[-1]
    last_failure_order = int(last_failure.get("order", -1))
    later_events = [
        event for event in events if int(event.get("order", -1)) > last_failure_order
    ]

    if any(
        event.get("kind") == "execution" and event.get("trusted")
        for event in later_events
    ):
        return text

    profile_recovery = next(
        (
            event
            for event in reversed(later_events)
            if event.get("kind") == "profile"
            and event.get("trusted")
            and str(event.get("summary_text") or "").strip()
        ),
        None,
    )
    if profile_recovery is not None:
        barrier = agent._build_execute_failure_truth_barrier(
            user_query=user_query,
            failed_event=last_failure,
            profile_text=str(profile_recovery.get("summary_text") or "").strip(),
        )
        return barrier

    # Check if the model's answer contains substantive content from
    # successful read tools (document_reader, file_operations, etc.).
    # If so, prepend a warning instead of replacing the entire answer,
    # so partial results are preserved for the user.
    has_substantive_answer = len(text) > 200
    if has_substantive_answer:
        if _looks_like_completion_claim_text(text):
            return agent._build_execute_failure_truth_barrier(
                user_query=user_query,
                failed_event=last_failure,
            )
        warning = agent._build_execute_failure_warning(
            user_query=user_query,
            failed_event=last_failure,
        )
        return f"{warning}\n\n---\n\n{text}"

    return agent._build_execute_failure_truth_barrier(
        user_query=user_query,
        failed_event=last_failure,
    )


def _collect_evidence_scope_signals(cls: Any, steps: Sequence[ThinkingStep]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            tool_name = str(entry.get("tool") or "").strip().lower()
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            if not isinstance(inner, dict):
                continue
            evidence_scope = inner.get("evidence_scope")
            if not isinstance(evidence_scope, dict):
                evidence_scope = payload.get("evidence_scope")
            status_counts = None
            if isinstance(evidence_scope, dict):
                status_counts = evidence_scope.get("status_counts")
            if status_counts is None and isinstance(inner.get("counts"), dict):
                counts = inner.get("counts")
                if any(key in counts for key in ("completed", "failed", "status_file_total")):
                    status_counts = {
                        key: counts.get(key)
                        for key in ("completed", "failed", "status_file_total")
                        if key in counts
                    }
            status_count_sources = None
            if isinstance(inner.get("status_count_sources"), list):
                status_count_sources = inner.get("status_count_sources")
            elif isinstance(evidence_scope, dict) and isinstance(evidence_scope.get("status_count_sources"), list):
                status_count_sources = evidence_scope.get("status_count_sources")
            status_counts_confidence = inner.get("status_counts_confidence")
            if status_counts_confidence is None and isinstance(evidence_scope, dict):
                status_counts_confidence = evidence_scope.get("status_counts_confidence")
            signal: Dict[str, Any] = {
                "tool": tool_name,
                "operation": str(inner.get("operation") or payload.get("operation") or "").strip().lower(),
                "path": inner.get("path") or payload.get("path"),
                "counts": inner.get("counts") if isinstance(inner.get("counts"), dict) else None,
                "summary": inner.get("summary") if isinstance(inner.get("summary"), str) else None,
                "sample_items": inner.get("sample_items") if isinstance(inner.get("sample_items"), list) else None,
                "evidence_scope": evidence_scope if isinstance(evidence_scope, dict) else None,
                "reconciliation": inner.get("reconciliation") if isinstance(inner.get("reconciliation"), dict) else (
                    evidence_scope.get("reconciliation") if isinstance(evidence_scope, dict) and isinstance(evidence_scope.get("reconciliation"), dict) else None
                ),
                "completeness_status": inner.get("completeness_status") or payload.get("completeness_status"),
                "status_counts": status_counts if isinstance(status_counts, dict) else None,
                "status_count_sources": status_count_sources,
                "status_counts_confidence": status_counts_confidence,
                "incomplete_examples": inner.get("incomplete_examples") if isinstance(inner.get("incomplete_examples"), list) else None,
                "partial_completion_suspected": bool(
                    inner.get("partial_completion_suspected")
                    or payload.get("partial_completion_suspected")
                ),
                "partial_ratio": inner.get("partial_ratio") or payload.get("partial_ratio"),
            }
            if signal["evidence_scope"] or signal["status_counts"] or signal["reconciliation"] or signal["incomplete_examples"] or signal["partial_completion_suspected"]:
                signals.append(signal)
    return signals


def _build_evidence_scope_notice(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    signals: Sequence[Dict[str, Any]],
    replace_claim: bool = False,
) -> str:
    completed: Optional[int] = None
    failed: Optional[int] = None
    failure_examples: List[str] = []
    partial_ratio = ""
    sampled_or_partial = False
    paths: List[str] = []
    profile_summaries: List[str] = []
    status_source_names: List[str] = []
    status_directory_names: List[str] = []
    reconciliation_notes: List[str] = []
    reconciliation_missing_examples: List[str] = []
    reconciliation_guidance: List[str] = []
    suffix_profile_text = ""
    success_structure_text = ""
    sampled_structure_notes: List[str] = []
    seen_sources: set[tuple[str, str, str]] = set()
    seen_profiles: set[str] = set()
    seen_status_names: set[str] = set()
    seen_reconciliation_notes: set[str] = set()
    seen_missing_examples: set[str] = set()
    seen_reconciliation_guidance: set[str] = set()
    seen_sampled_structure_notes: set[str] = set()

    for signal in signals:
        path = str(signal.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
        counts = signal.get("counts") if isinstance(signal.get("counts"), dict) else {}
        if counts:
            profile_key = path or str(signal.get("operation") or "profile")
            if profile_key not in seen_profiles:
                seen_profiles.add(profile_key)
                metric_parts: List[str] = []
                for key, label in (
                    ("direct_children", "direct_children"),
                    ("directories", "directories"),
                    ("sample_candidate_directories", "sample_candidate_directories"),
                    ("status_directories", "status_directories"),
                    ("files", "files"),
                    ("other", "other"),
                ):
                    value = counts.get(key)
                    if isinstance(value, int):
                        metric_parts.append(f"{label}={value}")
                if metric_parts:
                    profile_summaries.append(", ".join(metric_parts))
        evidence_scope = signal.get("evidence_scope") if isinstance(signal.get("evidence_scope"), dict) else {}
        directory_classification = (
            evidence_scope.get("directory_classification")
            if isinstance(evidence_scope.get("directory_classification"), dict)
            else {}
        )
        if directory_classification:
            for name in directory_classification.get("status_directory_names") or []:
                name_text = str(name or "").strip()
                if name_text and name_text not in status_directory_names:
                    status_directory_names.append(name_text)
        completeness = str(
            signal.get("completeness_status")
            or evidence_scope.get("completeness_status")
            or ""
        ).strip().lower()
        enumeration = evidence_scope.get("enumeration") if isinstance(evidence_scope.get("enumeration"), dict) else {}
        omitted = enumeration.get("omitted_children")
        if completeness in {"partial", "unknown"} or (isinstance(omitted, int) and omitted > 0):
            sampled_or_partial = True
        status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
        status_total = status_counts.get("status_file_total")
        sample_candidates = directory_classification.get("sample_candidate_directories")
        if isinstance(status_total, int) and isinstance(sample_candidates, int) and status_total != sample_candidates:
            note = f"status_file_total={status_total} differs from sample_candidate_directories={sample_candidates}"
            if note not in seen_reconciliation_notes:
                seen_reconciliation_notes.add(note)
                reconciliation_notes.append(note)
        reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
        if reconciliation:
            rec_counts = reconciliation.get("counts") if isinstance(reconciliation.get("counts"), dict) else {}
            rec_examples = reconciliation.get("examples") if isinstance(reconciliation.get("examples"), dict) else {}
            status_missing = rec_counts.get("status_entries_missing_directories")
            failure_missing = rec_counts.get("failure_missing_directories")
            success_missing = rec_counts.get("success_missing_directories")
            sample_without_status = rec_counts.get("sample_dirs_without_status")
            rec_note_parts: List[str] = []
            for key, label in (
                ("status_unique_total", "status_unique_total"),
                ("sample_candidate_directories", "sample_candidate_directories"),
                ("status_entries_missing_directories", "status_entries_missing_directories"),
                ("failure_missing_directories", "failure_missing_directories"),
                ("success_missing_directories", "success_missing_directories"),
                ("sample_dirs_without_status", "sample_dirs_without_status"),
                ("duplicate_success_entries", "duplicate_success_entries"),
                ("duplicate_failure_entries", "duplicate_failure_entries"),
                ("success_failure_overlap", "success_failure_overlap"),
            ):
                value = rec_counts.get(key)
                if isinstance(value, int):
                    rec_note_parts.append(f"{label}={value}")
            if rec_note_parts:
                note = ", ".join(rec_note_parts)
                if note not in seen_reconciliation_notes:
                    seen_reconciliation_notes.add(note)
                    reconciliation_notes.append(note)
            for key in ("failure_missing_directories", "success_missing_directories", "sample_dirs_without_status", "success_failure_overlap"):
                values = rec_examples.get(key)
                if not isinstance(values, list) or not values:
                    continue
                for value in values[:5]:
                    text_value = str(value or "").strip()
                    if text_value and text_value not in seen_missing_examples:
                        seen_missing_examples.add(text_value)
                        reconciliation_missing_examples.append(text_value)
            for item in reconciliation.get("claim_guidance") or []:
                guidance = str(item or "").strip()
                if guidance and guidance not in seen_reconciliation_guidance:
                    seen_reconciliation_guidance.add(guidance)
                    reconciliation_guidance.append(guidance)
            if isinstance(status_missing, int) and status_missing and not (rec_counts.get("duplicate_success_entries") or rec_counts.get("duplicate_failure_entries") or rec_counts.get("success_failure_overlap")):
                guidance = "Status/directory mismatch is explained by status IDs without matching directories; do not infer retries/reruns from this evidence."
                if guidance not in seen_reconciliation_guidance:
                    seen_reconciliation_guidance.add(guidance)
                    reconciliation_guidance.append(guidance)
            name_profile = reconciliation.get("sample_directory_name_profile") if isinstance(reconciliation.get("sample_directory_name_profile"), dict) else {}
            suffix_counts = name_profile.get("hyphen_suffix_counts") if isinstance(name_profile.get("hyphen_suffix_counts"), dict) else {}
            if suffix_counts and not suffix_profile_text:
                suffix_profile_text = ", ".join(f"{key}={value}" for key, value in list(suffix_counts.items())[:12])
            structure = reconciliation.get("success_directory_structure") if isinstance(reconciliation.get("success_directory_structure"), dict) else {}
            file_distribution = structure.get("file_count_distribution") if isinstance(structure.get("file_count_distribution"), dict) else {}
            if file_distribution and not success_structure_text:
                scanned = structure.get("directories_scanned")
                considered = structure.get("entries_considered")
                patterns = structure.get("common_file_patterns") if isinstance(structure.get("common_file_patterns"), list) else []
                pattern_text = ", ".join(
                    str(item.get("pattern"))
                    for item in patterns[:5]
                    if isinstance(item, dict) and item.get("pattern")
                )
                success_structure_text = (
                    f"success directories scanned={scanned}/{considered}, file_count_distribution={file_distribution}"
                    + (f", common_file_patterns={pattern_text}" if pattern_text else "")
                )
        count_sources = signal.get("status_count_sources") if isinstance(signal.get("status_count_sources"), list) else []
        if count_sources:
            for source in count_sources:
                if not isinstance(source, dict):
                    continue
                if source.get("count_confidence") != "high":
                    continue
                source_path = str(source.get("path") or source.get("name") or path).strip()
                kind = str(source.get("kind") or "").strip()
                key = (source_path, kind, str(source.get("count_source") or ""))
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                if source_path and source_path not in seen_status_names:
                    seen_status_names.add(source_path)
                    status_source_names.append(str(source.get("name") or source_path).strip())
                entry_count = source.get("entry_count")
                if not isinstance(entry_count, int):
                    continue
                if kind == "success":
                    completed = (completed or 0) + entry_count
                elif kind == "failure":
                    failed = (failed or 0) + entry_count
        else:
            fallback_key = (path, str(signal.get("operation") or ""), "status_counts")
            if fallback_key not in seen_sources:
                seen_sources.add(fallback_key)
                if isinstance(status_counts.get("completed"), int):
                    completed = (completed or 0) + int(status_counts["completed"])
                if isinstance(status_counts.get("failed"), int):
                    failed = (failed or 0) + int(status_counts["failed"])
        examples = signal.get("incomplete_examples")
        if isinstance(examples, list):
            for item in examples[:5]:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    reason = str(item.get("reason") or "").strip()
                    if name:
                        failure_examples.append(f"{name} ({reason})" if reason else name)
        if signal.get("partial_completion_suspected"):
            sampled_or_partial = True
            if signal.get("partial_ratio"):
                partial_ratio = str(signal.get("partial_ratio"))
        sample_items = signal.get("sample_items") if isinstance(signal.get("sample_items"), list) else []
        sampled_dirs = [
            item
            for item in sample_items
            if isinstance(item, dict)
            and str(item.get("type") or "").strip().lower() == "directory"
            and item.get("child_count") is not None
        ]
        if sampled_dirs:
            structure_counts = sorted(
                {
                    int(item.get("child_count"))
                    for item in sampled_dirs
                    if isinstance(item.get("child_count"), int)
                }
            )
            note = (
                f"per-sample file structure is based on {len(sampled_dirs)} sampled direct child directories"
                + (f" with observed child_count values {structure_counts[:5]}" if structure_counts else "")
            )
            if note not in seen_sampled_structure_notes:
                seen_sampled_structure_notes.add(note)
                sampled_structure_notes.append(note)

    path_text = ", ".join(paths[:3]) if paths else "the inspected path"
    completed_text = completed if completed is not None else "unknown"
    failed_text = failed if failed is not None else "unknown"
    examples_text = ", ".join(failure_examples[:5])
    profile_text = "; ".join(profile_summaries[:3])
    source_text = ", ".join(status_source_names[:5])
    status_dirs_text = ", ".join(status_directory_names[:5])
    reconciliation_text = "; ".join(reconciliation_notes[:3])
    reconciliation_examples_text = ", ".join(reconciliation_missing_examples[:8])
    reconciliation_guidance_text = " ".join(reconciliation_guidance[:3])
    sampled_structure_text = "; ".join(sampled_structure_notes[:3])
    label = "Corrected evidence-scoped conclusion" if replace_claim else "Evidence-scope note"
    if replace_claim:
        lines = [f"{label}:"]
        lines.append(f"- Evidence scope: tool output for {path_text}.")
        if profile_text:
            lines.append(f"- Directory profile: {profile_text}.")
        if completed is not None or failed is not None:
            lines.append(f"- Status counts: completed={completed_text}, failed={failed_text}.")
        if source_text:
            lines.append(f"- Count sources: {source_text}.")
        if status_dirs_text:
            lines.append(f"- Status/progress directories: {status_dirs_text}. Do not report root direct_children as verified sample-directory count.")
        if reconciliation_text:
            lines.append(f"- Reconciliation needed: {reconciliation_text}.")
        if reconciliation_examples_text:
            lines.append(f"- Reconciliation examples: {reconciliation_examples_text}.")
        if reconciliation_guidance_text:
            lines.append(f"- Reconciliation interpretation limit: {reconciliation_guidance_text}")
        if suffix_profile_text:
            lines.append(f"- Sample-name suffix distribution: {suffix_profile_text}.")
        if success_structure_text:
            lines.append(f"- Completed-directory file structure: {success_structure_text}.")
        if sampled_structure_text:
            lines.append(f"- Per-sample file structure evidence: {sampled_structure_text}; do not state that each/all samples share that structure.")
        if partial_ratio:
            lines.append(f"- Partial-completion signal: {partial_ratio}.")
        if examples_text:
            lines.append(f"- Failure/incomplete examples: {examples_text}.")
        if sampled_or_partial:
            lines.append("- Scope caution: Do not treat sampled or compacted listings as evidence that all samples succeeded.")
        lines.append("- Correction: the original all-success/global completion claim is not supported by the available evidence.")
        return "\n".join(lines)

    parts = [f"{label}: this run is limited to tool output for {path_text}. "]
    if profile_text:
        parts.append(f"Directory profile: {profile_text}. ")
    if completed is not None or failed is not None:
        parts.append(f"Observed status counts: completed={completed_text}, failed={failed_text}. ")
    if status_dirs_text:
        parts.append(f"Status/progress directories: {status_dirs_text}; root direct_children is not a verified sample-directory count. ")
    if reconciliation_text:
        parts.append(f"Reconciliation needed: {reconciliation_text}. ")
    if reconciliation_examples_text:
        parts.append(f"Reconciliation examples: {reconciliation_examples_text}. ")
    if reconciliation_guidance_text:
        parts.append(f"Reconciliation interpretation limit: {reconciliation_guidance_text} ")
    if suffix_profile_text:
        parts.append(f"Sample-name suffix distribution: {suffix_profile_text}. ")
    if success_structure_text:
        parts.append(f"Completed-directory file structure: {success_structure_text}. ")
    if sampled_structure_text:
        parts.append(f"Per-sample file structure evidence: {sampled_structure_text}; do not state that each/all samples share that structure. ")
    if partial_ratio:
        parts.append(f"A partial-completion signal was detected: {partial_ratio}. ")
    if examples_text:
        parts.append(f"Failure/incomplete examples: {examples_text}. ")
    if sampled_or_partial:
        parts.append("Do not treat sampled or compacted listings as evidence that all samples succeeded.")
    if replace_claim:
        parts.append(" The original global success claim is not supported by the available evidence.")
    return "".join(parts).strip()


def _apply_evidence_scope_truth_barrier(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> str:
    text = str(answer or "").strip()
    if not text:
        return text
    signals = agent._collect_evidence_scope_signals(steps)
    if not signals:
        return text
    if _answer_acknowledges_failed_status_counts(text, signals):
        return text

    needs_notice = False
    replace_claim = False
    has_global_success_claim = _looks_like_global_success_claim_text(text)
    has_unverified_sample_directory_claim = bool(_UNVERIFIED_SAMPLE_DIRECTORY_CLAIM_RE.search(text))
    has_unverified_each_file_structure_claim = bool(_UNVERIFIED_EACH_FILE_STRUCTURE_RE.search(text))
    has_unsupported_retry_rerun_claim = bool(_UNSUPPORTED_RETRY_RERUN_RE.search(text))
    for signal in signals:
        evidence_scope = signal.get("evidence_scope") if isinstance(signal.get("evidence_scope"), dict) else {}
        enumeration = evidence_scope.get("enumeration") if isinstance(evidence_scope.get("enumeration"), dict) else {}
        omitted = enumeration.get("omitted_children")
        status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
        counts = signal.get("counts") if isinstance(signal.get("counts"), dict) else {}
        directory_classification = (
            evidence_scope.get("directory_classification")
            if isinstance(evidence_scope.get("directory_classification"), dict)
            else {}
        )
        if has_unverified_sample_directory_claim and (
            counts.get("status_directories")
            or directory_classification.get("status_directories")
            or directory_classification.get("sample_candidate_directories") != counts.get("direct_children")
        ):
            needs_notice = True
            replace_claim = True
        if has_unverified_each_file_structure_claim:
            sample_items = signal.get("sample_items") if isinstance(signal.get("sample_items"), list) else []
            reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
            success_structure = reconciliation.get("success_directory_structure") if isinstance(reconciliation.get("success_directory_structure"), dict) else {}
            file_distribution = success_structure.get("file_count_distribution") if isinstance(success_structure.get("file_count_distribution"), dict) else {}
            complete_structure_scan = bool(success_structure.get("complete_scan"))
            if not (complete_structure_scan and len(file_distribution) == 1):
                if sample_items:
                    needs_notice = True
                    replace_claim = True
        if has_unsupported_retry_rerun_claim:
            reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
            rec_counts = reconciliation.get("counts") if isinstance(reconciliation.get("counts"), dict) else {}
            has_retry_evidence = bool(
                rec_counts.get("duplicate_success_entries")
                or rec_counts.get("duplicate_failure_entries")
                or rec_counts.get("success_failure_overlap")
            )
            if reconciliation and not has_retry_evidence:
                needs_notice = True
                replace_claim = True
        if isinstance(status_counts.get("failed"), int) and status_counts.get("failed", 0) > 0:
            needs_notice = True
            if has_global_success_claim:
                replace_claim = True
        if signal.get("partial_completion_suspected"):
            needs_notice = True
            if has_global_success_claim:
                replace_claim = True
        if isinstance(omitted, int) and omitted > 0 and has_global_success_claim:
            needs_notice = True
            replace_claim = True
        completeness = str(
            signal.get("completeness_status")
            or evidence_scope.get("completeness_status")
            or ""
        ).strip().lower()
        if completeness in {"partial", "unknown"} and has_global_success_claim:
            needs_notice = True
            replace_claim = True
    if not needs_notice:
        return text

    notice = sanitize_professional_response_text(
        agent._build_evidence_scope_notice(
            user_query=user_query,
            signals=signals,
            replace_claim=replace_claim,
        )
    )
    if not notice or text.startswith(notice):
        return text
    if replace_claim:
        return notice
    return f"{notice}\n\n{text}"


def _unwrap_tool_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the innermost result dict, handling nested {result: {...}} wrappers."""
    inner = payload.get("result")
    return inner if isinstance(inner, dict) else payload


def _collect_plan_operation_events(cls: Any, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "plan_operation":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            rp = cls._unwrap_tool_result(payload)
            success = bool(rp.get("success", payload.get("success")))
            operation = str(rp.get("operation") or "").strip().lower()
            plan_id = cls._coerce_positive_int(rp.get("plan_id"))
            plan_title = str(rp.get("plan_title") or rp.get("title") or "").strip()
            error = str(rp.get("error") or "").strip()
            if not error:
                error = str(
                    payload.get("error")
                    or payload.get("summary")
                    or (rp.get("message") if not success else "")
                    or ""
                ).strip()
            applied_changes = rp.get("applied_changes")
            failed_changes = rp.get("failed_changes")
            try:
                applied_changes = int(applied_changes) if applied_changes is not None else None
            except (TypeError, ValueError):
                applied_changes = None
            try:
                failed_changes = int(failed_changes) if failed_changes is not None else None
            except (TypeError, ValueError):
                failed_changes = None
            events.append(
                {
                    "success": success,
                    "operation": operation or None,
                    "plan_id": plan_id,
                    "plan_title": plan_title or None,
                    "applied_changes": applied_changes,
                    "failed_changes": failed_changes,
                    "error": error or None,
                    "already_bound_plan_reused": bool(
                        rp.get("already_bound_plan_reused")
                        or payload.get("already_bound_plan_reused")
                    ),
                }
            )
    return events


def _summarize_structured_plan_outcome(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    *,
    user_query: str = "",
) -> Dict[str, Any]:
    # Enforce explicit plan lifecycle contracts. The LLM still decides how
    # to decompose and what evidence to gather, but once routing identifies
    # create/review/optimize/execute intent, prose-only answers are not
    # allowed to masquerade as real plan operations.
    plan_id = agent._current_plan_id()
    plan_title = agent._current_plan_title()
    flags = agent._plan_contract_flags()
    route_reasons = agent.request_profile.get("route_reason_codes")
    if not isinstance(route_reasons, list):
        route_reasons = []

    events = agent._collect_plan_operation_events(steps)

    if flags["conflict_requires_confirmation"]:
        return {
            "required": True,
            "mode": "plan_conflict_confirmation",
            "called": bool(events),
            "satisfied": False,
            "state": "confirmation_required",
            "message": agent._build_plan_conflict_confirmation_message(),
            "plan_id": plan_id,
            "plan_title": plan_title,
            "operation": None,
        }

    required_ops: List[str] = []
    if flags["create_required"]:
        required_ops.append("create")
    if flags["execute_required"]:
        required_ops.append("execute_all")
    if flags["review_required"]:
        required_ops.append("review")
    if flags["optimize_required"]:
        required_ops.append("optimize")

    if required_ops:
        def _successful_event(op_name: str) -> Optional[Dict[str, Any]]:
            for event in events:
                if not event.get("success"):
                    continue
                if event.get("operation") != op_name:
                    continue
                if op_name == "create" and event.get("already_bound_plan_reused"):
                    continue
                if op_name == "optimize" and event.get("applied_changes") == 0:
                    continue
                return event
            return None

        create_event = _successful_event("create") if flags["create_required"] else None
        execute_event = _successful_event("execute_all") if flags["execute_required"] else None
        review_event = _successful_event("review") if flags["review_required"] else None
        optimize_event = _successful_event("optimize") if flags["optimize_required"] else None

        satisfied_ops: List[str] = []
        missing_ops: List[str] = []
        if flags["create_required"]:
            (satisfied_ops if create_event else missing_ops).append("create")
        if flags["execute_required"]:
            execute_matches_created = True
            if flags["execute_after_create_required"] and create_event and execute_event:
                created_id = create_event.get("plan_id")
                executed_id = execute_event.get("plan_id")
                execute_matches_created = bool(created_id and executed_id == created_id)
            if execute_event and execute_matches_created:
                satisfied_ops.append("execute_all")
            else:
                missing_ops.append("execute_all")
        if flags["review_required"]:
            (satisfied_ops if review_event else missing_ops).append("review")
        if flags["optimize_required"]:
            (satisfied_ops if optimize_event else missing_ops).append("optimize")

        satisfied = not missing_ops
        last_op = events[-1].get("operation") if events else None
        outcome_plan_id = None
        for event in reversed(events):
            if event.get("plan_id") is not None:
                outcome_plan_id = event.get("plan_id")
                break
        if outcome_plan_id is None:
            outcome_plan_id = plan_id
        outcome_plan_title = plan_title
        for event in reversed(events):
            if event.get("plan_title"):
                outcome_plan_title = event.get("plan_title")
                break
        message = None
        if not satisfied:
            if events:
                missing_text = ", ".join(missing_ops)
                message = (
                    "The requested structured plan contract was not satisfied: "
                    f"missing successful plan_operation operation(s): {missing_text}."
                )
            else:
                missing_text = ", ".join(missing_ops)
                message = (
                    "The requested structured plan contract was not satisfied: "
                    "plan_operation was not called. "
                    f"Required operation(s): {missing_text}."
                )
        return {
            "required": True,
            "mode": "plan_lifecycle",
            "called": bool(events),
            "satisfied": satisfied,
            "state": "satisfied" if satisfied else ("called_but_incomplete" if events else "not_called"),
            "message": message,
            "plan_id": outcome_plan_id,
            "plan_title": outcome_plan_title,
            "operation": last_op,
            "required_operations": required_ops,
            "satisfied_operations": satisfied_ops,
            "missing_operations": missing_ops,
        }

    is_bound_plan_mutation_request = (
        plan_id is not None
        and any(
            code in route_reasons
            for code in ("plan_review", "plan_optimize")
        )
    )

    if not is_bound_plan_mutation_request:
        return {
            "required": False,
            "mode": None,
            "called": False,
            "satisfied": False,
            "state": None,
            "message": None,
            "plan_id": plan_id,
            "plan_title": plan_title,
            "operation": None,
        }

    # Check if plan_operation was actually called with a mutation operation.
    # Exclude "create" — when a plan is already bound, the tool wrapper
    # rewrites create into a no-op already_bound_plan_reused result that
    # does not actually modify the plan.
    mutation_ops = {"review", "optimize", "update"}
    called = bool(events)
    satisfied = any(
        e.get("success")
        and e.get("operation") in mutation_ops
        and not e.get("already_bound_plan_reused")
        for e in events
    )
    last_op = events[-1].get("operation") if events else None

    return {
        "required": True,
        "mode": "bound_plan_mutation",
        "called": called,
        "satisfied": satisfied,
        "state": "satisfied" if satisfied else ("called_but_failed" if called else "not_called"),
        "message": None if satisfied else (
            "plan_operation was called but did not succeed"
            if called
            else "plan_operation was not called; the user requested a plan mutation"
        ),
        "plan_id": plan_id,
        "plan_title": plan_title,
        "operation": last_op,
    }


def _extract_successful_created_plan_from_tool_results(
    cls: Any,
    tool_results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for item in tool_results:
        if str(item.get("tool_name") or "").strip().lower() != "plan_operation":
            continue
        payload = item.get("tool_result")
        if not isinstance(payload, dict):
            continue
        inner = cls._unwrap_tool_result(payload)
        success = bool(inner.get("success", payload.get("success")))
        operation = str(inner.get("operation") or payload.get("operation") or "").strip().lower()
        if not success or operation != "create":
            continue
        plan_id = cls._coerce_positive_int(inner.get("plan_id") or payload.get("plan_id"))
        if plan_id is None:
            continue
        plan_title = str(
            inner.get("plan_title")
            or inner.get("title")
            or payload.get("plan_title")
            or payload.get("title")
            or ""
        ).strip()
        return {
            "plan_id": plan_id,
            "plan_title": plan_title or None,
            "already_bound_plan_reused": bool(
                inner.get("already_bound_plan_reused")
                or payload.get("already_bound_plan_reused")
            ),
        }
    return None


def _ensure_structured_plan_notice(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    outcome: Dict[str, Any],
    user_query: str,
) -> str:
    text = str(answer or "").strip()
    if not outcome.get("required") or outcome.get("satisfied"):
        return text
    notice = sanitize_professional_response_text(str(outcome.get("message") or "").strip())
    if not notice:
        notice = _dta()._localized_text(
            _dta().detect_reasoning_language(user_query or text),
            "本轮未创建或更新结构化计划。",
            "A structured plan was not created or updated in this run.",
        )
    if not text:
        return notice
    if text.startswith(notice):
        return text
    return f"{notice}\n\n{text}"


def _build_structured_plan_contract_failure_answer(
    agent: "DeepThinkAgent",
    *,
    outcome: Dict[str, Any],
    user_query: str,
) -> str:
    if str(outcome.get("state") or "") == "confirmation_required":
        return agent._build_plan_conflict_confirmation_message()
    message = sanitize_professional_response_text(str(outcome.get("message") or "").strip())
    if not message:
        missing = outcome.get("missing_operations")
        if isinstance(missing, list) and missing:
            message = (
                "The requested structured plan contract was not satisfied: missing successful "
                f"plan_operation operation(s): {', '.join(str(item) for item in missing)}."
            )
        else:
            message = "The requested structured plan contract was not satisfied."
    required = outcome.get("required_operations")
    if isinstance(required, list) and required:
        return (
            f"{message}\n\n"
            f"Required operation(s): {', '.join(str(item) for item in required)}. "
            "I cannot treat file probes, terminal checks, or ordinary markdown text as a completed structured plan operation."
        )
    return message


def _directory_dataset_analysis_requested(user_query: str) -> bool:
    text = str(user_query or "").strip()
    if not text:
        return False
    return bool(_DIRECTORY_DATASET_REQUEST_RE.search(text) and _ABSOLUTE_PATH_RE.search(text))


def _extract_directory_path_from_query(user_query: str) -> Optional[str]:
    matches = [match.group(0).rstrip(".,;:)]}>") for match in _ABSOLUTE_PATH_RE.finditer(str(user_query or ""))]
    if not matches:
        return None
    return max(matches, key=len)


def _phagescope_dataset_analysis_requested(cls: Any, user_query: str) -> bool:
    text = str(user_query or "").strip()
    if not text:
        return False
    path = cls._extract_directory_path_from_query(text) or ""
    path_mentions_phagescope = "phagescope" in path.lower()
    if not path_mentions_phagescope:
        return False
    if cls._path_is_generic_tabular_file(path):
        return False
    # A real on-disk directory without a meta_data/ child is NOT a PhageScope
    # dataset (e.g. .../phagescope/test holding a clinical xlsx). deep_profile
    # would fail with "Missing meta_data directory", so demote here and let the
    # generic tool chain (code_executor) handle it.
    if cls._directory_positively_lacks_phagescope_meta_data(path):
        return False
    return bool(
        _PHAGESCOPE_DATASET_REQUEST_RE.search(text)
        or _PHAGESCOPE_ANALYSIS_ACTION_RE.search(text)
    )


def _path_is_generic_tabular_file(path: str) -> bool:
    text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》").lower()
    return text.endswith(_NON_PHAGESCOPE_TABULAR_FILE_EXTS)


def _directory_positively_lacks_phagescope_meta_data(path: str) -> bool:
    text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》")
    if not text:
        return False
    try:
        return os.path.isdir(text) and not os.path.isdir(os.path.join(text, "meta_data"))
    except OSError:
        return False


def _directory_payload_is_generic_tabular_only(path: str) -> bool:
    # Positive on-disk evidence that this is a simple tabular data folder (e.g. one
    # clinical .xlsx), not a multi-file dataset needing a profile/census: demote the
    # barrier so code_executor analyzes it directly. Conservative on purpose.
    text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》")
    if not text:
        return False
    try:
        if not os.path.isdir(text):
            return False
        saw_tabular_file = False
        with os.scandir(text) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    return False
                if not entry.is_file():
                    continue
                if entry.name.lower().endswith(_NON_PHAGESCOPE_TABULAR_FILE_EXTS):
                    saw_tabular_file = True
                else:
                    return False
        return saw_tabular_file
    except OSError:
        return False


def _file_operation_profile_or_census_seen(cls: Any, steps: Sequence[ThinkingStep]) -> bool:
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "file_operations":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            operation = str(inner.get("operation") or payload.get("operation") or "").strip().lower()
            if operation in {"profile", "census"}:
                return True
    return False


def _phagescope_deep_profile_seen(cls: Any, steps: Sequence[ThinkingStep]) -> bool:
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "phagescope_research":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            action = str(inner.get("action") or payload.get("action") or "").strip().lower()
            if action == "deep_profile" and inner.get("success", payload.get("success")) is not False:
                return True
    return False


def _phagescope_deep_profile_failure(cls: Any, steps: Sequence[ThinkingStep]) -> Optional[str]:
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "phagescope_research":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            action = str(inner.get("action") or payload.get("action") or "").strip().lower()
            if action != "deep_profile":
                continue
            success = inner.get("success", payload.get("success"))
            if success is not False:
                continue
            error = inner.get("error") or payload.get("error") or inner.get("summary") or payload.get("summary")
            return str(error or "phagescope_research deep_profile failed").strip()
    return None


def _build_phagescope_deep_profile_failure_answer(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> Optional[str]:
    if not agent._phagescope_dataset_analysis_requested(user_query):
        return None
    if agent._phagescope_deep_profile_seen(steps):
        return None
    error = agent._phagescope_deep_profile_failure(steps)
    if not error:
        return None
    path = agent._extract_directory_path_from_query(user_query) or "the PhageScope dataset path"
    return (
        f"I could not complete the PhageScope dataset analysis because `phagescope_research` "
        f"`deep_profile` failed for `{path}`: {error}. "
        "I am not going to synthesize a dataset-level answer from shallow file listings or sampled metadata. "
        "Please retry after fixing the tool/path permission issue; until then, any directory-listing evidence is only a limited diagnostic, not a complete PhageScope profile."
    )


def _needs_phagescope_deep_profile_before_final(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> Optional[str]:
    if "phagescope_research" not in agent.available_tools:
        return None
    if not agent._phagescope_dataset_analysis_requested(user_query):
        return None
    if agent._phagescope_deep_profile_seen(steps):
        return None
    return agent._extract_directory_path_from_query(user_query)


def _needs_directory_profile_before_final(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> Optional[str]:
    if "file_operations" not in agent.available_tools:
        return None
    if (
        "phagescope_research" in agent.available_tools
        and agent._phagescope_dataset_analysis_requested(user_query)
    ):
        return None
    if not agent._directory_dataset_analysis_requested(user_query):
        return None
    path = agent._extract_directory_path_from_query(user_query)
    if path and agent._directory_payload_is_generic_tabular_only(path):
        return None
    if agent._file_operation_profile_or_census_seen(steps):
        return None
    return agent._extract_directory_path_from_query(user_query)
