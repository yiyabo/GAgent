import json
from dataclasses import replace

import pytest

from app.services.harness_eval.accounting import (
    CampaignLedger, EvaluationLimit, TrialAccounting, read_events, recover_result,
)
from app.services.harness_eval.config import EvalSuiteConfig


def attempt(accounting, call='first', usage=None, number=1):
    accounting.observe({'kind': 'attempt', 'logical_call_id': call, 'attempt_no': number, 'usage': usage})


def usage(total=10):
    return {'prompt_tokens': total - 2, 'completion_tokens': 2, 'total_tokens': total}


def test_duplicate_receipts_and_cli_database_projection_do_not_double_bill(tmp_path):
    accounting = TrialAccounting(tmp_path, EvalSuiteConfig())
    attempt(accounting)
    attempt(accounting, usage=usage())
    attempt(accounting, usage=usage())
    accounting.observe({'kind': 'native_result', 'usage': usage(), 'arguments': [{'name': 'file_operations'}]})
    accounting.observe({'kind': 'external_launch', 'backend': 'qwen_code'})
    cli = {**usage(20), 'provider': 'qwen_code_cli', 'model': 'test'}
    accounting.observe({'kind': 'external_usage', 'usage': cli, 'usage_source': 'estimated'})
    accounting.reconcile_rows([
        {'id': 1, 'logical_call_id': 'first', 'attempt_no': 1, **usage()},
        {'id': 2, **cli},
    ])
    summary = accounting.summary()
    assert summary['total_tokens'] == 30
    assert summary['provider_attempts'] == 1
    assert summary['external_launches'] == 1
    assert summary['usage_breakdown'] == {'provider': 10, 'estimated': 20}
    assert read_events(tmp_path / 'calls.jsonl') == accounting.events
    assert json.loads(next(tmp_path.glob('arguments-*.json')).read_text())[0]['name'] == 'file_operations'


def test_missing_usage_is_null_and_blocks_following_request(tmp_path):
    accounting = TrialAccounting(tmp_path, EvalSuiteConfig())
    attempt(accounting)
    accounting.observe({'kind': 'native_result', 'usage': None})
    with pytest.raises(EvaluationLimit, match='missing_usage'):
        attempt(accounting, 'second')
    result = recover_result(tmp_path)
    assert result['total_tokens'] is None
    assert result['known_total_tokens'] == 0
    assert result['provider_attempts'] == 1
    assert result['usage_complete'] is False


def test_post_response_threshold_denies_next_call_but_records_crossing_response(tmp_path):
    accounting = TrialAccounting(tmp_path, EvalSuiteConfig(per_trial_token_stop_threshold=9))
    attempt(accounting)
    attempt(accounting, usage=usage(10))
    with pytest.raises(EvaluationLimit, match='trial_token_stop'):
        attempt(accounting, 'second')
    assert accounting.summary()['total_tokens'] == 10
    assert accounting.summary()['provider_attempts'] == 1


def test_attempt_limit_counts_retry_attempt_separately_and_not_usage_updates(tmp_path):
    accounting = TrialAccounting(tmp_path, EvalSuiteConfig(provider_attempt_limit=2))
    attempt(accounting)
    attempt(accounting, usage=usage())
    attempt(accounting, number=2)
    attempt(accounting, number=2, usage=usage())
    with pytest.raises(EvaluationLimit, match='provider_attempt_limit'):
        attempt(accounting, number=3)
    assert accounting.summary()['total_tokens'] == 20


def test_cli_missing_source_keeps_known_usage_and_does_not_invent_extra_launch(tmp_path):
    accounting = TrialAccounting(tmp_path, EvalSuiteConfig())
    accounting.observe({'kind': 'external_launch', 'backend': 'claude_code'})
    accounting.reconcile_rows([{'id': 4, 'provider': 'claude_code_cli', 'model': 'test', **usage(30)}])
    result = accounting.summary()
    assert result['external_launches'] == 1
    assert result['known_total_tokens'] == 30
    assert result['total_tokens'] is None
    assert result['usage_source'] == 'missing'


def test_campaign_survives_new_objects_and_different_suite_roots(tmp_path):
    cfg = EvalSuiteConfig(campaign_root=str(tmp_path / 'campaign'), campaign_provider_attempt_limit=2)
    first = tmp_path / 'suite-a' / 'trial'
    first.mkdir(parents=True)
    CampaignLedger(cfg).start_trial(first)
    meter = TrialAccounting(first, cfg)
    attempt(meter); attempt(meter, usage=usage())
    CampaignLedger(cfg).recover_trial(first, {**meter.summary(), 'call_events': meter.events}, 4)
    second = tmp_path / 'suite-b' / 'trial'
    second.mkdir(parents=True)
    CampaignLedger(cfg).start_trial(second)
    meter2 = TrialAccounting(second, cfg)
    attempt(meter2, 'second'); attempt(meter2, 'second', usage=usage())
    with pytest.raises(EvaluationLimit, match='campaign_provider_attempt_limit'):
        attempt(meter2, 'third')
    assert CampaignLedger(cfg).state()['known_total_tokens'] == 20
    assert CampaignLedger(cfg).state()['started_trials'] == 2
    assert CampaignLedger(cfg).state()['active_seconds'] == 4
    with pytest.raises(ValueError, match='limits changed'):
        CampaignLedger(replace(cfg, campaign_provider_attempt_limit=10))


