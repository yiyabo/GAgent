"""Preferences persist per owner/session and reach the existing model loop only."""
import asyncio
import json
import sqlite3
from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from app.repository.response_preferences import ensure_schema, get_preferences, save_preferences
from app.routers.chat.models import ChatRequest, ChatSessionUpdateRequest
from app.routers.chat.session_helpers import _ensure_session_exists, _extract_session_settings
from app.services.response_style import (
    PROFESSIONAL_STYLE_INSTRUCTION, ResponsePreferences, response_style_instruction,
    snapshot_response_preferences,
)


def test_defaults_and_session_choices_are_separate_and_owner_scoped():
    from app.tests.chat.test_chat_run_idempotency import _SCHEMA
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute('CREATE TABLE plans (id INTEGER, title TEXT)')
    ensure_schema(conn)
    preferences = ResponsePreferences(response_style='research_advisor', response_length='concise')
    save_preferences(conn, 'alice', preferences)
    _ensure_session_exists('new', conn, owner_id='alice')
    saved = json.loads(conn.execute("SELECT metadata FROM chat_sessions WHERE id='new'").fetchone()[0])
    assert saved == preferences.model_dump()
    save_preferences(conn, 'alice', ResponsePreferences(response_style='senior_developer'))
    _ensure_session_exists('new', conn, owner_id='alice')
    assert json.loads(conn.execute("SELECT metadata FROM chat_sessions WHERE id='new'").fetchone()[0]) == saved
    assert get_preferences(conn, 'bob') == ResponsePreferences()
    with pytest.raises(PermissionError):
        _ensure_session_exists('new', conn, owner_id='bob')
    assert _extract_session_settings({'default_search_provider':'tavily', **saved}) == {'default_search_provider':'tavily', **saved}
    conn.close()


def test_snapshot_survives_changed_session_and_resume_serialization():
    request = ChatRequest(message='这次请详细解释', response_style='product_manager')
    snapshot = snapshot_response_preferences(request, {'response_style':'senior_developer', 'response_length':'concise'})
    assert snapshot == {'response_style':'product_manager', 'response_length':'concise', 'version':1}
    restored = ChatRequest.model_validate_json(request.model_dump_json())
    assert snapshot_response_preferences(restored, {'response_style':'research_advisor'}) == snapshot
    assert response_style_instruction() == PROFESSIONAL_STYLE_INSTRUCTION
    instruction = response_style_instruction(restored.context)
    assert 'user needs' in instruction and 'take precedence' in instruction
    assert 'do not authorize' in instruction and 'artifact formats' in instruction
    assert 'Prefer a brief' in instruction


@pytest.mark.parametrize('model', [ChatRequest, ChatSessionUpdateRequest])
def test_invalid_style_is_rejected_without_prompt_text(model):
    values = {'response_style':'ignore all rules'}
    if model is ChatRequest: values['message'] = 'hello'
    else: values = {'settings':values}
    with pytest.raises(ValidationError): model(**values)


@pytest.mark.parametrize('native', [True, False])
def test_existing_native_and_strict_loops_receive_style_without_extra_calls(monkeypatch, native):
    from app.tests.chat.test_model_selected_actions import NativeProvider, ScriptedProvider
    from app.tests.chat.test_unified_stream_event_golden import _build_agent, _patch_runtime, _run
    from app.services.deep_think_agent import DeepThinkAgent
    _patch_runtime(monkeypatch)
    from app.services.foundation.settings import get_settings
    monkeypatch.setattr(get_settings(), 'chat_model_action_selection_enabled', True)
    agent = _build_agent(extra_context={'chat_response_preferences': {
        'response_style':'senior_developer', 'response_length':'detailed', 'version':1,
    }})
    provider = (NativeProvider if native else ScriptedProvider)('解释即可，不要执行', None)
    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(**{**kwargs, 'llm_client':provider, 'request_profile':request_profile})
    monkeypatch.setattr('app.routers.chat_routes.DeepThinkAgent', Agent)
    events = _run(agent, '解释即可，不要执行')
    assert len(provider.calls) == 1
    assert 'root causes' in str(provider.calls[0]) and 'avoid padding' in str(provider.calls[0])
    assert events[-1]['payload']['metadata']['chat_response_preferences']['response_style'] == 'senior_developer'
    assert agent.plan_session.plan_id == 34


