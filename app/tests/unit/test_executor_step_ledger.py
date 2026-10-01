from __future__ import annotations

import asyncio
import fcntl
import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from app.repository import run_steps as repo
from app.services.chat_run_state import chat_run_claim
from app.services import cancellation
from app.services.execution.step_ledger import (
    ControllerCheckpoint, JsonBlobStore, LedgerCancelled, OutputReference,
    RemainingBudget, ResultUnavailable, StepLedger, params_fingerprint,
)


def _hold_shared_checkpoint_lock(path, ready, release):
    """A separate process acts as a pointer+blob reader for the lock test."""
    with Path(path).open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        ready.set()
        release.wait(timeout=10)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.sqlite'

    @contextmanager
    def connect():
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            yield conn
        finally:
            conn.close()

    with connect() as conn:
        conn.execute("""CREATE TABLE chat_runs(run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            owner_id TEXT NOT NULL, status TEXT NOT NULL, worker_id TEXT, lease_expires_at TEXT)""")
        conn.execute("""INSERT INTO chat_runs VALUES('run-a','session-a','owner-a','running','claim-a',datetime('now','+300 seconds'))""")
        repo.ensure_run_step_schema(conn)
    monkeypatch.setattr(repo, 'get_db', connect)
    claim_handle = chat_run_claim.set(None)
    cancel_handle = cancellation.set_cancel_token(None)
    fixture = SimpleNamespace(path=path, connect=connect, blobs=tmp_path / 'blobs')
    fixture.ledger = StepLedger('run-a', fixture.blobs, 'claim-a')
    yield fixture
    chat_run_claim.reset(claim_handle)
    cancellation.reset_cancel_token(cancel_handle)


def _prepare_claim(db, policy='read_only', call='slot-1', params=None):
    decision = db.ledger.prepare(call, 'file_operations', params or {'operation': 'read'}, replay_policy=policy)
    assert decision.action == 'execute'
    assert db.ledger.claim(decision.step.key)
    return decision.step.key


def _replace_claim(db, worker='claim-b'):
    with db.connect() as conn:
        conn.execute("UPDATE chat_runs SET worker_id=?,lease_expires_at=datetime('now','+300 seconds') WHERE run_id='run-a'", (worker,))
    return StepLedger('run-a', db.blobs, worker)


def test_schema_is_idempotent_and_completed_observation_survives_connection_restart(ledger_db):
    key = _prepare_claim(ledger_db)
    completed = ledger_db.ledger.complete(key, {'success': True, 'count': 3})
    with ledger_db.connect() as conn:
        repo.ensure_run_step_schema(conn)
        repo.ensure_run_step_schema(conn)
        assert conn.execute('SELECT count(*) FROM run_steps').fetchone()[0] == 1
    restarted = StepLedger('run-a', ledger_db.blobs, 'claim-a')
    decision = restarted.prepare('slot-1', 'file_operations', {'operation': 'read'}, replay_policy='read_only')
    assert decision.action == 'replay'
    assert decision.result == {'success': True, 'count': 3}
    assert decision.step.result_ref == completed.result_ref


def test_initialization_adds_schema_to_real_database_idempotently(isolated_app_env):
    from app.database import get_db, init_db
    init_db()
    init_db()
    with get_db() as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'run_steps', 'run_checkpoints'} <= names


def test_fingerprint_is_order_independent_and_repeated_provider_ids_do_not_replay_different_params(ledger_db):
    first = ledger_db.ledger.prepare('reused-id', 'reader', {'a': 1, 'b': 2}, replay_policy='read_only')
    assert ledger_db.ledger.claim(first.step.key)
    ledger_db.ledger.complete(first.step.key, {'value': 'first'})
    reordered = ledger_db.ledger.prepare('reused-id', 'reader', {'b': 2, 'a': 1}, replay_policy='read_only')
    changed = ledger_db.ledger.prepare('reused-id', 'reader', {'a': 2, 'b': 2}, replay_policy='read_only')
    assert reordered.action == 'replay'
    assert changed.action == 'execute'
    assert changed.step.key.params_fingerprint != first.step.key.params_fingerprint
    assert len(repo.list_steps('run-a')) == 2


