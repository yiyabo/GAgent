"""A rejected budget reservation must prevent the network call on every API path."""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.llm as llm
from app.services.execution.llm_observation import ObserverRejected, observer


@pytest.fixture
def observed_client(monkeypatch):
    monkeypatch.setattr(llm, 'is_production', lambda: False)
    client = llm.LLMClient(provider='qwen', api_key='test-only', retries=3, backoff_base=0)
    client.mock = False
    transport = MagicMock()
    monkeypatch.setattr(llm, '_get_shared_sync_client', lambda: transport)
    monkeypatch.setattr(llm, '_get_shared_async_client', lambda: transport)
    limiter = MagicMock()
    limiter.acquire_async = AsyncMock()
    monkeypatch.setattr(llm, '_outbound_limiter', limiter)
    events = []

    def reject(event):
        events.append(event)
        raise ObserverRejected('campaign_attempt_limit')

    handle = observer.set(reject)
    try:
        yield client, transport, events
    finally:
        observer.reset(handle)


@pytest.mark.parametrize('method', ['chat', 'stream_chat'])
def test_rejected_sync_dispatch_never_opens_transport_or_retries(observed_client, method):
    client, transport, events = observed_client
    with pytest.raises(ObserverRejected, match='campaign_attempt_limit'):
        result = getattr(client, method)('hello')
        if method == 'stream_chat':
            list(result)
    transport.post.assert_not_called()
    transport.stream.assert_not_called()
    assert len(events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['chat_async', 'stream_chat_async', 'stream_chat_with_tools_async'])
async def test_rejected_async_dispatch_never_opens_transport_or_retries(observed_client, method):
    client, transport, events = observed_client
    with pytest.raises(ObserverRejected, match='campaign_attempt_limit'):
        if method == 'stream_chat_async':
            async for _ in client.stream_chat_async('hello'):
                pass
        elif method == 'stream_chat_with_tools_async':
            await client.stream_chat_with_tools_async([{'role': 'user', 'content': 'hello'}], [])
        else:
            await client.chat_async('hello')
    transport.post.assert_not_called()
    transport.stream.assert_not_called()
    assert len(events) == 1


def test_network_failure_retains_attempt_before_receiving_headers(monkeypatch):
    monkeypatch.setattr(llm, 'is_production', lambda: False)
    client = llm.LLMClient(provider='qwen', api_key='test-only', retries=0)
    client.mock = False
    transport = MagicMock()
    transport.post.side_effect = httpx.ConnectError('fixture connection failed')
    monkeypatch.setattr(llm, '_get_shared_sync_client', lambda: transport)
    events = []
    handle = observer.set(events.append)
    try:
        with pytest.raises(RuntimeError, match='fixture connection failed'):
            client.chat('hello')
    finally:
        observer.reset(handle)
    assert transport.post.call_count == 1
    assert len(events) == 1
    assert events[0]['kind'] == 'attempt' and events[0]['usage'] is None


@pytest.mark.asyncio
async def test_nonstream_repair_reports_usage_before_the_next_call(monkeypatch):
    monkeypatch.setattr(llm, 'is_production', lambda: False)
    client = llm.LLMClient(provider='qwen', api_key='test-only', retries=0)
    response = MagicMock(status_code=200)
    response.json.return_value = {'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25},
                                 'choices': [{'message': {'content': 'done'}, 'finish_reason': 'stop'}]}
    transport = MagicMock()
    transport.post = AsyncMock(return_value=response)
    monkeypatch.setattr(llm, '_get_shared_async_client', lambda: transport)
    monkeypatch.setattr(llm, '_log_usage', lambda **kwargs: None)
    events = []
    handle = observer.set(events.append)
    try:
        await client._repair_tool_calls_nonstream({'model': 'test-model', 'messages': []}, 'original-call')
    finally:
        observer.reset(handle)
    assert [(e['logical_call_id'], e['attempt_no']) for e in events] == [('original-call', 2)] * 2
    assert events[-1]['usage']['total_tokens'] == 25


def test_observation_write_failure_is_not_retried_as_transport(observed_client):
    client, transport, _ = observed_client
    def broken_journal(event):
        raise OSError('journal unavailable')
    handle = observer.set(broken_journal)
    try:
        with pytest.raises(ObserverRejected, match='observation_failed:OSError'):
            client.chat('hello')
    finally:
        observer.reset(handle)
    transport.post.assert_not_called()
