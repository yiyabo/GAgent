"""Closing summary and last-resort answers for the DeepThink agent.

God-class split: the like-named DeepThinkAgent methods delegate here with
`self`/`cls` renamed to `agent`. Display-family helpers (detect_reasoning_language,
is_process_only_answer, ...) stay in deep_think_agent and are reached through
the late-bound `_dta()` so their monkeypatch surface is unchanged.

When a run ends without a final answer (iteration cap, run budget, loop guard,
repeated polling), the answer comes from ONE more call on the real conversation
plus a "stop calling tools, write the final reply" nudge, as in Hermes
`handle_max_iterations`. The earlier design rebuilt a few truncated "evidence
snippets" and asked the model to summarize those; a failed plotting tail pushed
all the real results out of the window and the report said so.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.llm import update_usage_context
from app.services.deep_think.models import (
    TaskExecutionContext,
    ThinkingStep,
)
from app.services.deep_think.text_utils import (
    _EXPECT_KIND_LABEL,
    _default_synthesis_max_tokens,
    _default_synthesis_timeout_seconds,
    _strip_runtime_absolute_paths,
    _ensure_inline_images,
)
from app.services.foundation.settings import get_settings
from app.services.execution.llm_observation import ObserverRejected
from app.services.response_style import (
    response_style_instruction,
    sanitize_professional_response_text,
)
from app.services.run_budget import RunDeadlineExceeded, iterate_stage, run_stage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_SUMMARY_NUDGE = {
    "zh": (
        "不要再调用任何工具。请根据上面的全部对话和工具结果，直接给用户写最终答复:"
        "做了什么、结果是什么、哪些步骤失败了以及原因。用自然的文字回答，不要输出 JSON。"
    ),
    "en": (
        "Do not call any more tools. Using the whole conversation and every tool result above, "
        "write the final reply to the user now: what was done, what the results are, and which "
        "steps failed and why. Answer in plain prose, not JSON."
    ),
}


def _collect_tool_usage_counts(cls: Any, steps: List[ThinkingStep]) -> Dict[str, int]:
    c: Counter[str] = Counter()
    for step in steps:
        if not step.action:
            continue
        try:
            payload = json.loads(step.action)
        except Exception:
            continue
        for name in cls._tool_names_from_payload(payload):
            if name and name != "submit_final_answer":
                c[name] += 1
    return dict(c)


def _format_tool_usage_counts(counts: Dict[str, int]) -> str:
    if not counts:
        return ""
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return ", ".join(f"{k}×{v}" for k, v in items)


def _select_steps_for_summary(steps: List[ThinkingStep]) -> List[ThinkingStep]:
    n = len(steps)
    if n <= 5:
        return list(steps)
    idx = sorted({0, 1, n - 3, n - 2, n - 1})
    idx = [i for i in idx if 0 <= i < n]
    return [steps[i] for i in idx]


def _summary_constraints(
    agent: "DeepThinkAgent",
    *,
    language: str,
    task_context: Optional[TaskExecutionContext],
    structured_plan_outcome: Dict[str, Any],
) -> List[str]:
    """Run-specific facts the closing reply must respect (one line each)."""
    zh = language == "zh"
    lines: List[str] = []
    if zh:
        lines.append("只陈述上面的对话和工具结果里真实出现过的内容；没有读到、没有产出的文件或结果，不要声称已经完成。不要提及内部步骤编号。")
        lines.append(
            "引用任何产出文件（清单、正文、图片路径）一律使用会话相对路径"
            "（如 raw_files/...、deliverables/...），禁止输出以 /app/runtime/ 开头的绝对路径。"
        )
    else:
        lines.append(
            "State only what the conversation and tool results above actually show; do not claim a file "
            "was read or produced, or a result obtained, unless a tool result shows it. Do not mention internal step numbers."
        )
        lines.append(
            "Reference every produced file (manifest lists, prose, image paths) with its session-relative "
            "path (e.g. raw_files/..., deliverables/...); never emit absolute paths starting with /app/runtime/."
        )

    produced_images = agent._collect_inline_image_relpaths()
    if produced_images:
        listing = "\n".join(f"  - {p}" for p in produced_images)
        if zh:
            lines.append(
                "本轮已产出的图片文件（相对路径）：\n" + listing +
                "\n  在答案相应位置用 `![描述](相对路径)` 把图片内联展示，不要只在文字里提文件名。"
            )
        else:
            lines.append(
                "Image files produced in this run (relative paths):\n" + listing +
                "\n  Embed each image inline at the relevant position with `![caption](relative-path)`; do not merely name the files."
            )

    acceptance_missing = [m for m in (getattr(agent, "_acceptance_missing", None) or []) if m]
    if acceptance_missing:
        if zh:
            labels = "、".join(_EXPECT_KIND_LABEL.get(m, {}).get("zh", m) for m in acceptance_missing)
            lines.append(f"本轮未能产出的交付物类型：{labels}。必须在答案开头明确说明哪些没有完成以及原因，不得假装全部完成。")
        else:
            labels = ", ".join(_EXPECT_KIND_LABEL.get(m, {}).get("en", m) for m in acceptance_missing)
            lines.append(
                f"Deliverable type(s) NOT produced this run: {labels}. "
                "State this explicitly at the top of the answer with the reason; do not pretend everything completed."
            )

    if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
        detail = str(structured_plan_outcome.get("message") or structured_plan_outcome.get("operation") or "")
        if zh:
            lines.append(
                "本轮请求的计划操作尚未成功完成。按实际回执说明未完成的操作及原因，不要把审核、查询或执行失败说成未创建计划，"
                "也不要把普通 markdown 文本说成已经创建好的计划。" + (f" 回执：{detail}" if detail else "")
            )
        else:
            lines.append(
                "The requested Plan operation is incomplete. Report this actual receipt and reason; do not describe a "
                "failed review/query/execution as failure to create the Plan, and do not present ordinary markdown as "
                "a created plan." + (f" Receipt: {detail}" if detail else "")
            )

    if agent._is_brief_execute_followup():
        lines.append(
            "这是一次简短的执行跟进。优先汇报当前任务结果或最新工具结果，不要回顾先前里程碑或“下一步建议”，除非用户明确要求。"
            if zh else
            "This is a short execution follow-up. Prioritize the current task outcome or latest tool result; "
            "do not recap earlier milestones or next-step menus unless the user asked."
        )

    if agent._is_execute_task_request() and agent._has_bound_task_context(task_context):
        task_id = agent._current_bound_task_id(task_context)
        task_name = str(getattr(task_context, "task_name", "") or "").strip()
        task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
        if task_name:
            task_label = f"{task_label} ({task_name})"
        lines.append(
            f"本请求已绑定 {task_label}。不要要求用户补充任务定义、任务描述，也不要让用户确认父级目录是否存在；"
            "若执行提前结束，如实汇报该任务的真实状态和已观察到的产出。"
            if zh else
            f"This request is already bound to {task_label}. Do NOT ask the user to provide task definitions or "
            "descriptions; if execution stopped early, report the real bound-task state and the observed outputs."
        )
    return lines


def _build_summary_nudge(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    task_context: Optional[TaskExecutionContext],
    structured_plan_outcome: Dict[str, Any],
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    nudge = _SUMMARY_NUDGE["zh" if language == "zh" else "en"]
    constraints = _summary_constraints(
        agent,
        language=language,
        task_context=task_context,
        structured_plan_outcome=structured_plan_outcome,
    )
    if constraints:
        nudge += "\n\n" + "\n".join(f"- {line}" for line in constraints)
    nudge += "\n" + response_style_instruction(
        {"chat_response_preferences": getattr(agent, "_chat_response_preferences", {})}
    )
    return nudge


def _build_bound_execute_task_fallback(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    *,
    user_query: str,
    task_context: Optional[TaskExecutionContext],
) -> str:
    if not (
        agent._is_execute_task_request()
        and agent._has_bound_task_context(task_context)
        and agent._explicit_task_override_active(task_context)
    ):
        return ""
    language = _dta().detect_reasoning_language(user_query)
    task_id = agent._current_bound_task_id(task_context)
    task_name = str(getattr(task_context, "task_name", "") or "").strip()
    task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
    if task_name:
        task_label = f"{task_label} ({task_name})"
    counts = agent._collect_tool_usage_counts(steps)
    stats = agent._format_tool_usage_counts(counts)
    if language == "zh":
        header = f"本轮已经进入已绑定任务执行链{f'（{stats}）' if stats else ''}，但系统在结束前没有成功提交正式最终答案。"
        return "\n".join([
            header,
            f"当前绑定任务：{task_label}。",
            "这不表示缺少任务定义；当前任务上下文已经存在，后续应从当前绑定任务继续执行，而不是再要求用户补任务描述。",
        ]).strip()
    header = (
        f"This run stayed inside a bound execute-task chain{f' ({stats})' if stats else ''}, "
        "but it ended before a formal final answer was submitted."
    )
    return "\n".join([
        header,
        f"Current bound task: {task_label}.",
        "This does not mean the task definition is missing; the task context is already bound and execution should continue from the current task instead of asking the user to provide task details again.",
    ]).strip()


def _produced_file_names(agent: "DeepThinkAgent", limit: int = 6) -> List[str]:
    names: List[str] = []
    for candidate in getattr(agent, "_produced_deliverable_paths", None) or []:
        name = str(candidate or "").strip().rstrip("/").rsplit("/", 1)[-1]
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _build_structured_fallback(agent: "DeepThinkAgent", steps: List[ThinkingStep], user_query: str = "") -> str:
    """Fixed message for when no closing summary could be written.

    Deterministic and short on purpose: it names what happened and lists the
    files the run did produce, but never re-tells results from clipped tool output.
    """
    language = _dta().detect_reasoning_language(user_query)
    structured_plan_outcome = agent._summarize_structured_plan_outcome(
        steps,
        user_query=user_query,
    )
    if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
        return agent._build_structured_plan_contract_failure_answer(
            outcome=structured_plan_outcome,
            user_query=user_query,
        )

    output_files = _produced_file_names(agent)
    if output_files:
        names = "\n".join(f"- {name}" for name in output_files)
        if language == "zh":
            text = (
                "本轮没能写出最终总结，但已生成以下交付文件（完整内容见右侧 Artifacts 面板）：\n"
                f"{names}\n\n"
                "如需我基于这些结果重新总结、调整内容或导出其他格式，请告诉我。"
            )
        else:
            text = (
                "No closing summary could be written for this run, but it produced these files "
                "(see the Artifacts panel for full content):\n"
                f"{names}\n\n"
                "Let me know if you want a fresh summary built on them, changes, or a different export format."
            )
        return _strip_runtime_absolute_paths(
            _ensure_inline_images(text, agent._collect_inline_image_relpaths())
        )

    useful_thoughts: List[str] = []
    for s in reversed(steps):
        if isinstance(s.thought, str) and s.thought.strip() and not _dta().is_process_only_answer(s.thought, user_query=user_query):
            cleaned = s.thought.strip()
            if len(cleaned) > 50:
                useful_thoughts.append(cleaned[:800])
                if len(useful_thoughts) >= 3:
                    break
    useful_thoughts.reverse()
    if useful_thoughts:
        combined = "\n\n".join(useful_thoughts)
        if language == "zh":
            return f"我先给出目前已经收敛出的关键判断：\n\n{combined}"
        return f"Here are the key conclusions that could still be supported:\n\n{combined}"

    if language == "zh":
        return (
            "这一轮检查还不足以支撑可靠结论。"
            "建议进一步缩小范围，或指定要继续核查的对象后再收敛。"
        )
    return (
        "The completed checks were not enough to support a reliable final answer. "
        "This request likely needs a narrower scope or a more specific fact to verify next."
    )


def _synthesis_thinking_enabled() -> bool:
    """The loop's switch (`THINKING_ENABLED`), sent explicitly on synthesis calls too.

    Left unset, the provider default decided (observed 2026-10-09, LOCAL_INFRA
    §109): qwen3.8-flash reasoned through the whole output budget and yielded
    0 visible chars, so forced synthesis and its retries all fell through
    to the structured fallback template.
    """
    return bool(getattr(get_settings(), "thinking_enabled", True))


async def _chat_messages_streaming(
    agent: "DeepThinkAgent",
    messages: List[Dict[str, Any]],
    *,
    max_tokens: int,
    enable_thinking: Optional[bool],
    thinking_budget: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """One completion over ``messages`` with tool calling switched off.

    The loop's own transport is used (native tool calling with ``tool_choice``
    "none" and the same tool list, which keeps the provider's prompt-cache
    prefix and avoids rejected tool-call history without tool definitions).
    ``max_tokens`` and the thinking switch are always sent explicitly (§109).
    """
    from .model_failure import remember_failure, recovered

    client = agent.llm_client
    cancel_event = getattr(agent, "cancel_event", None)
    try:
        native = getattr(client, "stream_chat_with_tools_async", None)
        if callable(native):
            result = await native(
                messages=messages,
                tools=list(tools or []),
                tool_choice="none",
                enable_thinking=enable_thinking,
                thinking_budget=thinking_budget,
                max_tokens=max_tokens,
            )
            text = str(getattr(result, "content", "") or "")
        else:
            stream_fn = getattr(client, "stream_chat_async", None)
            if not callable(stream_fn):
                raise RuntimeError("LLM client cannot stream a closing summary")
            chunks: List[str] = []
            async for chunk in iterate_stage(
                stream_fn(prompt="", messages=messages, max_tokens=max_tokens, enable_thinking=enable_thinking),
                stage="deepthink-synthesis-llm",
                cancel_event=cancel_event,
            ):
                chunks.append(str(chunk))
            text = "".join(chunks)
    except (RunDeadlineExceeded, ObserverRejected):
        raise
    except Exception as exc:
        remember_failure(agent, exc)
        raise
    recovered(agent)
    return text


_MIN_ATTEMPT_SECONDS = 5.0
_FIRST_ATTEMPT_SHARE = 0.6
_DEADLINE_MARGIN_SECONDS = 5.0
# Measured on the test instance: a closing summary over 58k tokens took 154 s
# with thinking on (6k completion tokens at ~40 tokens/s). With less than this
# left in the run budget a thinking summary cannot finish, so it thinks less.
_THINKING_MIN_REMAINING_SECONDS = 480.0


def _budget_remaining() -> Optional[float]:
    from app.services.run_budget import current_run_budget

    budget = current_run_budget()
    return None if budget is None else budget.remaining_seconds()


def _attempt_timeout(cap: float, *, is_last: bool) -> float:
    """Time one closing-summary attempt may take.

    Under a run budget the summary must end before the hard deadline: that
    deadline turns the whole run into a bare "time limit" failure and loses even
    the list of files the run produced. So the first attempt keeps at most 60%
    of what is left (the retry needs time too) and the last stops a few seconds
    early, which lets the fixed fallback message answer instead.
    """
    remaining = _budget_remaining()
    if remaining is None:
        return cap
    share = remaining - _DEADLINE_MARGIN_SECONDS if is_last else remaining * _FIRST_ATTEMPT_SHARE
    return min(cap, share)


def _ensure_acceptance_missing(agent: "DeepThinkAgent") -> None:
    """Record which requested deliverable types never appeared.

    The loop breakers used to do this on their way out; a run that simply hits
    the iteration cap or the run budget now reaches the closing summary without
    them, and the summary must still say what is missing.
    """
    if getattr(agent, "_acceptance_missing", None) or not agent._is_execute_task_request():
        return
    try:
        from .acceptance import acceptance_missing

        missing = acceptance_missing(
            agent,
            getattr(agent, "_expected_outputs_current", None) or [],
            getattr(agent, "_produced_deliverable_paths", None) or [],
            getattr(agent, "_acceptance_spec", None),
        )
    except Exception as exc:
        logger.warning("[DEEP_THINK_NATIVE] Could not check missing deliverables before the closing summary: %r", exc)
        return
    if missing:
        agent._acceptance_missing = list(missing)


async def _forced_synthesis_from_steps(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    user_query: str,
    messages: List[Dict[str, Any]],
    task_context: Optional[TaskExecutionContext] = None,
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    compact: Any = None,
) -> str:
    """The closing summary: one more call over the real ``messages``.

    ``messages`` is not modified: the nudge is added to a copy, so it never
    leaks into the continuation saved for later turns. ``compact`` (an async
    callable) is the loop's own context compaction, applied once before the
    call. Returns "" when no usable answer came back after one retry.
    """
    update_usage_context(call_purpose="deep_think_forced_synthesis", phase="deep_think")
    uq = (user_query or "").strip()
    conversation = list(messages or [])
    if not conversation:
        if not uq:
            return ""
        conversation = [{"role": "user", "content": uq}]

    try:
        if compact is not None:
            try:
                conversation = await compact(conversation)
            except (RunDeadlineExceeded, ObserverRejected):
                raise
            except Exception as exc:
                logger.warning("[DEEP_THINK_NATIVE] Compaction before the closing summary failed: %r", exc)

        _ensure_acceptance_missing(agent)
        structured_plan_outcome = agent._summarize_structured_plan_outcome(steps, user_query=user_query)
        conversation = conversation + [{
            "role": "user",
            "content": _build_summary_nudge(
                agent,
                user_query=user_query,
                task_context=task_context,
                structured_plan_outcome=structured_plan_outcome,
            ),
        }]

        from .native_validation import bounded_synthesis

        attempts = 2
        if bounded_synthesis(agent):
            if getattr(agent, "_synthesis_attempted", False):
                return ""
            agent._synthesis_attempted = True
            attempts = 1

        synthesis_timeout = _default_synthesis_timeout_seconds()
        synthesis_max_tokens = _default_synthesis_max_tokens()
        thinking = _synthesis_thinking_enabled()
        left = _budget_remaining()
        if thinking and left is not None and left < _THINKING_MIN_REMAINING_SECONDS:
            logger.info("[DEEP_THINK_NATIVE] Only %.0fs of run budget left; the closing summary skips thinking", left)
            thinking = False
        for attempt in range(attempts):
            # The retry turns thinking off: a thinking model that spent the whole
            # output cap reasoning returns nothing visible, and asking again the
            # same way repeats that.
            use_thinking = thinking and attempt == 0
            attempt_timeout = _attempt_timeout(synthesis_timeout, is_last=attempt == attempts - 1)
            if attempt_timeout < _MIN_ATTEMPT_SECONDS:
                logger.warning("[DEEP_THINK_NATIVE] No run time left for closing summary attempt %d", attempt + 1)
                break
            logger.info(
                "[DEEP_THINK_NATIVE] Closing summary attempt %d/%d (messages=%d timeout=%.0fs max_tokens=%s thinking=%s)",
                attempt + 1, attempts, len(conversation), attempt_timeout, synthesis_max_tokens, use_thinking,
            )
            try:
                raw = await run_stage(
                    agent._chat_messages_streaming(
                        conversation,
                        max_tokens=synthesis_max_tokens,
                        enable_thinking=use_thinking,
                        thinking_budget=getattr(agent, "thinking_budget", None) if use_thinking else None,
                        tools=tools,
                    ),
                    stage="deepthink-synthesis", timeout=attempt_timeout,
                )
            except (RunDeadlineExceeded, ObserverRejected):
                raise
            except Exception as exc:
                logger.warning("[DEEP_THINK_NATIVE] Closing summary attempt %d failed: %r", attempt + 1, exc)
                continue
            cleaned = sanitize_professional_response_text(str(raw or "").strip())
            if len(cleaned) < 30 or _dta().is_process_only_answer(cleaned, user_query=user_query):
                logger.warning(
                    "[DEEP_THINK_NATIVE] Closing summary attempt %d produced insufficient content (%d chars)",
                    attempt + 1, len(cleaned),
                )
                continue
            if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                return agent._build_structured_plan_contract_failure_answer(
                    outcome=structured_plan_outcome,
                    user_query=user_query,
                )
            cleaned = _strip_runtime_absolute_paths(
                _ensure_inline_images(cleaned, agent._collect_inline_image_relpaths())
            )
            logger.info("[DEEP_THINK_NATIVE] Closing summary succeeded (%d chars)", len(cleaned))
            return cleaned
        return ""
    except (RunDeadlineExceeded, ObserverRejected):
        raise
    except Exception as exc:
        logger.warning("[DEEP_THINK_NATIVE] Closing summary failed: %r", exc, exc_info=True)
        return ""


async def _fallback_answer_from_steps(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    user_query: str = "",
    task_context: Optional[TaskExecutionContext] = None,
) -> str:
    """Last resort when the closing summary produced nothing (no model call)."""
    language = _dta().detect_reasoning_language(user_query)
    if not steps:
        return _dta()._localized_text(
            language,
            "已完成思考，但暂未形成结构化结论。",
            "DeepThink finished without a structured final answer.",
        )
    probe_stop_text = getattr(agent, "_probe_stop_fallback", None)
    if probe_stop_text:
        return probe_stop_text
    from .native_validation import bounded_synthesis

    if bounded_synthesis(agent) and getattr(agent, "_synthesis_attempted", False):
        return _build_structured_fallback(agent, steps, user_query)
    useful = [
        s.thought
        for s in steps
        if isinstance(s.thought, str)
        and s.thought.strip()
        and not _dta().is_process_only_answer(s.thought, user_query=user_query)
    ]
    if useful and not agent._is_execute_task_request():
        return sanitize_professional_response_text(
            _dta().sanitize_reasoning_text(
                useful[-1].strip(),
                language=language,
                max_chars=180,
            )
            or useful[-1].strip()
        )
    bound_execute_fallback = agent._build_bound_execute_task_fallback(
        steps,
        user_query=user_query,
        task_context=task_context,
    )
    if bound_execute_fallback:
        return bound_execute_fallback
    return agent._build_structured_fallback(steps, user_query)


async def _generate_summary(agent: "DeepThinkAgent", steps: List[ThinkingStep], user_query: str) -> str:
    """Build a concise DeepThink summary from visible steps (no LLM call).

    Previous implementation made an extra LLM API call here, which added
    1-10 seconds of latency *after* the final answer was already streamed
    to the user, delaying the ``final`` SSE event and keeping the UI in a
    "still thinking" state.  The frontend ``getProcessSummary`` already has
    a fallback that builds the summary from step display texts, so an LLM
    call is unnecessary.
    """
    language = _dta().detect_reasoning_language(user_query)
    selected = agent._select_steps_for_summary(steps)
    labels: List[str] = []
    for step in selected:
        visible = _dta().build_user_visible_step(step, language=language)
        display_text = str(visible.get("display_text") or "").strip()
        if display_text:
            labels.append(display_text)
    if len(labels) > 1:
        return " → ".join(labels[:3])
    if labels:
        return labels[0]
    return _dta()._default_deepthink_summary(user_query)
