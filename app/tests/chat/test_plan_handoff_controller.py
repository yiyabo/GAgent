import asyncio
import json
import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import DeepThinkAgent


@pytest.mark.parametrize('native', [True, False])
def test_background_plan_submission_closes_chat_without_another_model_call(native):
    calls = []
    params = {'operation': 'execute_task', 'plan_id': 185, 'target_task_ids': [4]}
    class Strict:
        async def stream_chat_async(self, **kwargs):
            calls.append('model')
            assert len(calls) == 1, 'chat kept running after submission'
            yield json.dumps({'thinking': 'Submit the selected task',
                              'action': {'tool': 'plan_operation', 'params': params}, 'final_answer': None})
        async def chat_async(self, **kwargs):
            pytest.fail('handoff triggered an extra synthesis/summary call')
    class Native(Strict):
        async def stream_chat_with_tools_async(self, **kwargs):
            calls.append('model')
            assert len(calls) == 1, 'chat kept running after submission'
            return NativeStreamResult(content='Submit the selected task', tool_calls=[
                NativeToolCall(id='submit-one', name='plan_operation', arguments=params)])
    async def execute(name, arguments):
        assert name == 'plan_operation'
        result = {'success': True, 'operation': 'execute_task', 'plan_id': 185,
                  'job_id': 'job-4', 'background_submitted': True, 'submitted_task_ids': [4]}
        agent._chat_plan_handoff_result = result
        agent._chat_plan_handoff_query = '提交任务4到后台执行'
        return result
    agent = DeepThinkAgent(llm_client=Native() if native else Strict(),
                           available_tools=['plan_operation'], tool_executor=execute, max_iterations=8)
    result = asyncio.run(agent.think('提交任务4到后台执行'))
    assert calls == ['model']
    assert result.termination_reason == 'background_plan_submitted'
    assert '任务 #4' in result.final_answer and '正在排队' in result.final_answer
    assert 'job-4' not in result.final_answer and result.output_verification is None
    assert result.thinking_steps[-1].status == 'done'
