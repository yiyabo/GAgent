import hashlib
import json
import sqlite3

from app.services.harness_eval.accounting import TrialAccounting
from app.services.harness_eval.config import EvalSuiteConfig
from app.services.harness_eval import process_cleanup, supervisor


def completed_row(root, total=11):
    output = root / 'summary.json'
    output.write_text('{"A":{"count":3,"median":20},"B":{"count":2,"median":40}}')
    return {'total_tokens': total, 'usage_source': 'provider', 'duration_seconds': 3,
            'answer_completion_passed': True, 'production_status': 'succeeded',
            'artifacts': [{'name': output.name, 'path': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}]}


def test_recovery_preserves_completed_result_and_cost_without_launch(tmp_path, monkeypatch):
    cfg = EvalSuiteConfig(cases=['correction'], entries=['plan-native'])
    root = tmp_path / '000-correction-plan-native-0-single'; root.mkdir()
    (root / 'result.json').write_text(json.dumps(completed_row(root)))
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not relaunch')))
    result = supervisor.run_suite(tmp_path, cfg)
    assert result['total_tokens'] == 11
    assert result['passed'] == 1
    assert result['results'][0]['supervisor_recovered']
    assert supervisor.run_suite(tmp_path, cfg)['total_tokens'] == 11


def test_missing_partial_usage_stops_remaining_trials_and_keeps_receipts(tmp_path, monkeypatch):
    cfg = EvalSuiteConfig(cases=['correction', 'figure'], entries=['plan-native'])
    first = cfg.schedule()[0]
    root = tmp_path / f'000-{first["case"]}-plan-native-0-single'; root.mkdir()
    accounting = TrialAccounting(root, cfg)
    accounting.observe({'kind': 'attempt', 'logical_call_id': 'paid', 'attempt_no': 1, 'usage': {'prompt_tokens': 98, 'completion_tokens': 2, 'total_tokens': 100}})
    accounting.observe({'kind': 'attempt', 'logical_call_id': 'lost', 'attempt_no': 1})
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not launch')))
    result = supervisor.run_suite(tmp_path, cfg)
    assert result['known_total_tokens'] == 100
    assert result['total_tokens'] is None
    assert result['started'] == 1
    assert result['stop_reason'] == 'missing_usage_before_next_trial'
    assert result['results'][0]['provider_attempts'] == 2


def test_resume_does_not_reset_suite_active_wall_time(tmp_path, monkeypatch):
    cfg = EvalSuiteConfig(cases=['correction'], entries=['plan-native'], suite_wall_seconds=10)
    (tmp_path / 'supervisor-state.json').write_text(json.dumps({'active_seconds': 10}))
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not launch')))
    result = supervisor.run_suite(tmp_path, cfg)
    assert result['stop_reason'] == 'suite_wall_limit'
    assert result['started'] == 0


def test_normal_worker_exit_also_cleans_observed_descendants(tmp_path, monkeypatch):
    cfg = EvalSuiteConfig(cases=['correction'], entries=['plan-native'])
    cleaned = []
    class Worker:
        pid = 10
        returncode = 0
        def __init__(self, args, **kwargs):
            root = __import__('pathlib').Path(args[args.index('--worker') + 1]).parent
            (root / 'result.json').write_text(json.dumps(completed_row(root)))
            self.polls = 0
        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0
    monkeypatch.setattr(supervisor.subprocess, 'Popen', Worker)
    monkeypatch.setattr(supervisor, 'process_table', lambda: {10: (1, 'worker-start')})
    monkeypatch.setattr(supervisor, 'descendants', lambda pid: {20: 'child-start'})
    monkeypatch.setattr(supervisor, 'cleanup_children', lambda children: cleaned.append(dict(children)) or {'remaining_children': []})
    monkeypatch.setattr(supervisor.time, 'sleep', lambda seconds: None)
    result = supervisor.run_suite(tmp_path, cfg)
    assert result['passed'] == 1
    assert {20: 'child-start'} in cleaned


def test_artifact_receipt_hash_and_fresh_oracle_input_required(tmp_path):
    row = completed_row(tmp_path)
    (tmp_path / 'result.json').write_text(json.dumps(row))
    old = tmp_path / 'oracle-input'; old.mkdir()
    (old / 'summary.json').write_bytes((tmp_path / 'summary.json').read_bytes())
    (tmp_path / 'summary.json').write_text('tampered')
    result = supervisor.score_result(tmp_path, {'case': 'correction'})
    assert result['passed'] is False
    assert result['error'] == 'delivered_artifact_hash_mismatch'
    assert not (old / 'summary.json').exists()


def test_cleanup_does_not_signal_reused_pid(monkeypatch):
    monkeypatch.setattr(process_cleanup, 'process_table', lambda: {10: (1, 'new-start')})
    monkeypatch.setattr(process_cleanup.os, 'kill', lambda *a: (_ for _ in ()).throw(AssertionError('wrong process')))
    assert process_cleanup.cleanup_children({10: 'old-start'})['remaining_children'] == []


def test_supervisor_terminal_cleanup_is_scoped_to_isolated_eval_owner(tmp_path):
    database = tmp_path / 'db_root/main/plan_registry.db'; database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as con:
        con.execute('CREATE TABLE chat_runs(run_id TEXT,worker_id TEXT,owner_id TEXT,status TEXT,lease_expires_at TEXT,error TEXT,assistant_message_id INTEGER,finished_at TEXT)')
        con.executemany('INSERT INTO chat_runs VALUES (?,?,?,?,?,NULL,NULL,NULL)', [
            ('eval', 'worker', 'harness-eval', 'running', '2099-01-01'),
            ('business', 'business-worker', 'user', 'running', '2099-01-01')])
    assert process_cleanup.close_isolated_runs(tmp_path)['remaining_active_eval_runs'] == 0
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT status,lease_expires_at FROM chat_runs WHERE run_id='eval'").fetchone() == ('failed', None)
        assert con.execute("SELECT status,lease_expires_at FROM chat_runs WHERE run_id='business'").fetchone() == ('running', '2099-01-01')