def test_campaign_unsettled_or_missing_trial_cannot_be_bypassed_by_new_suite(tmp_path):
    cfg = EvalSuiteConfig(campaign_root=str(tmp_path / 'campaign'))
    ledger = CampaignLedger(cfg)
    first = tmp_path / 'first'; first.mkdir()
    ledger.start_trial(first)
    with pytest.raises(EvaluationLimit, match='unsettled'):
        CampaignLedger(cfg).start_trial(tmp_path / 'second')
    meter = TrialAccounting(first, cfg)
    attempt(meter)
    ledger.recover_trial(first, {**meter.summary(), 'call_events': meter.events}, 5)
    with pytest.raises(EvaluationLimit, match='missing_usage'):
        CampaignLedger(cfg).start_trial(tmp_path / 'second')
    assert CampaignLedger(cfg).state()['total_tokens'] is None


@pytest.mark.parametrize('field,value,reason', [
    ('campaign_trial_limit', 1, 'campaign_trial_limit'),
    ('campaign_wall_seconds', 4, 'campaign_wall_limit'),
    ('campaign_token_stop_threshold', 5, 'campaign_token_stop'),
])
def test_finished_campaign_caps_remain_enforced_on_restart(tmp_path, field, value, reason):
    cfg = replace(EvalSuiteConfig(campaign_root=str(tmp_path / 'campaign')), **{field: value})
    first = tmp_path / 'first'; first.mkdir()
    ledger = CampaignLedger(cfg); ledger.start_trial(first)
    meter = TrialAccounting(first, cfg); attempt(meter); attempt(meter, usage=usage())
    ledger.recover_trial(first, {**meter.summary(), 'call_events': meter.events}, 5)
    with pytest.raises(EvaluationLimit, match=reason):
        CampaignLedger(cfg).start_trial(tmp_path / 'next')


def test_campaign_nested_cli_launch_is_reserved_before_dispatch(tmp_path):
    cfg = EvalSuiteConfig(campaign_root=str(tmp_path / 'campaign'), campaign_external_launch_limit=0)
    root = tmp_path / 'native'; root.mkdir()
    CampaignLedger(cfg).start_trial(root)
    meter = TrialAccounting(root, cfg)
    with pytest.raises(EvaluationLimit, match='campaign_external_launch_limit'):
        meter.observe({'kind': 'external_launch', 'backend': 'qwen_code'})
    assert meter.summary()['external_launches'] == 0


def test_configuration_resets_ambient_experimental_flags(tmp_path, monkeypatch):
    from app.services.harness_eval.trial import configure
    import os
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    for key in ('AGENT_RUNTIME_V2_ENABLED', 'ARTIFACT_VERSIONING_ENABLED', 'SKILL_RECOMMENDATION_V2_ENABLED', 'SKILL_CONTEXT_PROGRESSIVE_ENABLED', 'CHAT_RUN_SYNTHESIS_RESERVE_SECONDS'):
        monkeypatch.setenv(key, '1')
    configure(tmp_path, EvalSuiteConfig(feature_overrides={'ARTIFACT_VERSIONING_ENABLED': '1'}), 'plan-native')
    assert os.environ['AGENT_RUNTIME_V2_ENABLED'] == '0'
    assert os.environ['CHAT_RUN_SYNTHESIS_RESERVE_SECONDS'] == '0'
    assert os.environ['ARTIFACT_VERSIONING_ENABLED'] == '1'


@pytest.mark.parametrize('override', [
    {'campaign_root': '/different-ledger'}, {'campaign_provider_attempt_limit': 10000},
    {'per_trial_token_stop_threshold': None}, {'provider_attempt_limit': 500},
])
def test_variant_cannot_reset_or_expand_evaluation_budget(override):
    with pytest.raises(ValueError, match='variant cannot override'):
        EvalSuiteConfig(variants={'candidate': override}).validate()


@pytest.mark.parametrize('overrides', [
    {'AGENT_RUNTIME_V2_ENABLED': 'yes'}, {'ARTIFACT_VERSIONING_ENABLED': True},
    {'CHAT_RUN_SYNTHESIS_RESERVE_SECONDS': 'nan'}, {'CHAT_RUN_SYNTHESIS_RESERVE_SECONDS': '-1'},
])
def test_invalid_feature_override_fails_before_environment_changes(overrides):
    with pytest.raises(ValueError):
        EvalSuiteConfig(feature_overrides=overrides).validate()


def test_recovery_reads_billed_cli_row_if_worker_died_before_observer_receipt(tmp_path):
    import sqlite3
    database = tmp_path / 'db_root/main/plan_registry.db'; database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as con:
        con.execute('CREATE TABLE chat_sessions(id TEXT,owner_id TEXT)')
        con.execute("INSERT INTO chat_sessions VALUES ('eval','harness-eval')")
        con.execute('CREATE TABLE llm_usage_log(id INTEGER,session_id TEXT,provider TEXT,model TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER)')
        con.execute("INSERT INTO llm_usage_log VALUES (1,'eval','qwen_code_cli','test',98,2,100)")
    meter = TrialAccounting(tmp_path, EvalSuiteConfig())
    meter.observe({'kind': 'external_launch', 'backend': 'qwen_code'})
    recovered = recover_result(tmp_path)
    assert recovered['known_total_tokens'] == 100
    assert recovered['total_tokens'] is None
    assert recovered['external_launches'] == 1
    assert recover_result(tmp_path)['known_total_tokens'] == 100