def test_raw_parameters_results_and_exception_payloads_are_not_in_sqlite(ledger_db):
    params = {'api_key': 'parameter-secret', 'operation': 'read'}
    decision = ledger_db.ledger.prepare('slot-secret', 'reader', params, replay_policy='read_only')
    assert ledger_db.ledger.claim(decision.step.key)
    completed = ledger_db.ledger.complete(decision.step.key, {'success': True, 'api_key': 'result-secret', 'value': 3})
    assert ledger_db.ledger.load_result(completed)['api_key'] == '[REDACTED]'
    with ledger_db.connect() as conn:
        dump = '\n'.join(conn.iterdump())
    assert 'parameter-secret' not in dump and 'result-secret' not in dump
    with pytest.raises(ValueError):
        repo.end_step(decision.step.key, 'failed', error_code='Exception containing raw token', worker_id='claim-a')


def test_same_step_policy_cannot_be_reclassified_to_bypass_mutation_guard(ledger_db):
    key = _prepare_claim(ledger_db, 'mutating')
    with pytest.raises(repo.StepStateConflict):
        ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='read_only')


def test_concurrent_claims_of_same_step_have_one_winner(ledger_db):
    decision = ledger_db.ledger.prepare('slot-concurrent', 'reader', {}, replay_policy='read_only')
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=3)
        return repo.claim_step(decision.step.key, worker_id='claim-a')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(results) == [False, True]
    assert repo.get_step(decision.step.key).status == 'running'


def test_submitted_but_unstarted_mutation_is_safe_to_claim_after_replacement(ledger_db):
    decision = ledger_db.ledger.prepare('slot-unstarted', 'writer', {}, replay_policy='mutating')
    replacement = _replace_claim(ledger_db)
    restored = replacement.prepare('slot-unstarted', 'writer', {}, replay_policy='mutating')
    assert restored.action == 'execute'
    assert restored.step.key == decision.step.key
    assert replacement.claim(restored.step.key)


@pytest.mark.parametrize('policy', ['read_only', 'idempotent'])
def test_ambiguous_safe_step_gets_a_new_attempt_after_claim_replacement(ledger_db, policy):
    key = _prepare_claim(ledger_db, policy)
    replacement = _replace_claim(ledger_db)
    decision = replacement.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy=policy)
    assert decision.action == 'execute'
    assert decision.step.key.attempt == 2
    assert repo.get_step(key).status == 'interrupted'
    assert replacement.claim(decision.step.key)
    with pytest.raises(repo.StaleRunClaim):
        ledger_db.ledger.complete(key, {'late': True})


def test_ambiguous_mutation_interrupts_and_requires_explicit_reconciliation(ledger_db):
    key = _prepare_claim(ledger_db, 'mutating')
    replacement = _replace_claim(ledger_db)
    decision = replacement.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='mutating')
    assert decision.action == 'reconcile'
    assert decision.step.status == 'interrupted'
    assert len(repo.list_steps('run-a')) == 1
    replacement.reconcile(key, {'success': True, 'verified_operation_id': 'op-123'})
    assert replacement.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='mutating').action == 'replay'


@pytest.mark.parametrize('policy,action', [('read_only', 'execute'), ('mutating', 'reconcile')])
def test_observed_failure_retries_only_when_policy_is_safe(ledger_db, policy, action):
    key = _prepare_claim(ledger_db, policy)
    ledger_db.ledger.fail(key, error_code='tool_failed')
    decision = ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy=policy)
    assert decision.action == action
    assert repo.get_step(key).status == 'failed'


def test_live_duplicate_execution_is_busy_and_terminal_result_cannot_be_overwritten(ledger_db):
    key = _prepare_claim(ledger_db)
    assert ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='read_only').action == 'busy'
    result = ledger_db.ledger.complete(key, {'value': 1})
    with pytest.raises(repo.StepStateConflict):
        ledger_db.ledger.complete(key, {'value': 2})
    assert repo.get_step(key).result_ref == result.result_ref


def test_expired_claim_cannot_write_step_or_checkpoint_and_inherited_claim_cannot_be_spoofed(ledger_db):
    key = _prepare_claim(ledger_db)
    with ledger_db.connect() as conn:
        conn.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-1 seconds')")
    with pytest.raises(repo.StaleRunClaim):
        ledger_db.ledger.fail(key)
    with pytest.raises(repo.StaleRunClaim):
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a'))
    _replace_claim(ledger_db)
    handle = chat_run_claim.set(('run-a', 'claim-a'))
    try:
        with pytest.raises(repo.StaleRunClaim):
            repo.assert_run_owned('run-a', worker_id='claim-b')
    finally:
        chat_run_claim.reset(handle)


