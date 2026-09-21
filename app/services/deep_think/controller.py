"""Loop bodies for the DeepThink agent (god-class split, behaviour zero-change).

`think`, `_think_native` and `_think_prompt_based` move here verbatim with
`self` renamed to `agent`; DeepThinkAgent keeps thin async wrappers. Every
helper the loops call stays reachable through agent._x(...) wrappers (loop
guards, dispatch, gating, prompts, synthesis, protocol, and the agent-UI
glue that still lives in deep_think_agent), so subclass overrides keep
working. Display-family helpers, the LLM-error trio
(_describe_exception/_classify_llm_provider_error/_build_llm_unavailable_final_answer)
and the env knobs (e.g. _default_max_consecutive_llm_failures) are resolved
through the late-bound `_dta()` so their monkeypatch surface is unchanged.
Function-local lazy imports (async_tool_executor, context_manager, and the
in-loop UnifiedToolExecutor re-import) stay in place verbatim.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.llm import stream_chat_collect_async, update_usage_context
from app.services.deep_think.models import (
    DeepThinkProtocolError,
    DeepThinkResult,
    TaskExecutionContext,
    ThinkingStep,
)
from app.services.deep_think.text_utils import (
    _derive_expected_outputs,
    _ensure_inline_images,
    _missing_expectations,
)
from app.services.execution.tool_executor import UnifiedToolExecutor
from app.services.foundation.settings import get_settings
from app.services.response_style import sanitize_professional_response_text
from app.services.tool_schemas import build_tool_schemas

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


async def think(
    agent: "DeepThinkAgent",
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    task_context: Optional[TaskExecutionContext] = None,
) -> DeepThinkResult:
    """
    Executes the deep thinking loop with streaming output.

    Automatically uses native tool calling when the LLM client supports it,
    falling back to prompt-based JSON parsing otherwise.
    """
    if not user_query or not user_query.strip():
        raise ValueError("User query cannot be empty")
    max_user_query_chars = int(
        getattr(get_settings(), "deep_think_max_user_query_chars", 100000)
    )
    if len(user_query) > max_user_query_chars:
        raise ValueError(
            f"User query too long (max {max_user_query_chars} chars)"
        )

    if agent._supports_native_tools():
        logger.info("[DEEP_THINK] Using native tool calling path")
        return await agent._think_native(user_query.strip(), context, task_context)

    return await agent._think_prompt_based(user_query.strip(), context, task_context)


async def _think_native(
    agent: "DeepThinkAgent",
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    task_context: Optional[TaskExecutionContext] = None,
) -> DeepThinkResult:
    from app.services.execution.async_tool_executor import (
        PendingToolCall,
        classify_tool_concurrency,
        execute_with_concurrency,
    )

    from app.services.context.context_manager import (
        ContextWindowManager,
        build_summarization_prompt,
    )

    context = dict(context or {})
    thinking_steps: List[ThinkingStep] = []
    tools_used: List[str] = []
    tool_schemas = build_tool_schemas(agent.available_tools)

    system_prompt = agent._build_native_system_prompt(context, task_context)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]

    llm_model = (
        getattr(agent.llm_client, "model", "")
        or getattr(getattr(agent.llm_client, "client", None), "model", "")
        or ""
    )
    try:
        _ctx_budget = int(os.getenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", "32000") or "32000")
    except (TypeError, ValueError):
        _ctx_budget = 32000
    ctx_mgr = ContextWindowManager(model=llm_model, budget_tokens=max(0, _ctx_budget) or None)

    async def _summarize_for_compaction(text: str) -> str:
        prompt = build_summarization_prompt(text)
        result = await stream_chat_collect_async(agent.llm_client, prompt)
        return str(result or "").strip()

    iteration = 0
    final_answer = ""
    fallback_used = False
    confidence = 0.0
    last_tool_cycle_signature: Optional[str] = None
    identical_tool_cycle_count = 0
    probe_only_execution_cycles = 0
    forced_probe_followthrough_attempts = 0
    forced_handoff_followthrough_attempts = 0
    had_real_execution_tool = False
    partial_completion_retry_count = 0
    _MAX_PARTIAL_RETRIES = 3
    _MAX_HANDOFF_ITERATION_EXTENSIONS = 4
    last_real_execution_tool_results: List[Dict[str, Any]] = []
    force_verified_execution_finalization = False
    pending_handoff_task_id: Optional[int] = None
    pending_handoff_previous_task_id: Optional[int] = None
    structured_plan_finalize_nudge_plan_id: Optional[int] = None
    runtime_iteration_limit = agent.max_iterations
    handoff_iteration_extensions = 0
    consecutive_llm_failures = 0
    max_consecutive_llm_failures = _dta()._default_max_consecutive_llm_failures()
    llm_fatal_abort = False
    loop_guard_state: Dict[str, Any] = {
        "verified_deliverables": [],
        "failure_sig_counts": {},
        "failure_sig_warned": set(),
        "last_progress_iteration": 0,
        "no_progress_nudge_sent": False,
        "time_nudge_sent": False,
        "started_at": time.monotonic(),
        "expected_outputs": _derive_expected_outputs(user_query),
    }
    agent._produced_deliverable_paths = []
    agent._acceptance_missing: List[str] = []
    agent._expected_outputs_current = list(loop_guard_state["expected_outputs"])
    if loop_guard_state["expected_outputs"]:
        logger.info(
            "[DEEP_THINK][acceptance] expected deliverable types: %s",
            ",".join(loop_guard_state["expected_outputs"]),
        )

    logger.info("[DEEP_THINK_NATIVE] Starting for: %s", user_query[:50])

    while iteration < runtime_iteration_limit:
        await agent._get_pause_event().wait()
        if agent.cancel_event and agent.cancel_event.is_set():
            logger.info("[DEEP_THINK_NATIVE] Cancelled by user")
            break

        if agent.steer_drain:
            steers = agent.steer_drain()
            for steer_text in steers:
                messages.append({
                    "role": "user",
                    "content": f"[User mid-run guidance]: {steer_text}",
                })
                logger.info(
                    "[DEEP_THINK_NATIVE] Injected user steer at iteration %d: %s",
                    iteration + 1,
                    steer_text[:120],
                )
                if agent.on_steer_ack:
                    await agent._safe_generic_callback(
                        agent.on_steer_ack, steer_text, iteration + 1
                    )

        messages = await ctx_mgr.compact_if_needed(
            messages, summarizer=_summarize_for_compaction,
        )

        iteration += 1
        current_step = ThinkingStep(
            iteration=iteration,
            thought="",
            action=None,
            action_result=None,
            self_correction=None,
            timestamp=datetime.now(),
            status="thinking",
        )
        if agent.on_thinking:
            await agent._safe_callback(current_step)

        try:
            async def _on_delta(chunk: str) -> None:
                if agent.on_thinking_delta:
                    await agent._safe_delta_callback(iteration, chunk)

            async def _on_reasoning_delta(chunk: str) -> None:
                if agent.on_reasoning_delta:
                    try:
                        ret = agent.on_reasoning_delta(iteration, chunk)
                        if asyncio.iscoroutine(ret):
                            await ret
                    except Exception:
                        pass

            update_usage_context(call_purpose="deep_think_iteration", phase="deep_think", tool_name="deep_think")
            result = await agent.llm_client.stream_chat_with_tools_async(
                messages=messages,
                tools=tool_schemas,
                tool_choice="auto",
                on_content_delta=_on_delta,
                on_reasoning_delta=_on_reasoning_delta,
                enable_thinking=agent.enable_thinking,
                thinking_budget=agent.thinking_budget,
            )
        except Exception as exc:
            error_detail = _dta()._describe_exception(exc)
            logger.exception(
                "[DEEP_THINK_NATIVE] LLM call failed at iteration %d: %s",
                iteration,
                error_detail,
            )
            current_step.status = "error"
            current_step.thought = f"Error: {error_detail}"
            current_step.finished_at = datetime.now()
            thinking_steps.append(current_step)
            if agent.on_thinking:
                await agent._safe_callback(current_step)
            classified = _dta()._classify_llm_provider_error(exc)
            if classified is not None and not classified.retryable:
                logger.error(
                    "[DEEP_THINK_NATIVE] Non-retryable LLM provider error (%s); aborting run",
                    getattr(classified, "error_code", "unknown"),
                )
                consecutive_llm_failures = max_consecutive_llm_failures
            else:
                consecutive_llm_failures += 1
            if consecutive_llm_failures >= max_consecutive_llm_failures:
                final_answer = _dta()._build_llm_unavailable_final_answer(classified)
                fallback_used = True
                llm_fatal_abort = True
                logger.error(
                    "[DEEP_THINK_NATIVE] Circuit breaker tripped after %d consecutive LLM failure(s); aborting run",
                    consecutive_llm_failures,
                )
                break
            continue

        consecutive_llm_failures = 0
        current_step.thought = result.content or ""

        if result.tool_calls:
            tool_calls = list(result.tool_calls)
            final_call = next((tc for tc in tool_calls if tc.name == "submit_final_answer"), None)
            executable_calls = [tc for tc in tool_calls if tc.name != "submit_final_answer"]
            replacement_task_id = agent._verification_only_cycle_replacement_task_id(
                executable_calls,
                task_context=task_context,
                had_real_execution_tool=had_real_execution_tool,
            )
            if replacement_task_id is not None:
                template_call = executable_calls[0]
                executable_calls = [
                    type(template_call)(
                        id=str(getattr(template_call, "id", "") or f"native_{iteration}_rerun_task"),
                        name="rerun_task",
                        arguments={"task_id": replacement_task_id},
                    )
                ]
                logger.info(
                    "[DEEP_THINK_NATIVE] Replaced verify_task-only cycle with rerun_task for task_id=%s at iteration=%s",
                    replacement_task_id,
                    iteration,
                )
                current_step.self_correction = (
                    f"Rejected verification-only follow-up for bound Task {replacement_task_id} and replaced it with rerun_task."
                )
            if force_verified_execution_finalization and executable_calls:
                allowed_summary_calls = [
                    tc
                    for tc in executable_calls
                    if str(getattr(tc, "name", "") or "").strip().lower() == "result_interpreter"
                ]
                skipped_names = [
                    str(getattr(tc, "name", "") or "").strip() or "<unknown>"
                    for tc in executable_calls
                    if tc not in allowed_summary_calls
                ]
                if skipped_names:
                    logger.info(
                        "[DEEP_THINK_NATIVE] Skipping post-success exploratory tool calls: %s",
                        ",".join(skipped_names),
                    )
                    current_step.self_correction = (
                        "Skipped non-summary tool calls after verified task completion and forced finalization mode."
                    )
                    executable_calls = allowed_summary_calls
                    if not executable_calls and final_call is None:
                        current_step.status = "analyzing"
                        current_step.finished_at = datetime.now()
                        thinking_steps.append(current_step)
                        if agent.on_thinking:
                            await agent._safe_callback(current_step)
                        messages.append({"role": "assistant", "content": result.content or ""})
                        messages.append(
                            {
                                "role": "user",
                                "content": agent._build_verified_execution_finalize_nudge(
                                    task_context=task_context,
                                    user_query=user_query,
                                ),
                            }
                        )
                        continue

            if executable_calls:
                bound_task_before_cycle = agent._current_bound_task_id(task_context)
                action_payload = {
                    "tools": [
                        {
                            "tool": tc.name,
                            "params": tc.arguments,
                            "tool_call_id": tc.id or f"native_{iteration}_{idx}",
                        }
                        for idx, tc in enumerate(executable_calls)
                    ]
                }
                current_step.action = json.dumps(action_payload, ensure_ascii=False)
                current_step.status = "calling_tool"
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)

                pending = []
                for idx, tc in enumerate(executable_calls):
                    name = str(getattr(tc, "name", "") or "")
                    pending.append(PendingToolCall(
                        index=idx,
                        tool_name=name,
                        coroutine_factory=lambda _tc=tc, _idx=idx: agent._execute_native_tool_call(
                            tc=_tc, iteration=iteration, index=_idx,
                        ),
                        is_concurrent_safe=classify_tool_concurrency(name),
                    ))
                tool_results = await execute_with_concurrency(pending)

                for item in tool_results:
                    tool_name = str(item.get("tool_name") or "")
                    if tool_name and tool_name not in tools_used:
                        tools_used.append(tool_name)

                agent._append_tool_cycle_messages(
                    messages=messages,
                    tool_results=tool_results,
                    assistant_content=result.content or "",
                    current_step=current_step,
                )

                loop_guard_break_reason = agent._apply_loop_guards(
                    messages=messages,
                    tool_results=tool_results,
                    iteration=iteration,
                    guard_state=loop_guard_state,
                )
                if loop_guard_break_reason:
                    current_step.self_correction = loop_guard_break_reason
                    logger.warning(
                        "[DEEP_THINK][loop-guard] break at iteration=%s: %s",
                        iteration,
                        loop_guard_break_reason,
                    )
                    break

                if final_call is None:
                    created_plan = agent._extract_successful_created_plan_from_tool_results(
                        tool_results
                    )
                    created_plan_id = (
                        int(created_plan["plan_id"])
                        if isinstance(created_plan, dict)
                        and created_plan.get("plan_id") is not None
                        else None
                    )
                    if (
                        created_plan_id is not None
                        and structured_plan_finalize_nudge_plan_id != created_plan_id
                    ):
                        plan_title = (
                            created_plan.get("plan_title")
                            if isinstance(created_plan, dict)
                            else None
                        )
                        if agent._plan_contract_flags()["execute_after_create_required"]:
                            nudge = agent._build_created_plan_execute_nudge(
                                user_query=user_query,
                                plan_id=created_plan_id,
                                plan_title=plan_title,
                            )
                        else:
                            nudge = agent._build_created_plan_finalize_nudge(
                                user_query=user_query,
                                plan_id=created_plan_id,
                                plan_title=plan_title,
                            )
                        messages.append(
                            {
                                "role": "user",
                                "content": nudge,
                            }
                        )
                        structured_plan_finalize_nudge_plan_id = created_plan_id
                        logger.info(
                            "[DEEP_THINK_NATIVE] Injected finalize nudge after successful plan creation: plan_id=%s",
                            created_plan_id,
                        )

                tool_cycle_signature = agent._build_tool_cycle_signature(tool_results)
                if tool_cycle_signature and tool_cycle_signature == last_tool_cycle_signature:
                    identical_tool_cycle_count += 1
                    if identical_tool_cycle_count == 1:
                        correction_nudge = agent._build_tool_failure_correction_nudge(tool_results)
                        if correction_nudge:
                            messages.append({"role": "user", "content": correction_nudge})
                            logger.info(
                                "[DEEP_THINK_NATIVE] Injected correction nudge after repeated tool failure"
                            )
                else:
                    last_tool_cycle_signature = tool_cycle_signature
                    identical_tool_cycle_count = 0

                if identical_tool_cycle_count >= agent.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                    repeated_cycles = identical_tool_cycle_count + 1
                    rep_missing = _missing_expectations(
                        loop_guard_state.get("expected_outputs") or [],
                        loop_guard_state.get("verified_deliverables") or [],
                    )
                    if rep_missing:
                        loop_guard_state["missing_expectations"] = rep_missing
                        agent._acceptance_missing = list(rep_missing)
                        logger.warning(
                            "[DEEP_THINK][acceptance] identical-cycle stop with missing deliverable types: %s",
                            ",".join(rep_missing),
                        )
                    current_step.status = "done"
                    current_step.self_correction = (
                        "Stopped repeated identical tool polling to avoid an unproductive loop."
                    )
                    if agent.on_thinking:
                        await agent._safe_callback(current_step)
                    final_answer = agent._build_repetition_stop_answer(
                        tool_results=tool_results,
                        repeated_cycles=repeated_cycles,
                    )
                    if rep_missing:
                        final_answer += (
                            "\n\nNote: the requested deliverable type(s) are still missing: "
                            + ", ".join(rep_missing)
                            + "."
                        )
                    confidence = max(
                        confidence,
                        0.75 if agent._contains_tool(tool_results, "phagescope") else 0.5,
                    )
                    logger.warning(
                        "[DEEP_THINK_NATIVE] Stopped repeated tool loop at iteration=%s repeated_cycles=%s tools=%s",
                        iteration,
                        repeated_cycles,
                        ",".join(
                            sorted(
                                {
                                    str(item.get("tool_name") or "")
                                    for item in tool_results
                                    if item.get("tool_name")
                                }
                            )
                        ),
                    )
                    if agent.on_final_delta and final_answer:
                        await agent._stream_final_answer(final_answer)
                    break

                is_probe_only_cycle = agent._is_probe_only_execution_cycle(
                    tool_results,
                    task_context=task_context,
                )
                if is_probe_only_cycle:
                    probe_only_execution_cycles += 1
                    probe_limit = 6 if had_real_execution_tool else 12
                    if (
                        not had_real_execution_tool
                        and probe_only_execution_cycles >= 2
                        and forced_probe_followthrough_attempts < 1
                        and agent._can_force_probe_followthrough_execution(task_context)
                    ):
                        forced_probe_followthrough_attempts += 1
                        forced_result = await agent._execute_forced_probe_followthrough(
                            task_context=task_context,
                            user_query=user_query,
                            iteration=iteration,
                            probe_only_execution_cycles=probe_only_execution_cycles,
                        )
                        forced_tool_name = str(forced_result.get("tool_name") or "")
                        if forced_tool_name and forced_tool_name not in tools_used:
                            tools_used.append(forced_tool_name)
                        agent._append_tool_cycle_messages(
                            messages=messages,
                            tool_results=[forced_result],
                            assistant_content="",
                            current_step=current_step,
                        )
                        tool_results = [forced_result]
                        last_tool_cycle_signature = agent._build_tool_cycle_signature(tool_results)
                        identical_tool_cycle_count = 0
                        is_probe_only_cycle = agent._is_probe_only_execution_cycle(
                            tool_results,
                            task_context=task_context,
                        )
                        if not is_probe_only_cycle:
                            probe_only_execution_cycles = 0
                            current_step.self_correction = (
                                "Detected repeated observation-only probing despite available upstream artifacts; "
                                "forced code_executor execution."
                            )
                if is_probe_only_cycle:
                    if probe_only_execution_cycles >= probe_limit:
                        # Hard stop for infinite observation loops — always active regardless of
                        # had_real_execution_tool. Without this, a post-execution AI that keeps
                        # reading non-existent files would silently burn through max_iterations.
                        current_step.status = "done"
                        if had_real_execution_tool:
                            # Task was executed; post-execution probing exceeded limit.
                            # Do NOT return BLOCKED_DEPENDENCY — we know the task ran.
                            # The AI never submitted submit_final_answer, so final_answer
                            # is likely empty here.  Return a neutral completion notice.
                            current_step.self_correction = (
                                "Stopped repeated post-execution observation-only probing."
                            )
                            if not final_answer:
                                raw_fallback = agent._build_post_execution_probe_stop_answer(
                                    task_context=task_context,
                                    user_query=user_query,
                                    steps=[*thinking_steps, current_step],
                                    tool_results=last_real_execution_tool_results,
                                )
                                # Try to synthesize a clean answer via LLM instead of
                                # dumping raw evidence snippets to the user.
                                try:
                                    synthesized = await agent._generate_fallback_from_evidence(
                                        user_query=user_query,
                                        evidence_snippets=raw_fallback,
                                        steps=[*thinking_steps, current_step],
                                        task_context=task_context,
                                        max_retries=2,
                                        timeout=120,
                                        max_tokens=4000,
                                    )
                                    if len(synthesized) >= 100 or len(synthesized) >= len(raw_fallback) // 2:
                                        final_answer = synthesized
                                    else:
                                        final_answer = raw_fallback
                                except Exception as synth_exc:
                                    logger.warning(
                                        "Post-execution probe-stop LLM synthesis failed, using raw fallback: %s",
                                        str(synth_exc)[:200],
                                    )
                                    final_answer = raw_fallback
                        else:
                            if agent._explicit_task_override_active(task_context):
                                # For explicit task override, do NOT return BLOCKED_DEPENDENCY.
                                # The user explicitly requested this task — give a neutral
                                # status instead of demanding manual prerequisite work.
                                current_step.self_correction = (
                                    "Stopped observation-only probing for explicit task override; "
                                    "returning execution status instead of blocked-dependency."
                                )
                                task_label = ""
                                if task_context and task_context.task_id is not None:
                                    task_label = f"Task {task_context.task_id}"
                                    if task_context.task_name:
                                        task_label = f"{task_label} ({task_context.task_name})"
                                final_answer = (
                                    f"{task_label or 'The bound task'} 的执行尝试未能产生预期输出。"
                                    "已尝试自动执行但未成功完成，请检查任务指令和上游数据是否就绪，然后重试。"
                                )
                            else:
                                current_step.self_correction = (
                                    "Stopped repeated observation-only probing and returned a blocked-dependency conclusion."
                                )
                                final_answer = agent._build_blocked_dependency_answer(
                                    task_context=task_context,
                                    user_query=user_query,
                                    tool_results=tool_results,
                                )
                        confidence = max(confidence, 0.8)
                        logger.warning(
                            "[DEEP_THINK_NATIVE] Stopped after %s consecutive probe-only execution cycles at iteration=%s had_real_execution_tool=%s",
                            probe_only_execution_cycles,
                            iteration,
                            had_real_execution_tool,
                        )
                        if agent.on_thinking:
                            await agent._safe_callback(current_step)
                        if agent.on_final_delta and final_answer:
                            await agent._stream_final_answer(final_answer)
                        break

                    if not had_real_execution_tool:
                        nudge = agent._build_probe_only_followthrough_nudge(
                            task_context=task_context,
                            user_query=user_query,
                            stage=probe_only_execution_cycles,
                        )
                        messages.append({"role": "user", "content": nudge})
                        logger.info(
                            "[DEEP_THINK_NATIVE] Injected execute followthrough nudge after probe-only cycle=%s at iteration=%s",
                            probe_only_execution_cycles,
                            iteration,
                        )
                        current_step.self_correction = (
                            "Detected observation-only exploration during a bound execute_task request; injected a followthrough nudge."
                        )
                    else:
                        nudge = agent._build_post_execution_summary_nudge(
                            task_context=task_context,
                            user_query=user_query,
                            stage=probe_only_execution_cycles,
                        )
                        messages.append({"role": "user", "content": nudge})
                        logger.info(
                            "[DEEP_THINK_NATIVE] Injected post-execution summary nudge after probe-only cycle=%s at iteration=%s",
                            probe_only_execution_cycles,
                            iteration,
                        )
                        current_step.self_correction = (
                            "Detected post-execution observation-only probing; injected a summary nudge."
                        )
                else:
                    probe_only_execution_cycles = 0
                    # Only mark had_real_execution_tool when a code-running
                    # tool actually executed.  Coordination tools like
                    # plan_operation still reset the probe counter (they ARE
                    # a deliberate action, not passive observation) but must
                    # NOT set the flag — otherwise the hard-stop emits a
                    # misleading "task code executed" message when no code
                    # was ever run.
                    if any(agent._tool_counts_as_real_execution(item) for item in tool_results):
                        had_real_execution_tool = True
                        last_real_execution_tool_results = [
                            item for item in tool_results if agent._tool_counts_as_real_execution(item)
                        ]
                        executed_pending_handoff = (
                            pending_handoff_task_id is not None
                            and bound_task_before_cycle == pending_handoff_task_id
                        )
                        bound_task_after_cycle = agent._current_bound_task_id(task_context)
                        if (
                            bound_task_before_cycle is not None
                            and bound_task_after_cycle is not None
                            and bound_task_after_cycle != bound_task_before_cycle
                        ):
                            pending_handoff_previous_task_id = bound_task_before_cycle
                            pending_handoff_task_id = bound_task_after_cycle
                            forced_handoff_followthrough_attempts = 0
                            had_real_execution_tool = False
                            last_real_execution_tool_results = []
                            probe_only_execution_cycles = 0
                            partial_completion_retry_count = 0
                            messages.append(
                                {
                                    "role": "user",
                                    "content": agent._build_task_handoff_execution_nudge(
                                        task_context=task_context,
                                        user_query=user_query,
                                        previous_task_id=bound_task_before_cycle,
                                        next_task_id=bound_task_after_cycle,
                                    ),
                                }
                            )
                            if (
                                handoff_iteration_extensions < _MAX_HANDOFF_ITERATION_EXTENSIONS
                                and iteration >= runtime_iteration_limit - 1
                            ):
                                runtime_iteration_limit += 1
                                handoff_iteration_extensions += 1
                                logger.info(
                                    "[DEEP_THINK_NATIVE] Extended iteration budget after task handoff previous=%s next=%s new_limit=%s extension=%s",
                                    bound_task_before_cycle,
                                    bound_task_after_cycle,
                                    runtime_iteration_limit,
                                    handoff_iteration_extensions,
                                )
                            logger.info(
                                "[DEEP_THINK_NATIVE] Detected task handoff from %s to %s at iteration=%s; injected execute-next-task nudge",
                                bound_task_before_cycle,
                                bound_task_after_cycle,
                                iteration,
                            )
                            current_step.self_correction = (
                                f"Detected task handoff from {bound_task_before_cycle} to "
                                f"{bound_task_after_cycle}; injected an execute-next-task nudge."
                            )
                            # Skip partial-completion / finalization checks this
                            # iteration — the handoff target hasn't been executed
                            # yet and finalization would prematurely end the run.
                            current_step.status = "analyzing"
                            if agent.on_thinking:
                                await agent._safe_callback(current_step)
                            continue
                        elif executed_pending_handoff:
                            pending_handoff_task_id = None
                            pending_handoff_previous_task_id = None
                            forced_handoff_followthrough_attempts = 0

                    # --- Partial completion retry ---
                    partial_info = agent._detect_partial_completion_in_tool_results(tool_results)
                    if (
                        partial_info
                        and partial_completion_retry_count < _MAX_PARTIAL_RETRIES
                        and agent._current_bound_task_id(task_context) == bound_task_before_cycle
                    ):
                        partial_completion_retry_count += 1
                        nudge = agent._build_partial_completion_retry_nudge(
                            partial_info,
                            task_context=task_context,
                            user_query=user_query,
                            retry_count=partial_completion_retry_count,
                        )
                        messages.append({"role": "user", "content": nudge})
                        logger.info(
                            "[DEEP_THINK_NATIVE] Partial completion retry nudge: ratio=%s retry=%d iter=%d",
                            partial_info.get("partial_ratio"),
                            partial_completion_retry_count,
                            iteration,
                        )
                        current_step.self_correction = (
                            f"Detected partial completion ({partial_info.get('partial_ratio', '?/?')}); "
                            f"injected retry nudge #{partial_completion_retry_count}."
                        )
                    elif agent._should_force_verified_execution_finalization(
                        task_context=task_context,
                        tool_results=tool_results,
                        had_real_execution_tool=had_real_execution_tool,
                    ):
                        force_verified_execution_finalization = True
                        messages.append(
                            {
                                "role": "user",
                                "content": agent._build_verified_execution_finalize_nudge(
                                    task_context=task_context,
                                    user_query=user_query,
                                ),
                            }
                        )
                        logger.info(
                            "[DEEP_THINK_NATIVE] Entered verified-execution finalization mode at iteration=%s",
                            iteration,
                        )
                        current_step.self_correction = (
                            "Detected verified task completion with no remaining pending tasks; "
                            "injected a finalization-only nudge."
                        )

                current_step.status = "analyzing"
                if agent.on_thinking:
                    await agent._safe_callback(current_step)
                continue

            if final_call:
                candidate_answer = str(final_call.arguments.get("answer", "") or "")
                raw_conf = final_call.arguments.get("confidence", 0.8)
                try:
                    confidence = max(0.0, min(1.0, float(raw_conf)))
                except (TypeError, ValueError):
                    confidence = 0.8
                current_step.status = "done"
                current_step.finished_at = datetime.now()
                if (
                    probe_only_execution_cycles >= 2
                    and not had_real_execution_tool
                    and agent._is_execute_task_request()
                    and agent._has_bound_task_context(task_context)
                    and not agent._looks_like_blocked_dependency_answer(candidate_answer)
                    # Do not replace with BLOCKED_DEPENDENCY when the user
                    # explicitly requested this task — forced execution
                    # should have run or the LLM's natural answer is
                    # preferable to a generic "please provide prerequisites"
                    # message that the user has already complained about.
                    and not agent._explicit_task_override_active(task_context)
                ):
                    current_step.self_correction = (
                        "Rejected a conclusion after repeated observation-only probing and replaced it with a blocked-dependency answer."
                    )
                    final_answer = agent._build_blocked_dependency_answer(
                        task_context=task_context,
                        user_query=user_query,
                        tool_results=[],
                    )
                    if agent.on_final_delta and final_answer:
                        await agent._stream_final_answer(final_answer)
                    thinking_steps.append(current_step)
                    if agent.on_thinking:
                        await agent._safe_callback(current_step)
                    break
                if not agent._is_valid_final_answer(candidate_answer, user_query=user_query):
                    current_step.self_correction = (
                        "Discarded a process-only conclusion and switching to fallback synthesis."
                    )
                    final_answer = ""
                else:
                    structured_plan_outcome = agent._summarize_structured_plan_outcome(
                        thinking_steps,
                        user_query=user_query,
                    )
                    if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                        current_step.self_correction = (
                            "Rejected the final answer because the required structured plan was not created or updated yet."
                        )
                        final_answer = ""
                        thinking_steps.append(current_step)
                        if agent.on_thinking:
                            await agent._safe_callback(current_step)
                        messages.append({"role": "assistant", "content": result.content or ""})
                        messages.append(
                            {
                                "role": "user",
                                "content": agent._get_structured_plan_retry_prompt(),
                            }
                        )
                        continue
                    profile_path = agent._needs_directory_profile_before_final(
                        user_query=user_query,
                        steps=thinking_steps,
                    )
                    if profile_path:
                        current_step.self_correction = (
                            "Rejected a directory/dataset final answer until file_operations profile evidence is collected."
                        )
                        final_answer = ""
                        thinking_steps.append(current_step)
                        if agent.on_thinking:
                            await agent._safe_callback(current_step)
                        forced_call = SimpleNamespace(
                            name="file_operations",
                            id=f"forced_directory_profile_{iteration}",
                            arguments={"operation": "profile", "path": profile_path},
                        )
                        forced_result = await agent._execute_native_tool_call(
                            tc=forced_call,
                            iteration=iteration,
                            index=9998,
                        )
                        if "file_operations" not in tools_used:
                            tools_used.append("file_operations")
                        forced_step = ThinkingStep(
                            iteration=iteration,
                            thought="Collecting required directory profile evidence before final answer.",
                            action=json.dumps(
                                {
                                    "tool": "file_operations",
                                    "params": {"operation": "profile", "path": profile_path},
                                },
                                ensure_ascii=False,
                            ),
                            action_result=None,
                            self_correction="Forced directory profile/census evidence for dataset-level analysis.",
                            kind="tool",
                            status="calling_tool",
                        )
                        thinking_steps.append(forced_step)
                        agent._append_tool_cycle_messages(
                            messages=messages,
                            tool_results=[forced_result],
                            assistant_content="",
                            current_step=forced_step,
                        )
                        forced_step.status = "analyzing"
                        if agent.on_thinking:
                            await agent._safe_callback(forced_step)
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "A directory-level file_operations profile was required and has now been collected. "
                                    "Use its evidence_scope/status_counts/status_count_sources in the final answer. "
                                    "Do not make all/every/global-ready claims unless the profile supports them. "
                                    "Now call submit_final_answer with a scoped dataset summary."
                                ),
                            }
                        )
                        continue
                    phagescope_path = agent._needs_phagescope_deep_profile_before_final(
                        user_query=user_query,
                        steps=thinking_steps,
                    )
                    if phagescope_path:
                        current_step.self_correction = (
                            "Rejected a PhageScope dataset final answer until phagescope_research deep_profile evidence is collected."
                        )
                        final_answer = ""
                        thinking_steps.append(current_step)
                        if agent.on_thinking:
                            await agent._safe_callback(current_step)
                        forced_call = SimpleNamespace(
                            name="phagescope_research",
                            id=f"forced_phagescope_deep_profile_{iteration}",
                            arguments={"action": "deep_profile", "data_dir": phagescope_path},
                        )
                        forced_result = await agent._execute_native_tool_call(
                            tc=forced_call,
                            iteration=iteration,
                            index=9997,
                        )
                        if "phagescope_research" not in tools_used:
                            tools_used.append("phagescope_research")
                        forced_step = ThinkingStep(
                            iteration=iteration,
                            thought="Collecting required PhageScope deep profile evidence before final answer.",
                            action=json.dumps(
                                {
                                    "tool": "phagescope_research",
                                    "params": {"action": "deep_profile", "data_dir": phagescope_path},
                                },
                                ensure_ascii=False,
                            ),
                            action_result=None,
                            self_correction="Forced PhageScope deep_profile evidence for dataset-level analysis.",
                            kind="tool",
                            status="calling_tool",
                        )
                        thinking_steps.append(forced_step)
                        agent._append_tool_cycle_messages(
                            messages=messages,
                            tool_results=[forced_result],
                            assistant_content="",
                            current_step=forced_step,
                        )
                        forced_step.status = "analyzing"
                        if agent.on_thinking:
                            await agent._safe_callback(forced_step)
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "A PhageScope deep_profile was required and has now been collected. "
                                    "Use metadata_size_bytes/metadata_size_human for meta_data size claims, "
                                    "total_size_bytes/total_size_human for whole-dataset size claims, and "
                                    "metadata_schema/ml_metadata_table/label_quality/split_readiness for readiness claims. "
                                    "Do not invent numeric size, row, column, or data-readiness claims absent from deep_profile. "
                                    "Now call submit_final_answer with an evidence-bound PhageScope dataset summary."
                                ),
                            }
                        )
                        continue
                    final_answer = candidate_answer
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)
                if agent.on_final_delta and final_answer:
                    await agent._stream_final_answer(final_answer)
                break
        else:
            # No tool calls – pure thinking text.
            # Try to parse structured JSON actions from content as compatibility fallback.
            parsed_actions = agent._try_parse_structured_actions(result.content or "")
            if parsed_actions:
                logger.info(
                    "[DEEP_THINK_NATIVE] Parsed %d structured actions from text fallback",
                    len(parsed_actions),
                )
                for pa in parsed_actions:
                    pa_name = pa.get("name", "")
                    pa_params = pa.get("parameters") or {}
                    if pa_name and pa_name in agent.available_tools:
                        if pa_name not in tools_used:
                            tools_used.append(pa_name)
                        current_step.action = json.dumps(
                            {"tool": pa_name, "params": pa_params}, ensure_ascii=False
                        )
                        current_step.status = "calling_tool"
                        if agent.on_thinking:
                            await agent._safe_callback(current_step)
                        try:
                            from app.services.execution.tool_executor import UnifiedToolExecutor
                            timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(pa_name, agent.tool_timeout)
                            tool_result = await asyncio.wait_for(
                                agent.tool_executor(pa_name, pa_params),
                                timeout=timeout,
                            )
                            try:
                                action_result_text = json.dumps(tool_result, ensure_ascii=False, default=str)
                            except Exception:
                                action_result_text = str(tool_result)
                            current_step.action_result = action_result_text
                            messages.append({"role": "assistant", "content": result.content or ""})
                            messages.append({"role": "user", "content": f"Tool Output: {action_result_text}"})
                        except Exception as exc:
                            current_step.action_result = f"Error: {exc}"
                            messages.append({"role": "assistant", "content": result.content or ""})
                            messages.append({"role": "user", "content": f"Tool Error: {exc}"})
                current_step.status = "analyzing"
                current_step.finished_at = datetime.now()
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)
            else:
                if (
                    pending_handoff_task_id is not None
                    and pending_handoff_previous_task_id is not None
                    and forced_handoff_followthrough_attempts < 1
                    and agent._can_force_handoff_followthrough_execution(
                        task_context,
                        next_task_id=pending_handoff_task_id,
                    )
                ):
                    forced_handoff_followthrough_attempts += 1
                    prior_handoff_task_id = pending_handoff_task_id
                    forced_result = await agent._execute_forced_handoff_followthrough(
                        task_context=task_context,
                        user_query=user_query,
                        iteration=iteration,
                        previous_task_id=pending_handoff_previous_task_id,
                        next_task_id=prior_handoff_task_id,
                        reason="no_tool_after_handoff",
                    )
                    forced_tool_name = str(forced_result.get("tool_name") or "")
                    if forced_tool_name and forced_tool_name not in tools_used:
                        tools_used.append(forced_tool_name)
                    current_step.action = json.dumps(
                        {
                            "tools": [
                                {
                                    "tool": "code_executor",
                                    "params": {"task": "[forced handoff followthrough]"},
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                    current_step.action_result = forced_result.get("tool_result_text")
                    agent._append_tool_cycle_messages(
                        messages=messages,
                        tool_results=[forced_result],
                        assistant_content=result.content or "",
                        current_step=current_step,
                    )
                    last_tool_cycle_signature = agent._build_tool_cycle_signature([forced_result])
                    identical_tool_cycle_count = 0
                    probe_only_execution_cycles = 0
                    bound_task_after_forced = agent._current_bound_task_id(task_context)
                    if agent._tool_counts_as_real_execution(forced_result):
                        had_real_execution_tool = True
                        last_real_execution_tool_results = [forced_result]
                    if (
                        bound_task_after_forced is not None
                        and bound_task_after_forced != prior_handoff_task_id
                    ):
                        pending_handoff_previous_task_id = prior_handoff_task_id
                        pending_handoff_task_id = bound_task_after_forced
                        forced_handoff_followthrough_attempts = 0
                        had_real_execution_tool = False
                        last_real_execution_tool_results = []
                        partial_completion_retry_count = 0
                        messages.append(
                            {
                                "role": "user",
                                "content": agent._build_task_handoff_execution_nudge(
                                    task_context=task_context,
                                    user_query=user_query,
                                    previous_task_id=prior_handoff_task_id,
                                    next_task_id=bound_task_after_forced,
                                ),
                            }
                        )
                        if (
                            handoff_iteration_extensions < _MAX_HANDOFF_ITERATION_EXTENSIONS
                            and iteration >= runtime_iteration_limit - 1
                        ):
                            runtime_iteration_limit += 1
                            handoff_iteration_extensions += 1
                            logger.info(
                                "[DEEP_THINK_NATIVE] Extended iteration budget after forced handoff followthrough previous=%s next=%s new_limit=%s extension=%s",
                                prior_handoff_task_id,
                                bound_task_after_forced,
                                runtime_iteration_limit,
                                handoff_iteration_extensions,
                            )
                        current_step.self_correction = (
                            f"Detected a no-tool response after handoff to Task {prior_handoff_task_id}; "
                            f"forced code_executor execution and advanced again to Task {bound_task_after_forced}."
                        )
                    else:
                        pending_handoff_task_id = None
                        pending_handoff_previous_task_id = None
                        current_step.self_correction = (
                            f"Detected a no-tool response immediately after handoff to Task {prior_handoff_task_id}; "
                            "forced code_executor execution instead of allowing generic fallback."
                        )
                        if agent._should_force_verified_execution_finalization(
                            task_context=task_context,
                            tool_results=[forced_result],
                            had_real_execution_tool=had_real_execution_tool,
                        ):
                            force_verified_execution_finalization = True
                            messages.append(
                                {
                                    "role": "user",
                                    "content": agent._build_verified_execution_finalize_nudge(
                                        task_context=task_context,
                                        user_query=user_query,
                                    ),
                                }
                            )
                            logger.info(
                                "[DEEP_THINK_NATIVE] Entered verified-execution finalization mode after forced handoff followthrough at iteration=%s",
                                iteration,
                            )
                    current_step.status = "analyzing"
                    current_step.finished_at = datetime.now()
                    thinking_steps.append(current_step)
                    if agent.on_thinking:
                        await agent._safe_callback(current_step)
                    continue

                # --- Early stop for light / standard tiers ---
                # When the LLM produces a substantive text answer without
                # any tool calls on a low-effort request, treat the content
                # as the final answer immediately instead of forcing
                # additional (empty) iterations + synthesis.
                _tier_for_early_stop = agent._request_tier()
                _content_for_early_stop = (result.content or "").strip()
                if (
                    _tier_for_early_stop == "standard"
                    and _content_for_early_stop
                    and len(_content_for_early_stop) >= 20
                    and not agent._is_execute_task_request()
                    and not agent._PROCESS_NARRATION_RE.match(_content_for_early_stop)
                    and not agent._collect_tool_failures_from_steps(thinking_steps)
                ):
                    final_answer = _content_for_early_stop
                    confidence = max(confidence, 0.85)
                    current_step.status = "done"
                    current_step.finished_at = datetime.now()
                    thinking_steps.append(current_step)
                    if agent.on_thinking:
                        await agent._safe_callback(current_step)
                    logger.info(
                        "[DEEP_THINK_NATIVE] Early stop: tier=%s iteration=%s content_len=%d — "
                        "treating direct text as final answer",
                        _tier_for_early_stop,
                        iteration,
                        len(_content_for_early_stop),
                    )
                    if agent.on_final_delta:
                        await agent._stream_final_answer(final_answer)
                    break

                current_step.finished_at = datetime.now()
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)
                messages.append({"role": "assistant", "content": result.content or ""})
                messages.append({"role": "user", "content": agent._get_next_step_prompt(iteration)})

        if agent._skip_current_step:
            agent._skip_current_step = False
            messages.append({"role": "user", "content": "Skip current branch and continue with the next reasoning step."})

        # Inject a strong nudge when nearing the iteration limit
        if not final_answer and iteration >= runtime_iteration_limit - 1:
            messages.append({
                "role": "user",
                "content": (
                    "IMPORTANT: You are about to reach the maximum number of thinking steps. "
                    "You MUST call submit_final_answer on the NEXT step with the best answer you can provide "
                    "based on all evidence gathered so far. Do NOT continue researching — synthesize NOW."
                ),
            })

    if final_answer and not llm_fatal_abort and not agent._is_valid_final_answer(final_answer, user_query=user_query):
        logger.info("[DEEP_THINK_NATIVE] Rejected process-only final answer; switching to fallback synthesis")
        final_answer = ""

    # ---- Forced synthesis: one more LLM call with all evidence before falling back ----
    # Guard: only emit the "整集阻塞" blocker when the router has explicitly confirmed
    # (via explicit_scope_all_blocked in context) that every task in the named set is
    # unreachable.  Using explicit_task_override alone is insufficient — tools may have
    # run successfully but the LLM simply didn't emit a final answer, in which case the
    # normal forced-synthesis path is still appropriate.
    _explicit_override = task_context is not None and bool(
        getattr(task_context, "explicit_task_override", False)
    )
    _scope_all_blocked = bool((context or {}).get("explicit_scope_all_blocked"))
    if not final_answer and _explicit_override and _scope_all_blocked:
        _blocked_ids = list(getattr(task_context, "explicit_task_ids", None) or [])
        _id_str = ", ".join(str(t) for t in _blocked_ids) if _blocked_ids else "the requested tasks"
        _block_reason = str((context or {}).get("explicit_scope_block_reason") or "blocked_deps").strip().lower()
        if _block_reason == "all_completed":
            final_answer = (
                f"Tasks [{_id_str}] are already completed — "
                f"no re-execution is needed. "
                f"If you want to re-run them, please say so explicitly."
            )
        else:
            final_answer = (
                f"Tasks [{_id_str}] could not be executed in this turn: "
                f"all tasks in the explicit set are blocked by unmet out-of-scope dependencies. "
                f"Please check the dependency status of the listed tasks and retry "
                f"after resolving any upstream blockers."
            )
        fallback_used = True
        logger.info(
            "[DEEP_THINK_NATIVE] explicit_scope_all_blocked: skipping forced synthesis, "
            "emitting structured blocker for task_ids=%s reason=%s",
            _blocked_ids,
            _block_reason,
        )
        if agent.on_final_delta:
            await agent._stream_final_answer(final_answer)
    elif not final_answer and thinking_steps:
        phagescope_failure_answer = agent._build_phagescope_deep_profile_failure_answer(
            user_query=user_query,
            steps=thinking_steps,
        )
        if phagescope_failure_answer:
            logger.warning(
                "[DEEP_THINK_NATIVE] Blocking forced synthesis after failed PhageScope deep_profile."
            )
            final_answer = phagescope_failure_answer
        else:
            logger.info("[DEEP_THINK_NATIVE] No final answer after %d iterations; attempting forced synthesis", iteration)
            final_answer = await agent._forced_synthesis_from_steps(
                thinking_steps,
                user_query,
                messages,
                task_context=task_context,
            )
        if final_answer and agent._should_reject_missing_task_definition_answer(
            final_answer,
            task_context=task_context,
        ):
            logger.warning(
                "[DEEP_THINK_NATIVE] Rejected forced synthesis that incorrectly asked for missing task definitions while a bound task context existed."
            )
            final_answer = ""
        if final_answer:
            fallback_used = True
            confidence = max(confidence, 0.5)
            if agent.on_final_delta:
                await agent._stream_final_answer(final_answer)

    if not final_answer:
        fallback_used = True
        final_answer = await agent._fallback_answer_from_steps(
            thinking_steps,
            user_query,
            task_context=task_context,
        )
        confidence = max(confidence, 0.3)
        if agent.on_final_delta and final_answer:
            await agent._stream_final_answer(final_answer)

    tool_failures = agent._collect_tool_failures_from_steps(thinking_steps)
    search_verified = agent._search_verified_from_steps(thinking_steps)
    final_answer = agent._apply_external_search_notice(
        final_answer,
        user_query=user_query,
        tool_failures=tool_failures,
        search_verified=search_verified,
    )
    execute_truth_answer = agent._apply_execute_failure_truth_barrier(
        final_answer,
        user_query=user_query,
        steps=thinking_steps,
    )
    if execute_truth_answer != final_answer:
        fallback_used = True
    final_answer = execute_truth_answer
    evidence_scope_answer = agent._apply_evidence_scope_truth_barrier(
        final_answer,
        user_query=user_query,
        steps=thinking_steps,
    )
    if evidence_scope_answer != final_answer:
        fallback_used = True
    final_answer = evidence_scope_answer
    structured_plan_outcome = agent._summarize_structured_plan_outcome(
        thinking_steps,
        user_query=user_query,
    )
    if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
        final_answer = agent._build_structured_plan_contract_failure_answer(
            outcome=structured_plan_outcome,
            user_query=user_query,
        )
        fallback_used = True
    else:
        final_answer = agent._ensure_structured_plan_notice(
            final_answer,
            outcome=structured_plan_outcome,
            user_query=user_query,
        )
    final_answer = sanitize_professional_response_text(final_answer)
    final_answer = _ensure_inline_images(final_answer, agent._collect_inline_image_relpaths())

    try:
        summary = await agent._generate_summary(thinking_steps, user_query)
    except Exception:
        summary = _dta()._default_deepthink_summary(user_query)

    return DeepThinkResult(
        final_answer=final_answer,
        thinking_steps=thinking_steps,
        total_iterations=iteration,
        tools_used=tools_used,
        confidence=confidence,
        thinking_summary=summary,
        tool_failures=tool_failures,
        search_verified=search_verified,
        fallback_used=fallback_used,
        structured_plan_required=bool(structured_plan_outcome.get("required")),
        structured_plan_satisfied=bool(structured_plan_outcome.get("satisfied")),
        structured_plan_state=structured_plan_outcome.get("state"),
        structured_plan_message=structured_plan_outcome.get("message"),
        structured_plan_plan_id=structured_plan_outcome.get("plan_id"),
        structured_plan_title=structured_plan_outcome.get("plan_title"),
        structured_plan_operation=structured_plan_outcome.get("operation"),
    )


async def _think_prompt_based(
    agent: "DeepThinkAgent",
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    task_context: Optional[TaskExecutionContext] = None,
) -> DeepThinkResult:
    context = dict(context or {})
    thinking_steps: List[ThinkingStep] = []
    tools_used: List[str] = []

    system_prompt = agent._build_system_prompt(context, task_context=task_context)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"User Query: {user_query}"}
    ]

    iteration = 0
    final_answer = ""
    fallback_used = False
    confidence = 0.0
    last_tool_cycle_signature: Optional[str] = None
    identical_tool_cycle_count = 0

    logger.info(f"Starting DeepThink for query: {user_query[:50]}...")

    while iteration < agent.max_iterations:
        await agent._get_pause_event().wait()
        if agent.cancel_event and agent.cancel_event.is_set():
            logger.info("DeepThink cancelled by user")
            break

        iteration += 1

        try:
            current_step = ThinkingStep(
                iteration=iteration,
                thought="",
                action=None,
                action_result=None,
                self_correction=None,
                timestamp=datetime.now(),
                status="thinking"
            )

            if agent.on_thinking:
                await agent._safe_callback(current_step)

            response_text = ""

            if not hasattr(agent.llm_client, "stream_chat_async"):
                raise DeepThinkProtocolError(
                    "DeepThink requires LLM client support for stream_chat_async in strict mode."
                )

            logger.info("[DEEP_THINK] Using streaming LLM call")
            async for delta in agent.llm_client.stream_chat_async(
                prompt="", messages=messages,
                enable_thinking=agent.enable_thinking,
                thinking_budget=agent.thinking_budget,
                on_reasoning_delta=lambda chunk: (
                    agent.on_reasoning_delta(iteration, chunk)
                    if agent.on_reasoning_delta else None
                ),
            ):
                response_text += delta
                if agent.on_thinking_delta:
                    await agent._safe_delta_callback(iteration, delta)

            parsed, parse_error = agent._parse_llm_response_safe(response_text)
            if parse_error:
                logger.warning(
                    "DeepThink parse error at iteration %s: %s",
                    iteration,
                    parse_error,
                )
                current_step.status = "error"
                current_step.thought = f"Protocol recovery: {parse_error}"
                current_step.self_correction = "Requesting corrected JSON schema output."
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)
                messages.append({"role": "assistant", "content": response_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous output violated protocol. "
                            "Return ONLY valid JSON with keys: thinking, action, final_answer."
                        ),
                    }
                )
                continue

            current_step.thought = parsed.get("thought", "")
            current_step.action = parsed.get("action_str", None)

            if parsed.get("is_final"):
                candidate_answer = parsed.get("final_answer", "")
                confidence = parsed.get("confidence", 0.8)
                if agent._is_valid_final_answer(candidate_answer, user_query=user_query):
                    structured_plan_outcome = agent._summarize_structured_plan_outcome(
                        thinking_steps,
                        user_query=user_query,
                    )
                    final_answer = (
                        candidate_answer
                        if not structured_plan_outcome.get("required")
                        or structured_plan_outcome.get("satisfied")
                        else ""
                    )
                    if not final_answer:
                        current_step.self_correction = (
                            "Rejected the final answer because the required structured plan was not created or updated yet."
                        )
                        messages.append({"role": "assistant", "content": response_text})
                        messages.append(
                            {
                                "role": "user",
                                "content": agent._get_structured_plan_retry_prompt(),
                            }
                        )
                else:
                    final_answer = ""
                current_step.status = "done"
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)

                # Stream final answer if callback provided
                if agent.on_final_delta and final_answer:
                    await agent._stream_final_answer(final_answer)
                break

            if current_step.action:
                current_step.status = "calling_tool"
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)

                tool_name = parsed.get("tool_name")
                tool_params = parsed.get("tool_params")

                if tool_name not in agent.available_tools:
                    current_step.action_result = f"Error: Tool '{tool_name}' is not available. Available: {agent.available_tools}"
                elif tool_params is not None and not isinstance(tool_params, dict):
                    current_step.action_result = f"Error: Tool params must be a dict, got {type(tool_params).__name__}"
                else:
                    if tool_name not in tools_used:
                        tools_used.append(tool_name)
                    timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(
                        str(tool_name),
                        agent.tool_timeout,
                    )
                    attempt = 0
                    while True:
                        attempt += 1
                        try:
                            if agent.on_tool_start:
                                await agent._safe_generic_callback(
                                    agent.on_tool_start,
                                    str(tool_name),
                                    dict(tool_params or {}),
                                )
                            result = await asyncio.wait_for(
                                agent.tool_executor(tool_name, tool_params or {}),
                                timeout=timeout
                            )
                            try:
                                current_step.action_result = json.dumps(
                                    result, ensure_ascii=False, default=str
                                )
                            except Exception:
                                current_step.action_result = str(result)
                            await agent._emit_artifacts(str(tool_name), result, iteration)
                            callback_success, callback_error = agent._normalize_tool_callback_outcome(result)
                            if agent.on_tool_result:
                                await agent._safe_generic_callback(
                                    agent.on_tool_result,
                                    str(tool_name),
                                    {
                                        "success": callback_success,
                                        "error": callback_error,
                                        "result": result,
                                        "summary": agent._build_tool_callback_summary(result),
                                        "iteration": iteration,
                                        "attempt": attempt,
                                    },
                                )
                            if agent._should_retry_external_tool(str(tool_name), success=callback_success) and attempt <= agent.MAX_EXTERNAL_TOOL_RETRIES:
                                if agent.on_tool_result:
                                    await agent._safe_generic_callback(
                                        agent.on_tool_result,
                                        str(tool_name),
                                        {
                                            "success": False,
                                            "error": callback_error,
                                            "summary": agent._build_tool_callback_summary(result),
                                            "iteration": iteration,
                                            "attempt": attempt,
                                            "retrying": True,
                                            "retry_attempt": attempt,
                                            "max_attempts": agent.MAX_EXTERNAL_TOOL_RETRIES + 1,
                                        },
                                    )
                                continue
                            break
                        except asyncio.TimeoutError:
                            current_step.action_result = f"Error: Tool '{tool_name}' execution timed out after {timeout}s"
                            logger.warning(f"Tool {tool_name} timed out after {timeout}s")
                            should_retry = agent._should_retry_external_tool(str(tool_name), success=False) and attempt <= agent.MAX_EXTERNAL_TOOL_RETRIES
                            if agent.on_tool_result:
                                await agent._safe_generic_callback(
                                    agent.on_tool_result,
                                    str(tool_name),
                                    {
                                        "success": False,
                                        "error": "timeout",
                                        "summary": current_step.action_result,
                                        "iteration": iteration,
                                        "attempt": attempt,
                                        "retrying": should_retry,
                                        "retry_attempt": attempt if should_retry else None,
                                        "max_attempts": agent.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                                    },
                                )
                            if should_retry:
                                continue
                            break
                        except Exception as e:
                            current_step.action_result = f"Error executing tool: {str(e)}"
                            logger.exception(f"Tool {tool_name} execution failed")
                            should_retry = agent._should_retry_external_tool(str(tool_name), success=False) and attempt <= agent.MAX_EXTERNAL_TOOL_RETRIES
                            if agent.on_tool_result:
                                await agent._safe_generic_callback(
                                    agent.on_tool_result,
                                    str(tool_name),
                                    {
                                        "success": False,
                                        "error": str(e),
                                        "summary": current_step.action_result,
                                        "iteration": iteration,
                                        "attempt": attempt,
                                        "retrying": should_retry,
                                        "retry_attempt": attempt if should_retry else None,
                                        "max_attempts": agent.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                                    },
                                )
                            if should_retry:
                                continue
                            break

                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": f"Tool Output: {current_step.action_result}"})

                cycle_results = [
                    {
                        "tool_name": str(tool_name or ""),
                        "tool_params": dict(tool_params or {}),
                        "tool_result_text": current_step.action_result or "",
                    }
                ]
                tool_cycle_signature = agent._build_tool_cycle_signature(cycle_results)
                if tool_cycle_signature and tool_cycle_signature == last_tool_cycle_signature:
                    identical_tool_cycle_count += 1
                    if identical_tool_cycle_count == 1:
                        correction_nudge = agent._build_tool_failure_correction_nudge(cycle_results)
                        if correction_nudge:
                            messages.append({"role": "user", "content": correction_nudge})
                            logger.info(
                                "[DEEP_THINK_NATIVE] Injected correction nudge after repeated tool failure"
                            )
                else:
                    last_tool_cycle_signature = tool_cycle_signature
                    identical_tool_cycle_count = 0

                if identical_tool_cycle_count >= agent.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                    repeated_cycles = identical_tool_cycle_count + 1
                    rep_missing = _missing_expectations(
                        getattr(agent, "_expected_outputs_current", None) or [],
                        getattr(agent, "_produced_deliverable_paths", None) or [],
                    )
                    if rep_missing:
                        agent._acceptance_missing = list(rep_missing)
                        logger.warning(
                            "[DEEP_THINK][acceptance] identical-cycle stop with missing deliverable types: %s",
                            ",".join(rep_missing),
                        )
                    current_step.status = "done"
                    current_step.self_correction = (
                        "Stopped repeated identical tool polling to avoid an unproductive loop."
                    )
                    if agent.on_thinking:
                        await agent._safe_callback(current_step)
                    final_answer = agent._build_repetition_stop_answer(
                        tool_results=cycle_results,
                        repeated_cycles=repeated_cycles,
                    )
                    if rep_missing:
                        final_answer += (
                            "\n\nNote: the requested deliverable type(s) are still missing: "
                            + ", ".join(rep_missing)
                            + "."
                        )
                    confidence = max(
                        confidence,
                        0.75 if str(tool_name or "").strip().lower() == "phagescope" else 0.5,
                    )
                    if agent.on_final_delta and final_answer:
                        await agent._stream_final_answer(final_answer)
                    break

                current_step.status = "analyzing"
                if agent.on_thinking:
                    await agent._safe_callback(current_step)

            else:
                thinking_steps.append(current_step)
                if agent.on_thinking:
                    await agent._safe_callback(current_step)
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": agent._get_next_step_prompt(iteration)})

        except Exception as e:
            logger.exception("Error in deep thinking loop")
            current_step.status = "error"
            current_step.thought = f"Error: {str(e)}"
            thinking_steps.append(current_step)
            if agent.on_thinking:
                await agent._safe_callback(current_step)
            messages.append(
                {
                    "role": "user",
                    "content": "Continue with a robust fallback and provide valid JSON only.",
                }
            )
            continue

        if agent._skip_current_step:
            agent._skip_current_step = False
            messages.append(
                {
                    "role": "user",
                    "content": "Skip current branch and continue with the next reasoning step.",
                }
            )

    if not final_answer and thinking_steps:
        logger.info("[DEEP_THINK] Iterations exhausted, requesting strict final conclusion")

        conclusion_prompt = """You have reached the thinking limit. Based on the information you already have,
