"""Close the chat loop after its plan tool has submitted a background job."""

from .models import DeepThinkResult


async def conclude_background_plan(agent, steps, tools_used, iteration):
    record = agent._chat_plan_handoff_result
    job_id = str(record['job_id'])
    plan_id = record.get('plan_id')
    text = f"计划 #{plan_id} 已提交后台执行（作业 {job_id}）。你可以继续聊天，进度和产物在计划面板查看。"
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