@pytest.mark.parametrize('policy, action', [('read_only', 'execute'), ('mutating', 'reconcile')])
@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_missing_or_modified_observation_never_replays_unverified_mutation(ledger_db, policy, action, damage):
    key = _prepare_claim(ledger_db, policy)
    result = ledger_db.ledger.complete(key, {'value': 1})
    path = ledger_db.blobs / f'{result.result_ref}.json'
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{"value":2}')
    decision = ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy=policy)
    assert decision.action == action
    assert repo.get_step(key).status == 'succeeded'
    if policy == 'mutating':
        ledger_db.ledger.reconcile(key, {'verified': True})
        assert ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy=policy).result == {'verified': True}


def test_output_content_checksum_change_requires_reconciliation_for_mutation(ledger_db, tmp_path):
    output = tmp_path / 'report.txt'
    output.write_text('version one')
    key = _prepare_claim(ledger_db, 'mutating')
    ledger_db.ledger.complete(key, {'success': True}, output_refs=[output])
    output.write_text('version two')
    decision = ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='mutating')
    assert decision.action == 'reconcile'


def test_failure_after_sql_terminal_write_rolls_back_to_running_for_safe_resume(ledger_db, monkeypatch):
    key = _prepare_claim(ledger_db, 'mutating')
    original = ledger_db.connect
    class FaultConnection:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, key): return getattr(self.conn, key)
        def execute(self, sql, args=()):
            cursor = self.conn.execute(sql, args)
            if "SET status='succeeded',result_ref" in sql:
                raise RuntimeError('injected crash before commit')
            return cursor
    @contextmanager
    def fault_db():
        with original() as conn:
            yield FaultConnection(conn)
    monkeypatch.setattr(repo, 'get_db', fault_db)
    with pytest.raises(RuntimeError, match='injected crash'):
        ledger_db.ledger.complete(key, {'external_operation_completed': True})
    monkeypatch.setattr(repo, 'get_db', original)
    assert repo.get_step(key).status == 'running'
    assert repo.get_step(key).result_ref is None
    replacement = _replace_claim(ledger_db)
    assert replacement.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='mutating').action == 'reconcile'


def test_checkpoint_restart_restores_serializable_state_and_remaining_budget(ledger_db):
    cp = ControllerCheckpoint('run-a', phase='after_tools', iteration=4,
        messages=[{'role': 'user', 'content': 'analyze'}], tool_result_refs=[],
        control_counters={'failures': 2, 'paused': False}, remaining_budget=RemainingBudget(6, 1000, 12.5),
        controller_state={'confidence': 0.6, 'handoff_reserve_used': 1})
    assert ledger_db.ledger.save_checkpoint(cp, expected_version=0) == 1
    restored = StepLedger('run-a', ledger_db.blobs).load_checkpoint()
    assert restored == cp
    assert restored.remaining_budget.iterations == 6
    assert restored.remaining_budget.seconds == 12.5
    with pytest.raises(repo.StepStateConflict):
        ledger_db.ledger.save_checkpoint(cp, expected_version=0)
    assert ledger_db.ledger.load_checkpoint() == cp


def test_snapshot_ref_write_failure_keeps_prior_checkpoint(ledger_db, monkeypatch):
    first = ControllerCheckpoint('run-a', iteration=2, remaining_budget=RemainingBudget(8))
    ledger_db.ledger.save_checkpoint(first)
    monkeypatch.setattr(repo, 'save_checkpoint_pointer', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('checkpoint write fault')))
    with pytest.raises(RuntimeError):
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=3, remaining_budget=RemainingBudget(7)))
    assert ledger_db.ledger.load_checkpoint() == first


def test_claim_replacement_between_blob_write_and_commit_fences_the_late_result(ledger_db, monkeypatch):
    key = _prepare_claim(ledger_db, 'mutating')
    save = ledger_db.ledger.blobs.save
    def fenced_save(value):
        result = save(value)
        _replace_claim(ledger_db)
        return result
    monkeypatch.setattr(ledger_db.ledger.blobs, 'save', fenced_save)
    with pytest.raises(repo.StaleRunClaim):
        ledger_db.ledger.complete(key, {'external_operation_completed': True})
    assert repo.get_step(key).status == 'running'
    assert repo.get_step(key).result_ref is None
    assert StepLedger('run-a', ledger_db.blobs, 'claim-b').prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='mutating').action == 'reconcile'


