"""Real file handler through both chat model protocols; no provider API calls."""
import json
from pathlib import Path
import pytest
from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import DeepThinkAgent
from app.tests.chat.test_unified_stream_event_golden import _build_agent, _patch_runtime, _run


class Provider:
    def __init__(self, target, recover):
        self.target, self.recover, self.calls = target, recover, []

    def next(self, messages):
        self.calls.append(json.loads(json.dumps(messages)))
        attempt = len(self.calls)
        if attempt == 1 or (self.recover and attempt == 2):
            if attempt == 2:
                assert 'Permission denied' in str(messages)
            return {
                'thinking': 'Save the requested note using the tool receipt.',
                'action': {'tool':'file_operations','params':{'operation':'write','path':str(self.target),'content':'# 笔记\n已保存正文。'}},
                'final_answer': None,
            }
        assert 'success' in str(messages)
        return {'thinking':'The file was saved.','action':None,'final_answer':{'answer':'笔记已保存，文件工具已返回实际保存路径。','confidence':0.9}}

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next(kwargs['messages']))

    async def chat_async(self, **kwargs):
        pytest.fail('An extra synthesis/classification request is not needed')


class NativeProvider(Provider):
    async def stream_chat_with_tools_async(self, **kwargs):
        data = self.next(kwargs['messages']); action = data['action']
        call = NativeToolCall(str(len(self.calls)), action['tool'], action['params']) if action else NativeToolCall('final','submit_final_answer',data['final_answer'])
        return NativeStreamResult(content=data['thinking'],tool_calls=[call],finish_reason='tool_calls')


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('recover', [False, True])
def test_file_write_has_session_context_and_recovered_failure_does_not_brand_reply(monkeypatch, tmp_path, native, recover):
    from app.services.foundation.settings import get_settings
    from tool_box.tools_impl import file_operations
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(get_settings(), 'chat_model_action_selection_enabled', True)
    repo = tmp_path/'repo'; repo.mkdir()
    runtime = repo/'runtime'; runtime.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv('APP_RUNTIME_ROOT',str(runtime))
    provider = (NativeProvider if native else Provider)(repo/'results'/'nested'/'note.md', recover)
    contexts, results = [], []
    real_write = file_operations._write_file
    async def write(path, content, tool_context=None):
        if recover and not results:
            return {'success':False,'operation':'write','path':path,'error':"[Errno 13] Permission denied: '/app/results'"}
        return await real_write(path, content, tool_context=tool_context)
    monkeypatch.setattr(file_operations,'_write_file',write)
    async def handler(name, tool_context=None, **params):
        assert name == 'file_operations'
        contexts.append(tool_context)
        result = await file_operations.file_operations_handler(tool_context=tool_context, **params)
        results.append(result)
        return result
    monkeypatch.setattr('app.routers.chat.agent.execute_tool',handler)
    monkeypatch.setattr('app.routers.chat_routes.execute_tool',handler)
    monkeypatch.setattr('app.routers.chat.action_handlers._update_session_metadata',lambda *a, **kw: None)
    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(**{**kwargs,'request_profile':request_profile,'llm_client':provider})
    monkeypatch.setattr('app.routers.chat_routes.DeepThinkAgent',Agent)
    agent = _build_agent(extra_context={
        'owner_id':'alice',
        'last_failure_state':{'error_message':"[Errno 13] Permission denied: '/app/results'"},
    })
    events = _run(agent,'请把这一段保存为笔记')
    assert len(provider.calls) == (3 if recover else 2)
    assert all(context.session_id == agent.session_id and context.owner_id == 'alice' for context in contexts)
    assert results[-1]['success'], results
    saved = Path(results[-1]['path'])
    assert saved.is_relative_to(runtime) and saved.parts[-2:] == ('nested','note.md')
    assert saved.read_text() == '# 笔记\n已保存正文。'
    assert not (repo/'results').exists()
    assert agent.extra_context['active_subject']['canonical_ref'] == str(saved)
    if recover:
        assert 'last_failure_state' not in agent.extra_context
    payload = events[-1]['payload']
    assert payload['response'] == '笔记已保存，文件工具已返回实际保存路径。'
    assert '未被验证成功' not in payload['response']
    receipts = payload['metadata']['tool_results']
    if recover:
        assert results[0]['success'] is False and 'Permission denied' in str(receipts)
    assert str(saved) in str(receipts)


@pytest.mark.parametrize('native', [False, True])
def test_unresolved_error_still_reaches_model_and_final_receipt(monkeypatch, tmp_path, native):
    from app.services.foundation.settings import get_settings
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(get_settings(), 'chat_model_action_selection_enabled', True)
    monkeypatch.setenv('APP_RUNTIME_ROOT',str(tmp_path/'runtime'))
    provider = (NativeProvider if native else Provider)(tmp_path/'note.md', False)
    original_next = provider.next
    def next_response(messages):
        if not provider.calls:
            return original_next(messages)
        provider.calls.append(messages)
        assert 'Permission denied' in str(messages)
        return {'thinking':'Report the unresolved write failure.','action':None,'final_answer':{
            'answer':'文件尚未保存：写入目录没有权限，本轮没有完成文件交付。','confidence':0.8}}
    provider.next = next_response
    async def fail(name, **params):
        return {'success':False,'operation':'write','error':"[Errno 13] Permission denied: '/app/results'"}
    monkeypatch.setattr('app.routers.chat_routes.execute_tool',fail)
    monkeypatch.setattr('app.routers.chat.agent.execute_tool',fail)
    monkeypatch.setattr('app.routers.chat.action_handlers._update_session_metadata',lambda *a, **kw: None)
    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(**{**kwargs,'request_profile':request_profile,'llm_client':provider})
    monkeypatch.setattr('app.routers.chat_routes.DeepThinkAgent',Agent)
    payload = _run(_build_agent(), '请保存这一份笔记')[-1]['payload']
    assert len(provider.calls) == 2
    assert payload['response'] == '文件尚未保存：写入目录没有权限，本轮没有完成文件交付。'
    assert 'Permission denied' in str(payload['metadata']['tool_results'])
    assert not (tmp_path/'note.md').exists()