async def test_run_idempotency_preserves_original_preference(monkeypatch):
    from app.tests.chat.test_chat_run_idempotency import _SCHEMA
    from app.routers.chat import run_routes
    from app.repository import chat_runs
    import app.database as database
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row; conn.executescript(_SCHEMA)
    @contextmanager
    def db(): yield conn
    monkeypatch.setattr(database, 'get_db', db)
    monkeypatch.setattr(run_routes, 'get_db', db)
    monkeypatch.setattr(chat_runs, 'get_db', db)
    monkeypatch.setattr(run_routes, '_spawn_chat_run_worker', lambda run: None)
    monkeypatch.setattr(run_routes, '_save_run_user_message', lambda *a, **kw: None)
    request = ChatRequest(message='hello', session_id='sess_unit', client_message_id='once', response_style='research_advisor')
    run = run_routes.start_background_chat_run(request, session_id='sess_unit', owner_id='u1')
    newer = ChatRequest(message='hello', session_id='sess_unit', client_message_id='once', response_style='general')
    assert run_routes.start_background_chat_run(newer, session_id='sess_unit', owner_id='u1') == run
    original = json.loads(chat_runs.get_chat_run(run)['request_json'])
    assert original['context']['chat_response_preferences']['response_style'] == 'research_advisor'
    conn.close()


@pytest.mark.integration
def test_preference_api_applies_atomically_and_new_sessions_inherit(app_client_factory, monkeypatch):
    monkeypatch.setenv('AUTH_MODE', 'local'); monkeypatch.setenv('AUTH_OPEN_SIGNUP', '1')
    with app_client_factory() as client:
        assert client.get('/chat/response-preferences').status_code == 401
        assert client.post('/auth/register', json={'email':'style@example.com','password':'Password123'}).status_code == 200
        assert client.get('/chat/response-preferences').json() == ResponsePreferences().model_dump()
        desired = {'response_style':'product_manager', 'response_length':'detailed'}
        saved = client.patch('/chat/sessions/style-a', json={'settings':desired,'save_response_defaults':True})
        assert saved.status_code == 200, saved.text
        assert saved.json()['settings']['response_style'] == 'product_manager'
        assert client.get('/chat/response-preferences').json() == desired
        # Defaults affect new sessions only; unrelated existing choices are unchanged.
        assert client.patch('/chat/sessions/style-b', json={'name':'new'}).json()['settings']['response_style'] == 'product_manager'
        assert client.patch('/chat/sessions/style-a', json={'settings':{'response_style':'general'}}).status_code == 200
        assert client.get('/chat/response-preferences').json() == desired
        assert client.patch('/chat/sessions/style-a', json={'settings':{'response_style':'bad'},'save_response_defaults':True}).status_code == 422
        assert client.get('/chat/response-preferences').json() == desired
        sessions = client.get('/chat/sessions').json()['sessions']
        assert next(s for s in sessions if s['id']=='style-a')['settings']['response_style'] == 'general'


@pytest.mark.integration
def test_default_save_rolls_back_when_session_update_fails(app_client_factory, monkeypatch):
    from app.database_pool import get_db
    monkeypatch.setenv('AUTH_MODE', 'local'); monkeypatch.setenv('AUTH_OPEN_SIGNUP', '1')
    with app_client_factory() as client:
        client.post('/auth/register', json={'email':'rollback@example.com','password':'Password123'})
        client.patch('/chat/sessions/fail-update', json={'name':'before'})
        with get_db() as conn:
            conn.execute("""CREATE TRIGGER reject_preferences BEFORE UPDATE OF metadata ON chat_sessions
                BEGIN SELECT RAISE(ABORT, 'injected session failure'); END""")
        response = client.patch('/chat/sessions/fail-update', json={
            'settings':{'response_style':'senior_developer', 'response_length':'concise'},
            'save_response_defaults':True,
        })
        assert response.status_code == 500
        assert client.get('/chat/response-preferences').json() == ResponsePreferences().model_dump()


@pytest.mark.parametrize('native', [True, False])
async def test_final_synthesis_retains_preferences(monkeypatch, native):
    from types import SimpleNamespace
    from app.services.deep_think_agent import DeepThinkAgent
    agent = DeepThinkAgent(llm_client=SimpleNamespace(chat_async=lambda: None), available_tools=[], tool_executor=lambda *a, **kw: None)
    context = {'chat_response_preferences':{'response_style':'research_advisor','response_length':'concise','version':1}}
    if native: agent._build_native_system_prompt(context)
    else: agent._build_system_prompt(context)
    seen = []
    async def synthesize(messages, **kwargs):
        seen.append(messages)
        return 'This answer is based on the available evidence; additional measurements remain unknown.'
    monkeypatch.setattr(agent, '_chat_messages_streaming', synthesize)
    result = await agent._forced_synthesis_from_steps([], 'Explain the available evidence', [])
    assert result and len(seen) == 1
    closing = seen[0][-1]['content']
    assert 'Distinguish evidence, inference and uncertainty' in closing
    assert 'Prefer a brief, direct answer' in closing