def test_claim_replacement_while_loading_observation_fences_the_replay_decision(ledger_db, monkeypatch):
    key = _prepare_claim(ledger_db)
    ledger_db.ledger.complete(key, {'value': 1})
    load = ledger_db.ledger.blobs.load
    def fenced_load(ref, checksum):
        result = load(ref, checksum)
        _replace_claim(ledger_db)
        return result
    monkeypatch.setattr(ledger_db.ledger.blobs, 'load', fenced_load)
    with pytest.raises(repo.StaleRunClaim):
        ledger_db.ledger.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='read_only')


def test_concurrent_checkpoint_compare_and_swap_has_one_winner(ledger_db):
    barrier = Barrier(2)
    def save(iteration):
        barrier.wait(timeout=3)
        try:
            return ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=iteration), expected_version=0)
        except repo.StepStateConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert results.count(1) == 1 and results.count('conflict') == 1
    assert repo.load_checkpoint_pointer('run-a')['version'] == 1


def test_checkpoint_redacts_credential_fields_inside_json_tool_message(ledger_db):
    checkpoint = ControllerCheckpoint('run-a', messages=[{'role': 'tool', 'content': '{"api_key":"private-result","value":3}'}])
    ledger_db.ledger.save_checkpoint(checkpoint)
    assert 'private-result' not in ledger_db.ledger.load_checkpoint().messages[0]['content']
    assert '"value": 3' in ledger_db.ledger.load_checkpoint().messages[0]['content']


@pytest.mark.parametrize('bad', [lambda: None, float('nan'), object()])
def test_checkpoints_reject_nonserializable_or_nonfinite_controller_state(bad):
    with pytest.raises(ValueError):
        ControllerCheckpoint('run-a', controller_state={'invalid': bad})


def test_new_run_import_preserves_terminal_source_and_copies_checkpoint_and_results(ledger_db):
    completed_key = _prepare_claim(ledger_db, 'mutating', 'done')
    completed = ledger_db.ledger.complete(completed_key, {'operation': 'confirmed'})
    _prepare_claim(ledger_db, 'read_only', 'safe-inflight')
    _prepare_claim(ledger_db, 'mutating', 'unsafe-inflight')
    ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=5, messages=[{'role': 'assistant', 'content': 'working'}], remaining_budget=RemainingBudget(3)))
    with ledger_db.connect() as conn:
        conn.execute("UPDATE chat_runs SET status='failed',lease_expires_at=NULL WHERE run_id='run-a'")
        conn.execute("INSERT INTO chat_runs VALUES('run-resume','session-a','owner-a','running','claim-new',datetime('now','+300 seconds'))")
    source_before = repo.list_steps('run-a')
    resumed = StepLedger('run-resume', ledger_db.blobs, 'claim-new')
    assert resumed.import_from('run-a') == 3
    assert resumed.import_from('run-a') == 0
    assert repo.list_steps('run-a') == source_before
    replay = resumed.prepare('done', 'file_operations', {'operation': 'read'}, replay_policy='mutating')
    assert replay.action == 'replay' and replay.step.result_ref == completed.result_ref
    assert resumed.prepare('safe-inflight', 'file_operations', {'operation': 'read'}, replay_policy='read_only').step.key.attempt == 2
    assert resumed.prepare('unsafe-inflight', 'file_operations', {'operation': 'read'}, replay_policy='mutating').action == 'reconcile'
    checkpoint = resumed.load_checkpoint()
    assert checkpoint.run_id == 'run-resume' and checkpoint.iteration == 5 and checkpoint.remaining_budget.iterations == 3
    with ledger_db.connect() as conn:
        assert conn.execute("SELECT status FROM chat_runs WHERE run_id='run-a'").fetchone()[0] == 'failed'


@pytest.mark.parametrize('session,owner,status', [('session-other','owner-a','failed'), ('session-a','owner-other','failed'), ('session-a','owner-a','running')])
def test_resume_import_rejects_other_owner_session_or_nonterminal_source(ledger_db, session, owner, status):
    with ledger_db.connect() as conn:
        conn.execute('UPDATE chat_runs SET session_id=?,owner_id=?,status=? WHERE run_id=?', (session, owner, status, 'run-a'))
        conn.execute("INSERT INTO chat_runs VALUES('run-resume','session-a','owner-a','running','claim-new',datetime('now','+300 seconds'))")
    with pytest.raises(repo.StepStateConflict):
        StepLedger('run-resume', ledger_db.blobs, 'claim-new').import_from('run-a')


