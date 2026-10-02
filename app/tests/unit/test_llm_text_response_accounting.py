"""A completed reasoning-only response is billed once and is not a transport retry."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.llm as llm
from app.services.execution.llm_observation import observer
from app.services.llm.llm_service import LLMService, LLMProviderError


@pytest.mark.asyncio
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('finish_reason,code', [('length', 'llm_output_truncated'), ('stop', 'llm_missing_assistant_text')])
async def test_reasoning_without_answer_preserves_usage_and_does_not_retry(monkeypatch, asynchronous, finish_reason, code):
    monkeypatch.setattr(llm, 'is_production', lambda: False)
    client = llm.LLMClient(provider='qwen', api_key='test-only', retries=2, backoff_base=0)
    client.mock = False
    usage = {'prompt_tokens': 590, 'completion_tokens': 500, 'total_tokens': 1090}
    response = MagicMock(status_code=200, headers={'x-request-id': 'receipt-1'})
    response.json.return_value = {'choices': [{'message': {'role': 'assistant', 'reasoning_content': 'reasoning only'},
                                              'finish_reason': finish_reason}], 'usage': usage}
    transport = MagicMock()
    transport.post = AsyncMock(return_value=response) if asynchronous else MagicMock(return_value=response)
    monkeypatch.setattr(llm, '_get_shared_async_client', lambda: transport)
    monkeypatch.setattr(llm, '_get_shared_sync_client', lambda: transport)
    logged, events = [], []
    monkeypatch.setattr(llm, '_log_usage', lambda **kw: logged.append(kw))
    handle = observer.set(events.append)
    try:
        service = LLMService(client=client)
        with pytest.raises(LLMProviderError) as caught:
            if asynchronous:
                await service.chat_async('Summarize the delivered files.')
            else:
                service.chat('Summarize the delivered files.')
    finally:
        observer.reset(handle)
    assert caught.value.error_code == code
    assert caught.value.retryable is False
    assert transport.post.call_count == 1
    assert [row['total_tokens'] for row in logged] == [1090]
    receipts = [event for event in events if event['kind'] == 'attempt' and event.get('usage')]
    assert len(receipts) == 1 and receipts[0]['usage'] == usage
    assert events[-1]['kind'] == 'text_result'
    assert events[-1]['finish_reason'] == finish_reason
    assert 'reasoning only' not in str(caught.value)


def test_missing_usage_text_result_stops_next_evaluation_call(tmp_path):
    from app.services.harness_eval.accounting import EvaluationLimit, TrialAccounting
    from app.services.harness_eval.config import EvalSuiteConfig
    accounting = TrialAccounting(tmp_path, EvalSuiteConfig())
    accounting.observe({'kind': 'attempt', 'logical_call_id': 'a', 'attempt_no': 1})
    accounting.observe({'kind': 'text_result', 'logical_call_id': 'a', 'attempt_no': 1, 'usage': None})
    with pytest.raises(EvaluationLimit, match='missing_usage'):
        accounting.observe({'kind': 'attempt', 'logical_call_id': 'b', 'attempt_no': 1})
    assert accounting.summary()['total_tokens'] is None
