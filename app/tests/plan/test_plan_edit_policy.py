from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.plans.edit_policy import user_requested_plan_edit, agent_plan_edit_allowed
from tool_box.context import ToolContext
from tool_box.tools_impl import plan_tools


@pytest.mark.parametrize('message', ['这样啊，你重新执行一下吧', '重试任务4，不要拆分任务',
    '帮我补齐任务4缺少的文件', '任务4失败了是什么原因', '如何拆分任务4', '继续执行计划，不允许修改任务树',
    '任务4为什么被拆分了？', '为什么修改任务4？'])
def test_execution_or_advice_does_not_authorize_plan_edit(message):
    assert not user_requested_plan_edit(message)


@pytest.mark.parametrize('message', ['请把任务4拆成七个子任务', '请拆分任务4', '删除task 4', '修改任务4的说明',
    '调整这个计划', 'Add a task to this plan', '先拆解任务4，再执行', '能帮我修改任务4吗？'])
def test_explicit_edit_authorizes_plan_tools(message):
    assert user_requested_plan_edit(message)


def test_old_creation_context_is_not_edit_authorization_for_new_retry():
    agent = SimpleNamespace(_current_user_message='重新执行一下吧', extra_context={'plan_create_required':True})
    assert agent_plan_edit_allowed(agent) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create','bind','optimize','update_task'])
async def test_plan_handler_rejects_mutation_before_dispatch(monkeypatch, operation):
    called = AsyncMock()
    names = {'create':'_create_plan','bind':'_bind_plan','optimize':'_optimize_plan','update_task':'_update_task'}
    monkeypatch.setattr(plan_tools, names[operation], called)
    result = await plan_tools.plan_operation_handler(operation, plan_id=185,
        tool_context=ToolContext(plan_id=185, extra={'plan_tree_edit_allowed':False}))
    assert result['error_code'] == 'plan_edit_not_requested' and result['executed'] is False
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_edit_still_reaches_existing_handler(monkeypatch):
    called = AsyncMock(return_value={'success':True})
    monkeypatch.setattr(plan_tools,'_optimize_plan',called)
    result = await plan_tools.plan_operation_handler('optimize',plan_id=185,changes=[],
        tool_context=ToolContext(plan_id=185,extra={'plan_tree_edit_allowed':True}))
    assert result['success'] is True
    called.assert_awaited_once()


@pytest.mark.asyncio
async def test_execution_context_reaches_tool_handler_with_edits_disabled(monkeypatch):
    from app.services.execution.tool_executor import UnifiedToolExecutor, ToolExecutionContext
    import tool_box
    called = AsyncMock()
    monkeypatch.setattr(plan_tools,'_optimize_plan',called)
    async def execute(name, **kwargs):
        assert name == 'plan_operation'
        return await plan_tools.plan_operation_handler(**kwargs)
    monkeypatch.setattr(tool_box,'execute_tool',execute)
    result = await UnifiedToolExecutor().execute('plan_operation',{'operation':'optimize','plan_id':185},
        context=ToolExecutionContext(plan_id=185,task_id=4,mode='task_execution'))
    assert result['success'] is False
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_mode_rpc_preserves_the_execution_edit_boundary(monkeypatch):
    from tool_box.tools_impl.execute_code.rpc import KernelRPCServer
    import tool_box
    called = AsyncMock()
    monkeypatch.setattr(plan_tools,'_optimize_plan',called)
    async def execute(name, **kwargs):
        return await plan_tools.plan_operation_handler(**kwargs)
    monkeypatch.setattr(tool_box,'execute_tool',execute)
    result = await KernelRPCServer._dispatch(object(), 'plan_operation',
        {'operation':'optimize','plan_id':185}, ToolContext(plan_id=185,extra={'plan_tree_edit_allowed':False}))
    assert result['executed'] is False
    called.assert_not_awaited()


@pytest.mark.parametrize('name', ['create_task','decompose_task','delete_task','update_task_instruction'])
def test_structured_task_edits_are_blocked_during_retry(name):
    from app.routers.chat.action_task_ops import handle_task_action
    from app.services.llm.structured_response import LLMAction
    agent = SimpleNamespace(_current_user_message='重新执行任务4')
    result = handle_task_action(agent,LLMAction(kind='task_operation',name=name,parameters={},order=1))
    assert result.success is False and result.details['executed'] is False