def test_cancel_token_stops_output_hashing_and_blob_work_without_committing_success(ledger_db, tmp_path):
    output = tmp_path / 'report.txt'
    output.write_bytes(b'x' * 200000)
    key = _prepare_claim(ledger_db)
    token = cancellation.CancelToken()
    token.set('budget_expired')
    handle = cancellation.set_cancel_token(token)
    try:
        with pytest.raises(LedgerCancelled):
            ledger_db.ledger.complete(key, {'success': True}, output_refs=[output])
        assert repo.get_step(key).status == 'running'
        ledger_db.ledger.interrupt(key, error_code='budget_expired')
    finally:
        cancellation.reset_cancel_token(handle)
    assert repo.get_step(key).status == 'interrupted'


def test_cancellation_arriving_between_file_chunks_stops_hash_validation(ledger_db, tmp_path, monkeypatch):
    import app.services.execution.step_ledger as service
    output = tmp_path / 'large.bin'
    output.write_bytes(b'x' * 300000)
    token = cancellation.CancelToken()
    handle = cancellation.set_cancel_token(token)
    check = service._check_cancel
    checked = 0
    def mid_hash_cancel():
        nonlocal checked
        checked += 1
        if checked == 3:
            token.set('budget_expired')
        check()
    monkeypatch.setattr(service, '_check_cancel', mid_hash_cancel)
    try:
        with pytest.raises(LedgerCancelled):
            OutputReference.capture(output)
        assert checked == 3
    finally:
        cancellation.reset_cancel_token(handle)


@pytest.mark.asyncio
async def test_inherited_claim_and_cancellation_context_work_in_to_thread(ledger_db):
    handle = chat_run_claim.set(('run-a', 'claim-a'))
    try:
        ledger = StepLedger('run-a', ledger_db.blobs)
        decision = await asyncio.to_thread(ledger.prepare, 'thread-slot', 'reader', {}, replay_policy='read_only')
        assert await asyncio.to_thread(ledger.claim, decision.step.key)
        result = await asyncio.to_thread(ledger.complete, decision.step.key, {'success': True})
        assert result.status == 'succeeded'
    finally:
        chat_run_claim.reset(handle)


@pytest.mark.parametrize('operation', ['prepare', 'claim', 'complete', 'checkpoint', 'repo_submit', 'repo_claim', 'repo_finish', 'repo_checkpoint'])
def test_closed_context_rejects_producer_writes_with_a_live_sql_claim(ledger_db, operation):
    running = _prepare_claim(ledger_db, 'mutating', 'running')
    submitted = ledger_db.ledger.prepare('submitted', 'reader', {}, replay_policy='read_only').step.key
    new_key = repo.StepKey('run-a', 'late-producer', 'f' * 64)
    checkpoint = ControllerCheckpoint('run-a')
    ref, checksum = ledger_db.ledger.blobs.save({'success': True})
    token = cancellation.CancelToken()  # No deadline: also covers disabled budgets.
    handle = cancellation.set_cancel_token(token)
    token.close()
    actions = {
        'prepare': lambda: ledger_db.ledger.prepare('late', 'reader', {}, replay_policy='read_only'),
        'claim': lambda: ledger_db.ledger.claim(submitted),
        'complete': lambda: ledger_db.ledger.complete(running, {'success': True}),
        'checkpoint': lambda: ledger_db.ledger.save_checkpoint(checkpoint),
        'repo_submit': lambda: repo.submit_step(new_key, 'reader', 'read_only', worker_id='claim-a'),
        'repo_claim': lambda: repo.claim_step(submitted, worker_id='claim-a'),
        'repo_finish': lambda: repo.finish_step(running, result_ref=ref, checksum=checksum, worker_id='claim-a'),
        'repo_checkpoint': lambda: repo.save_checkpoint_pointer('run-a', ref, checksum, worker_id='claim-a'),
    }
    try:
        with pytest.raises((LedgerCancelled, repo.ClosedRunContext)):
            actions[operation]()
        # Read-only diagnostics do not require a live producer context.
        assert repo.get_step(running).status == 'running'
        assert repo.get_step(submitted).status == 'submitted'
        assert len(repo.list_steps('run-a')) == 2
        assert repo.load_checkpoint_pointer('run-a') is None
        with ledger_db.connect() as conn:
            assert conn.execute("SELECT lease_expires_at > datetime('now') FROM chat_runs WHERE run_id='run-a'").fetchone()[0] == 1
    finally:
        cancellation.reset_cancel_token(handle)


