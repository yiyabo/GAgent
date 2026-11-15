"""Close the chat loop after its plan tool has submitted a background job."""

from .models import DeepThinkResult


async def conclude_background_plan(agent, steps, tools_used, iteration):
    record = agent._chat_plan_handoff_result
    if record.get("operation") == "ask_user" and isinstance(record.get("clarification"), dict):
        return await _conclude_clarification(agent, record["clarification"], steps, tools_used, iteration)
    if record.get("operation") == "offer":
        offer = record.get("offer") if isinstance(record.get("offer"), dict) else {}
        reason = str(offer.get("reason") or "这个任务包含多阶段研究和执行。")
        text = (
            f"{reason}\n\n建议先进入 Plan 模式，完成只读研究并创建结构化计划。"
            "请选择“进入 Plan 模式并创建计划”或“直接继续”。在你做出选择前，本轮不会创建或执行计划。"
        )
        if steps:
            steps[-1].status = "done"
            if agent.on_thinking:
                await agent._safe_callback(steps[-1])
        if agent.on_final_delta:
            await agent._stream_final_answer(text)
        return DeepThinkResult(
            final_answer=text,
            thinking_steps=steps,
            total_iterations=iteration,
            tools_used=tools_used,
            confidence=1.0,
            thinking_summary="等待用户选择是否进入 Plan 模式",
            termination_reason="plan_mode_offer",
            structured_plan_required=False,
            structured_plan_satisfied=False,
            structured_plan_state="offer",
            structured_plan_operation="offer",
            plan_mode_offer=offer,
        )
    plan_id = record.get('plan_id')
    from app.services.plans.execution_receipt import execution_receipt
    text = execution_receipt(record, record.get('submitted_task_ids'))
    if steps:
        steps[-1].status = 'done'
        if agent.on_thinking:
            await agent._safe_callback(steps[-1])
    if agent.on_final_delta:
        await agent._stream_final_answer(text)
    outcome = agent._summarize_structured_plan_outcome(steps, user_query=agent._chat_plan_handoff_query)
    return DeepThinkResult(
        final_answer=text, thinking_steps=steps, total_iterations=iteration,
        tools_used=tools_used, confidence=1.0, thinking_summary='已提交后台计划作业',
        termination_reason='background_plan_submitted',
        structured_plan_required=bool(outcome.get('required')),
        structured_plan_satisfied=bool(outcome.get('satisfied')),
        structured_plan_state=outcome.get('state'), structured_plan_plan_id=plan_id,
        structured_plan_operation=record.get('operation') or 'execute_all',
    )


async def _conclude_clarification(agent, request, steps, tools_used, iteration):
    """End the turn with a pending question card; the answer is the next message."""
    from app.services.deep_think_agent import detect_reasoning_language
    from app.services.plan_clarifications import clarification_text

    query = str(getattr(agent, "_chat_plan_handoff_query", "") or "")
    language = detect_reasoning_language(
        query + " ".join(str(q.get("question") or "") for q in request.get("questions") or [])
    )
    text = clarification_text(request, language)
    if steps:
        steps[-1].status = "done"
        if agent.on_thinking:
            await agent._safe_callback(steps[-1])
    if agent.on_final_delta:
        await agent._stream_final_answer(text)
    return DeepThinkResult(
        final_answer=text,
        thinking_steps=steps,
        total_iterations=iteration,
        tools_used=tools_used,
        confidence=1.0,
        thinking_summary="等待用户回答规划问题" if language == "zh" else "Waiting for the user's planning answers",
        termination_reason="plan_clarification",
        structured_plan_required=False,
        structured_plan_satisfied=False,
        structured_plan_state="clarification",
        structured_plan_operation="ask_user",
        plan_clarification=dict(request),
    )