you MUST now provide a final answer. Synthesize what is most relevant into a direct, appropriately scoped response.

Respond with ONLY a JSON object:
{
  "thinking": "I've gathered the following key information: [summarize key findings]",
  "action": null,
  "final_answer": {"answer": "Your final answer based on the gathered information", "confidence": 0.7}
}"""

        messages.append({"role": "user", "content": conclusion_prompt})

        try:
            if not hasattr(agent.llm_client, "stream_chat_async"):
                raise DeepThinkProtocolError(
                    "DeepThink requires stream_chat_async for forced conclusion in strict mode."
                )

            response_text = ""
            async for delta in agent.llm_client.stream_chat_async(
                prompt="", messages=messages,
                enable_thinking=agent.enable_thinking,
                thinking_budget=agent.thinking_budget,
                on_reasoning_delta=lambda chunk: (
                    agent.on_reasoning_delta(iteration + 1, chunk)
                    if agent.on_reasoning_delta else None
                ),
            ):
                response_text += delta
                if agent.on_thinking_delta:
                    await agent._safe_delta_callback(iteration + 1, delta)

            parsed, parse_error = agent._parse_llm_response_safe(response_text)
            if parse_error:
                logger.warning("Forced conclusion parse fallback triggered: %s", parse_error)
                parsed = {}
            if parsed.get("is_final"):
                candidate_answer = parsed.get("final_answer", "")
                confidence = parsed.get("confidence", 0.7)
                if agent._is_valid_final_answer(candidate_answer, user_query=user_query):
                    structured_plan_outcome = agent._summarize_structured_plan_outcome(
                        thinking_steps,
                        user_query=user_query,
                    )
                    final_answer = (
                        candidate_answer
                        if not structured_plan_outcome.get("required")
                        or structured_plan_outcome.get("satisfied")
                        else ""
                    )
                else:
                    final_answer = ""

                # Stream final answer
                if agent.on_final_delta and final_answer:
                    await agent._stream_final_answer(final_answer)
            else:
                fallback_used = True
                final_answer = await agent._fallback_answer_from_steps(thinking_steps, user_query)
                confidence = 0.5
                if agent.on_final_delta and final_answer:
                    await agent._stream_final_answer(final_answer)
        except Exception as e:
            logger.exception("Failed to generate strict forced conclusion")
            fallback_used = True
            final_answer = await agent._fallback_answer_from_steps(thinking_steps, user_query)
            confidence = 0.4

    if final_answer and not agent._is_valid_final_answer(final_answer, user_query=user_query):
        final_answer = ""

    # Forced synthesis before generic fallback (prompt-based path)
    if not final_answer and thinking_steps:
        logger.info("[DEEP_THINK] Attempting forced synthesis (prompt-based path)")
        final_answer = await agent._forced_synthesis_from_steps(thinking_steps, user_query, messages)
        if final_answer:
            fallback_used = True
            confidence = max(confidence, 0.5)
            if agent.on_final_delta:
                await agent._stream_final_answer(final_answer)

    if not final_answer:
        fallback_used = True
        final_answer = await agent._fallback_answer_from_steps(thinking_steps, user_query)
        confidence = max(confidence, 0.3)

    tool_failures = agent._collect_tool_failures_from_steps(thinking_steps)
    search_verified = agent._search_verified_from_steps(thinking_steps)
    final_answer = agent._apply_external_search_notice(
        final_answer,
        user_query=user_query,
        tool_failures=tool_failures,
        search_verified=search_verified,
    )
    execute_truth_answer = agent._apply_execute_failure_truth_barrier(
        final_answer,
        user_query=user_query,
        steps=thinking_steps,
    )
    if execute_truth_answer != final_answer:
        fallback_used = True
    final_answer = execute_truth_answer
    structured_plan_outcome = agent._summarize_structured_plan_outcome(
        thinking_steps,
        user_query=user_query,
    )
    if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
        final_answer = agent._build_structured_plan_contract_failure_answer(
            outcome=structured_plan_outcome,
            user_query=user_query,
        )
        fallback_used = True
    else:
        final_answer = agent._ensure_structured_plan_notice(
            final_answer,
            outcome=structured_plan_outcome,
            user_query=user_query,
        )
    final_answer = sanitize_professional_response_text(final_answer)
    final_answer = _ensure_inline_images(final_answer, agent._collect_inline_image_relpaths())

    try:
        summary = await agent._generate_summary(thinking_steps, user_query)
    except Exception:
        summary = _dta()._default_deepthink_summary(user_query)

    return DeepThinkResult(
        final_answer=final_answer,
        thinking_steps=thinking_steps,
        total_iterations=iteration,
        tools_used=tools_used,
        confidence=confidence,
        thinking_summary=summary,
        tool_failures=tool_failures,
        search_verified=search_verified,
        fallback_used=fallback_used,
        structured_plan_required=bool(structured_plan_outcome.get("required")),
        structured_plan_satisfied=bool(structured_plan_outcome.get("satisfied")),
        structured_plan_state=structured_plan_outcome.get("state"),
        structured_plan_message=structured_plan_outcome.get("message"),
        structured_plan_plan_id=structured_plan_outcome.get("plan_id"),
        structured_plan_title=structured_plan_outcome.get("plan_title"),
        structured_plan_operation=structured_plan_outcome.get("operation"),
    )
