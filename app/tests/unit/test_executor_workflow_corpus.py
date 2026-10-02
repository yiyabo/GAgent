import json
import csv
from app.services.harness_eval.corpus import prepare,check


def test_independent_oracle_rejects_wrong_stats_even_if_files_exist(tmp_path):
    prepare('table_clean',tmp_path)
    (tmp_path/'clean.csv').write_text('id,group,score\na,A,10\nb,A,20\nc,B,30\nd,B,50\n')
    (tmp_path/'summary.json').write_text('{"A":{"count":2,"mean":999},"B":{"count":2,"mean":40}}')
    assert check('table_clean',tmp_path)['passed'] is False
    (tmp_path/'summary.json').write_text('{"A":{"count":2,"mean":15},"B":{"count":2,"mean":40}}')
    assert check('table_clean',tmp_path)['passed'] is True


def test_correction_oracle_rejects_stale_mean_fields(tmp_path):
    prepare('correction',tmp_path)
    assert not check('correction',tmp_path)['passed']
    (tmp_path/'summary.json').write_text('{"A":{"count":3,"median":20},"B":{"count":2,"median":40}}')
    assert check('correction',tmp_path)['passed']


def test_literature_oracle_keeps_scientific_review_distinct(tmp_path):
    prepare('literature_report',tmp_path)
    (tmp_path/'report.md').write_text('S1 S2 Findings and limitations\n'+('observational evidence; confounding; small sample. '*4))
    (tmp_path/'evidence.json').write_text('{"cited_ids":["S1","S2","invented"]}')
    result=check('literature_report',tmp_path)
    assert not result['passed'] and result['manual_review_required']


async def test_evaluation_meter_respects_native_client_signature_and_call_cap():
    from app.services.harness_eval.runner import MeteredLLM
    from app.llm import NativeStreamResult
    import pytest
    class Client:
        async def stream_chat_with_tools_async(self,messages,tools,tool_choice='auto'):
            return NativeStreamResult(content='ok')
    client=MeteredLLM(Client())
    for _ in range(5):
        assert (await client.stream_chat_with_tools_async(messages=[],tools=[])).content=='ok'
    with pytest.raises(RuntimeError,match='call limit'):
        await client.stream_chat_with_tools_async(messages=[],tools=[])


def test_oracle_checks_requested_values_without_rejecting_extra_correct_statistics(tmp_path):
    prepare('table_clean',tmp_path)
    (tmp_path/'clean.csv').write_text('id,group,score\na,A,10\nb,A,20\nc,B,30\nd,B,50\n')
    (tmp_path/'summary.json').write_text('{"A":{"count":2,"mean":15,"median":15},"B":{"count":2,"mean":40,"median":40}}')
    assert check('table_clean',tmp_path)['passed']
    (tmp_path/'summary.json').write_text('{"A":{"count":2,"mean":"15"},"B":{"count":2,"mean":40}}')
    assert not check('table_clean',tmp_path)['passed']
    prepare('correction',tmp_path)
    (tmp_path/'summary.json').write_text('{"A":{"count":3,"median":20,"mean":40},"B":{"count":2,"median":40}}')
    assert not check('correction',tmp_path)['passed']