def test_context_closed_during_sql_success_rolls_back_before_commit(ledger_db, monkeypatch):
    key = _prepare_claim(ledger_db, 'mutating')
    ref, checksum = ledger_db.ledger.blobs.save({'success': True})
    token = cancellation.CancelToken()
    handle = cancellation.set_cancel_token(token)
    original = ledger_db.connect
    class ClosingConnection:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, name): return getattr(self.conn, name)
        def execute(self, sql, args=()):
            cursor = self.conn.execute(sql, args)
            if "SET status='succeeded',result_ref" in sql:
                token.close()
            return cursor
    @contextmanager
    def closing_db():
        with original() as conn:
            yield ClosingConnection(conn)
    monkeypatch.setattr(repo, 'get_db', closing_db)
    try:
        with pytest.raises(repo.ClosedRunContext):
            repo.finish_step(key, result_ref=ref, checksum=checksum, worker_id='claim-a')
        assert repo.get_step(key).status == 'running'
        assert repo.get_step(key).result_ref is None
    finally:
        cancellation.reset_cancel_token(handle)


@pytest.mark.asyncio
async def test_delayed_host_thread_cannot_complete_after_scope_closes_without_a_deadline(ledger_db):
    key = _prepare_claim(ledger_db, 'mutating')
    token = cancellation.CancelToken()
    token_handle = cancellation.set_cancel_token(token)
    claim_handle = chat_run_claim.set(('run-a', 'claim-a'))
    ledger = StepLedger('run-a', ledger_db.blobs)
    started, release = Event(), Event()
    def late_complete():
        started.set()
        assert release.wait(timeout=3)
        return ledger.complete(key, {'success': True})
    task = asyncio.create_task(asyncio.to_thread(late_complete))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        token.close()
        release.set()
        with pytest.raises(LedgerCancelled):
            await task
        assert repo.get_step(key).status == 'running'
        assert repo.get_step(key).result_ref is None
    finally:
        release.set()
        chat_run_claim.reset(claim_handle)
        cancellation.reset_cancel_token(token_handle)


def test_cancelled_open_scope_allows_interrupt_cleanup_but_closed_scope_does_not(ledger_db):
    first = _prepare_claim(ledger_db, 'mutating', 'first')
    second = _prepare_claim(ledger_db, 'mutating', 'second')
    token = cancellation.CancelToken()
    handle = cancellation.set_cancel_token(token)
    try:
        token.set('user_cancelled')
        assert ledger_db.ledger.interrupt(first, error_code='user_cancelled').status == 'interrupted'
        token.close()
        with pytest.raises(repo.ClosedRunContext):
            ledger_db.ledger.interrupt(second)
        assert repo.get_step(second).status == 'running'
    finally:
        cancellation.reset_cancel_token(handle)


