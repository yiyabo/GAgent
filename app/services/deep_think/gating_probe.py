"""Probe/verification-only detection and execution follow-through nudges for
the DeepThink agent (god-class split, behaviour zero-change).

Each function here is the body of the like-named DeepThinkAgent method with
`self` renamed to `agent` (`cls` kept); the class keeps thin wrappers with
the same decorators. Display-family helpers (detect_reasoning_language,
_localized_text) stay in deep_think_agent and are reached through the
late-bound `_dta()` so their monkeypatch surface is unchanged.

Sanctioned deviation from verbatim: _extract_recommended_tool_from_instruction
reads the class attribute _FOLLOWTHROUGH_TOOL_CANDIDATES through
_dta().DeepThinkAgent because the attribute stays on DeepThinkAgent and a
runtime import of the class here would be circular; the lookup still happens
at call time on the same class object, so behaviour is identical.
"""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from app.services.deep_think.models import TaskExecutionContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


# ---------------------------------------------------------------------------
# Read-only verification delegation detection ("牛刀核验" guard)
# ---------------------------------------------------------------------------
# Intent families (Chinese + English): read-only / do-not-modify markers and
# verification nouns. A delegated code_executor task is treated as read-only
# verification work ONLY when both families hit AND no production signal
# (write/save/generate/update/deliverable) survives after the read-only
# phrases are stripped (the strip prevents "不修改" from tripping the
# production regex via its bare "修改").
_READ_ONLY_INTENT_RE = re.compile(
    r"只读|不修改|不要修改|禁止修改|不得修改|勿修改|不可修改|不写入|不写文件|"
    r"read[\s-]?only|do\s+not\s+modify|don'?t\s+modify|must\s+not\s+(?:modify|change)|"
    r"without\s+modif\w*|no\s+changes?\b",
    re.IGNORECASE,
)
_VERIFICATION_INTENT_RE = re.compile(
    r"核验|审计|取证|校验|核对|审查|检查|核查|"
    r"verif\w*|audit\w*|check(?:ing|s)?\b|inspect\w*|review\b|forensic\w*",
    re.IGNORECASE,
)
_PRODUCTION_SIGNAL_RE = re.compile(
    # Imperative production only — passive/adjectival uses (产出目录, 生成的,
    # 已保存的, generated/created) must NOT disqualify a read-only audit.
    r"写入|写出|输出到|输出为|生成(?!的)|保存(?!的)|另存|产出(?!目录|物|文件|结果)|创建|更新|覆盖|"
    r"\b(?:write|writes|writing|save|saves|saving|generate|generates|generating|"
    r"create|creates|creating|produce|produces|producing|update|updates|updating|"
    r"overwrite|overwrites|overwriting)\b|deliverable\w*|output\s+to",
    re.IGNORECASE,
)
_READ_ONLY_PHRASE_STRIP_RE = re.compile(
    r"只读|不修改|不要修改|禁止修改|不得修改|勿修改|不可修改|不写入|不写文件|"
    r"read[\s-]?only|do\s+not\s+modify|don'?t\s+modify|must\s+not\s+(?:modify|change)|"
    r"without\s+modif\w*",
    re.IGNORECASE,
)


def _is_readonly_verification_task_text(text: str) -> bool:
    """True when a delegated task is read-only verification work (audit,
    check, forensics) with no production intent — the heavyweight核验 that
    must never be delegated to code_executor.

    False-positives are avoided twice: the task must carry BOTH a read-only
    marker and a verification noun, and any production signal (write/save/
    generate/update/deliverable) outside the read-only phrases disqualifies
    it (e.g. "核验数据后生成修正版报告并保存" stays a production task).
    """
    t = str(text or "").strip().lower()
    if not t:
        return False
    if not _VERIFICATION_INTENT_RE.search(t):
        return False
    if not _READ_ONLY_INTENT_RE.search(t):
        return False
    residual = _READ_ONLY_PHRASE_STRIP_RE.sub(" ", t)
    return not _PRODUCTION_SIGNAL_RE.search(residual)


def _cycle_is_readonly_verification(tool_results: List[Dict[str, Any]]) -> bool:
    """A whole cycle counts as read-only verification only when every executed
    call is a code_executor whose task text is read-only verification work.
    Mixed or non-code_executor cycles keep the normal execution semantics."""
    saw_code_executor = False
    for item in tool_results or []:
        tool_name = str(item.get("tool_name") or "").strip().lower()
        if tool_name != "code_executor":
            return False
        saw_code_executor = True
        params = item.get("tool_params")
        task_text = str(params.get("task") or "") if isinstance(params, dict) else ""
        if not _is_readonly_verification_task_text(task_text):
            return False
    return saw_code_executor


def _build_readonly_verification_redirect_nudge(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    count: int,
) -> str:
    """Redirect nudge for read-only verification delegations; from the second
    hit on, also clamps the long analysis-prose habit observed in production
    (直接给结论，不复述、不全量打印)."""
    language = _dta().detect_reasoning_language(user_query)
    base = _dta()._localized_text(
        language,
        "禁止用 code_executor 做只读检查/取证/核验/审计。这类工作请改用 document_reader、file_operations，或 execute_code（kernel 内直接 open()+正则即可，秒级完成）。code_executor 只用于需要完整编码 agent 的实现任务。",
        "Do not delegate read-only checks/forensics/verification/audits to code_executor. "
        "Use document_reader, file_operations, or execute_code (open()+regex inside the kernel is enough and takes seconds). "
        "Reserve code_executor for implementation tasks that need a full coding agent.",
    )
    if count >= 2:
        base += _dta()._localized_text(
            language,
            "\n直接给结论，不要复述已读内容，不要全量打印。",
            "\nGive the conclusion directly — do not restate what was read or dump full contents.",
        )
    return base


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
        except (json.JSONDecodeError, TypeError):
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