def test_legacy_checkpoint_table_migrates_to_controller_scope_without_losing_snapshot(ledger_db):
    checkpoint = ControllerCheckpoint('run-a', iteration=4, remaining_budget=RemainingBudget(6))
    ledger_db.ledger.save_checkpoint(checkpoint)
    with ledger_db.connect() as conn:
        conn.execute('DROP TABLE run_checkpoint_history')
        conn.execute('ALTER TABLE run_checkpoints RENAME TO run_checkpoints_scoped')
        conn.execute("""CREATE TABLE run_checkpoints(run_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
            schema_version INTEGER NOT NULL,checkpoint_ref TEXT NOT NULL,checksum TEXT NOT NULL,
            worker_id TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        conn.execute("""INSERT INTO run_checkpoints SELECT run_id,version,schema_version,
            checkpoint_ref,checksum,worker_id,updated_at FROM run_checkpoints_scoped""")
        conn.execute('DROP TABLE run_checkpoints_scoped')
        repo.ensure_run_step_schema(conn)
        repo.ensure_run_step_schema(conn)
        assert conn.execute('SELECT checkpoint_key FROM run_checkpoints').fetchone()[0] == 'controller'
    assert ledger_db.ledger.load_checkpoint() == checkpoint
    assert len(repo.list_checkpoint_history('run-a')) == 1


def test_checkpoint_scopes_have_independent_pointers_and_versions(ledger_db):
    chat = ControllerCheckpoint('run-a', iteration=2, controller_state={'namespace': 'chat-a'})
    task = ControllerCheckpoint('run-a', iteration=7, controller_state={'namespace': 'task-a'})
    assert ledger_db.ledger.save_checkpoint(chat, expected_version=0, checkpoint_key='chat:query') == 1
    assert ledger_db.ledger.save_checkpoint(task, expected_version=0, checkpoint_key='task:77:4:query') == 1
    assert ledger_db.ledger.save_checkpoint(chat, expected_version=1, checkpoint_key='chat:query') == 2
    assert ledger_db.ledger.load_checkpoint(checkpoint_key='chat:query') == chat
    assert ledger_db.ledger.load_checkpoint(checkpoint_key='task:77:4:query') == task
    assert ledger_db.ledger.load_checkpoint() is None
    assert {row['checkpoint_key']: row['version'] for row in repo.list_checkpoint_pointers('run-a')} == {'chat:query': 2, 'task:77:4:query': 1}


def test_snapshot_disk_retention_is_three_per_scope_and_keeps_result_and_untracked_blobs(ledger_db):
    step = _prepare_claim(ledger_db, 'mutating')
    result = ledger_db.ledger.complete(step, {'success': True})
    untracked_ref, _ = ledger_db.ledger.blobs.save({'untracked_result_observation': True})
    for iteration in range(12):
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=iteration), checkpoint_key='chat:query')
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=iteration + 20), checkpoint_key='task:77:4:query')
    histories = [*repo.list_checkpoint_history('run-a', 'chat:query'), *repo.list_checkpoint_history('run-a', 'task:77:4:query')]
    assert len(histories) == 6
    assert {path.stem for path in ledger_db.blobs.glob('*.json')} == {row['checkpoint_ref'] for row in histories} | {result.result_ref, untracked_ref}
    assert ledger_db.ledger.load_result(result) == {'success': True}


def test_pruning_protects_an_obsolete_snapshot_referenced_as_a_result(ledger_db):
    ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=1), checkpoint_key='chat:query')
    original = repo.load_checkpoint_pointer('run-a', 'chat:query')
    key = _prepare_claim(ledger_db)
    repo.finish_step(key, result_ref=original['checkpoint_ref'], checksum=original['checksum'], worker_id='claim-a')
    for iteration in range(2, 9):
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=iteration), checkpoint_key='chat:query')
    assert len(repo.list_checkpoint_history('run-a', 'chat:query')) == 3
    assert (ledger_db.blobs / f"{original['checkpoint_ref']}.json").is_file()
    assert len(list(ledger_db.blobs.glob('*.json'))) == 4


def test_import_copies_all_scopes_and_child_retention_does_not_delete_source_snapshots(ledger_db):
    key = _prepare_claim(ledger_db, 'mutating')
    result = ledger_db.ledger.complete(key, {'confirmed': True})
    for index in range(6):
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=index), checkpoint_key='chat:query')
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=index + 10), checkpoint_key='task:77:4:query')
    source_history = {row['checkpoint_ref'] for scope in ['chat:query', 'task:77:4:query'] for row in repo.list_checkpoint_history('run-a', scope)}
    with ledger_db.connect() as conn:
        conn.execute("UPDATE chat_runs SET status='failed',lease_expires_at=NULL WHERE run_id='run-a'")
        conn.execute("INSERT INTO chat_runs VALUES('run-child','session-a','owner-a','running','child-claim',datetime('now','+300 seconds'))")
    child = StepLedger('run-child', ledger_db.blobs, 'child-claim')
    child.import_from('run-a')
    assert child.load_checkpoint(checkpoint_key='chat:query').iteration == 5
    assert child.load_checkpoint(checkpoint_key='task:77:4:query').iteration == 15
    for index in range(8):
        child.save_checkpoint(ControllerCheckpoint('run-child', iteration=index + 20), checkpoint_key='chat:query')
    child.import_from('run-a')
    assert child.load_checkpoint(checkpoint_key='chat:query').iteration == 27
    assert child.load_checkpoint(checkpoint_key='task:77:4:query').iteration == 15
    assert source_history <= {path.stem for path in ledger_db.blobs.glob('*.json')}
    assert ledger_db.ledger.load_checkpoint(checkpoint_key='chat:query').iteration == 5
    assert child.prepare(key.tool_call_id, 'file_operations', {'operation': 'read'}, replay_policy='mutating').result == {'confirmed': True}
    assert (ledger_db.blobs / f'{result.result_ref}.json').is_file()


def test_failed_checkpoint_write_cleans_only_its_new_snapshot_and_keeps_prior_snapshot(ledger_db, monkeypatch):
    ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=1))
    prior_files = set(ledger_db.blobs.glob('*.json'))
    monkeypatch.setattr(repo, 'save_checkpoint_pointer', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('pointer fault')))
    for _ in range(4):
        with pytest.raises(RuntimeError, match='pointer fault'):
            ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=2))
    assert set(ledger_db.blobs.glob('*.json')) == prior_files
    assert ledger_db.ledger.load_checkpoint().iteration == 1


def test_prune_failure_does_not_turn_a_committed_checkpoint_into_failure(ledger_db, monkeypatch):
    ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=1))
    obsolete = repo.load_checkpoint_pointer('run-a')['checkpoint_ref']
    unlink = Path.unlink
    def fail_one(path, *args, **kwargs):
        if path.stem == obsolete and path.suffix == '.json':
            raise OSError('injected prune fault')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_one)
    for iteration in range(2, 5):
        assert ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=iteration)) == iteration
    assert ledger_db.ledger.load_checkpoint().iteration == 4


def test_checkpoint_reader_holds_pointer_and_blob_lock_against_pruning_writer(ledger_db, monkeypatch):
    first = ControllerCheckpoint('run-a', iteration=1)
    ledger_db.ledger.save_checkpoint(first)
    read_started, release_read, write_attempted = Event(), Event(), Event()
    load = ledger_db.ledger.blobs.load
    flock = fcntl.flock
    def delayed_load(ref, checksum):
        read_started.set()
        assert release_read.wait(timeout=5)
        return load(ref, checksum)
    def observe_lock(descriptor, operation):
        if operation & fcntl.LOCK_EX and operation & fcntl.LOCK_NB:
            write_attempted.set()
        return flock(descriptor, operation)
    monkeypatch.setattr(ledger_db.ledger.blobs, 'load', delayed_load)
    monkeypatch.setattr(fcntl, 'flock', observe_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(ledger_db.ledger.load_checkpoint)
        assert read_started.wait(timeout=3)
        writing = pool.submit(ledger_db.ledger.save_checkpoint, ControllerCheckpoint('run-a', iteration=2))
        try:
            assert write_attempted.wait(timeout=3)
            assert not writing.done()
        finally:
            release_read.set()
        assert reading.result(timeout=3) == first
        assert writing.result(timeout=3) == 2
    for iteration in [3, 4]:
        ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=iteration))
    assert ledger_db.ledger.load_checkpoint().iteration == 4


def test_cross_process_reader_lock_prevents_checkpoint_writer_from_committing(ledger_db, monkeypatch):
    ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=1))
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_shared_checkpoint_lock, args=(str(ledger_db.blobs / '.checkpoint.lock'), ready, release))
    attempted = Event()
    flock = fcntl.flock
    def observe_lock(descriptor, operation):
        if operation & fcntl.LOCK_EX and operation & fcntl.LOCK_NB:
            attempted.set()
        return flock(descriptor, operation)
    monkeypatch.setattr(fcntl, 'flock', observe_lock)
    process.start()
    try:
        assert ready.wait(timeout=8)
        with ThreadPoolExecutor(max_workers=1) as pool:
            writing = pool.submit(ledger_db.ledger.save_checkpoint, ControllerCheckpoint('run-a', iteration=2))
            try:
                assert attempted.wait(timeout=3)
                assert not writing.done()
            finally:
                release.set()
            assert writing.result(timeout=5) == 2
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
    assert process.exitcode == 0


@pytest.mark.asyncio
async def test_waiting_checkpoint_reader_cooperatively_cancels_while_store_is_locked(ledger_db, monkeypatch):
    ledger_db.ledger.save_checkpoint(ControllerCheckpoint('run-a', iteration=1))
    attempted = Event()
    token = cancellation.CancelToken()
    handle = cancellation.set_cancel_token(token)
    flock = fcntl.flock
    def observe_lock(descriptor, operation):
        if operation & fcntl.LOCK_SH and operation & fcntl.LOCK_NB:
            attempted.set()
        return flock(descriptor, operation)
    with (ledger_db.blobs / '.checkpoint.lock').open('a+b') as owner:
        flock(owner.fileno(), fcntl.LOCK_EX)
        monkeypatch.setattr(fcntl, 'flock', observe_lock)
        try:
            reading = asyncio.create_task(asyncio.to_thread(ledger_db.ledger.load_checkpoint))
            assert await asyncio.to_thread(attempted.wait, 3)
            token.set('user_cancelled')
            with pytest.raises(LedgerCancelled):
                await asyncio.wait_for(reading, timeout=2)
        finally:
            flock(owner.fileno(), fcntl.LOCK_UN)
            cancellation.reset_cancel_token(handle)
