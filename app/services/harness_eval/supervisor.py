"""Finite process supervisor; private oracles run only after a worker exits."""
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .accounting import CampaignLedger, EvaluationLimit, append_event, recover_result
from .config import EvalSuiteConfig
from .oracles import check, ORACLE_VERSION
from .process_cleanup import cleanup_children, close_isolated_runs, descendants, process_table, stop


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def suite_lock(root):
    with (root / '.supervisor.lock').open('a') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('evaluation suite already has a supervisor') from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def score_result(trial, item, *, interrupted=False):
    path = trial / 'result.json'
    row = json.loads(path.read_text()) if path.exists() else {
        **item, 'production_status': 'failed', 'error': 'worker_stopped_without_report', 'artifacts': []}
    row = recover_result(trial, row)
    if interrupted:
        row['supervisor_recovered'] = True
        if not path.exists():
            row['error'] = 'interrupted_trial_not_reexecuted'
    delivered = trial / 'oracle-input'
    if delivered.exists():
        shutil.rmtree(delivered)
    delivered.mkdir(exist_ok=True)
    copied = set()
    for artifact in row.get('artifacts', []):
        path = Path(artifact['path'])
        name = artifact['name']
        if Path(name).name != name:
            raise ValueError('invalid artifact name')
        if path.is_file() and trial in path.resolve().parents:
            contents = path.read_bytes()
            if not artifact.get('sha256') or hashlib.sha256(contents).hexdigest() != artifact['sha256']:
                row['error'] = 'delivered_artifact_hash_mismatch'
                continue
            if name in copied and (delivered / name).read_bytes() != contents:
                row['error'] = 'ambiguous_delivered_artifact'
                continue
            (delivered / name).write_bytes(contents)
            copied.add(name)
    row['oracle'] = check(item['case'], delivered)
    row['manual_review_required'] = row['oracle']['manual_review_required']
    row['manual_review_status'] = 'pending' if row['manual_review_required'] else 'not_required'
    row['oracle_passed'] = row['oracle']['passed']
    row['passed'] = bool(row['oracle_passed'] and row.get('answer_completion_passed') and not row.get('error'))
    return row


def run_suite(root: Path, cfg: EvalSuiteConfig, module_root=None):
    cfg.validate()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with suite_lock(root):
        return _run_suite(root, cfg, module_root)


def _run_suite(root, cfg, module_root):
    snapshot = root / 'config.json'
    payload, fingerprint = cfg.to_dict(), cfg.fingerprint()
    if snapshot.exists():
        if EvalSuiteConfig(**json.loads(snapshot.read_text())).to_dict() != payload:
            raise ValueError('suite configuration changed')
    else:
        atomic_json(snapshot, payload)
    journal = root / 'trials.jsonl'
    rows = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    done = {row['trial_id'] for row in rows}
    state_path = root / 'supervisor-state.json'
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    prior_elapsed = previous.get('active_seconds', sum(row.get('duration_seconds', 0) for row in rows))
    started, reason = time.monotonic(), None
    campaign = CampaignLedger(cfg) if cfg.campaign_root else None
    tokens = sum(row.get('known_total_tokens', row.get('total_tokens') or 0) for row in rows)
    launches = sum(row.get('external_launches', 0) for row in rows)

    def elapsed():
        return prior_elapsed + time.monotonic() - started

    def persist_state():
        atomic_json(state_path, {'active_seconds': elapsed()})

    def report():
        missing = sum(not row.get('usage_complete', row.get('total_tokens') is not None) for row in rows)
        result = {'schema_version': 3, 'config': payload, 'config_hash': fingerprint,
                  'oracle_version': ORACLE_VERSION, 'results': rows, 'total': len(cfg.schedule()),
                  'started': len(rows), 'passed': sum(row.get('passed', False) for row in rows),
                  'stop_reason': reason, 'token_limit_enforcement': 'post_response_stop_before_next_call_or_trial',
                  'known_total_tokens': tokens, 'total_tokens': None if missing else tokens,
                  'trials_with_missing_usage': missing, 'active_seconds': elapsed(), 'cost_usd': None}
        if campaign:
            result['campaign'] = campaign.state()
        atomic_json(root / 'report.json', result)
        return result

    for index, item in enumerate(cfg.schedule()):
        identity = f'{index:03d}-{item["case"]}-{item["entry"]}-{item["repetition"]}-{item.get("variant", "single")}'
        if identity in done:
            continue
        trial = root / identity
        effective = replace(cfg, **cfg.variants.get(item.get('variant'), {})).validate()
        # Recover before admission checks: existing effects and cost cannot disappear.
        if trial.exists():
            process_path = trial / 'process-state.json'
            process_state = json.loads(process_path.read_text()) if process_path.exists() else {}
            tracked = process_state.get('children', {})
            if process_state.get('pid') and process_state.get('start_identity'):
                tracked[str(process_state['pid'])] = process_state['start_identity']
            cleanup = cleanup_children(tracked)
            cleanup.update(close_isolated_runs(trial))
            row = score_result(trial, item, interrupted=True)
            duration = max(row.get('duration_seconds', 0), process_state.get('elapsed', 0))
        else:
            if elapsed() >= cfg.suite_wall_seconds:
                reason = 'suite_wall_limit'; break
            if tokens >= cfg.token_stop_threshold:
                reason = 'token_stop_before_next_trial'; break
            if any(not row.get('usage_complete', row.get('total_tokens') is not None) for row in rows):
                reason = 'missing_usage_before_next_trial'; break
            if item['entry'] == 'plan-external' and launches >= cfg.external_launch_limit:
                reason = 'external_launch_limit'; break
            if campaign:
                try:
                    campaign.start_trial(trial)
                except EvaluationLimit as exc:
                    reason = str(exc); break
            trial.mkdir()
            request = trial / 'request.json'
            atomic_json(request, {**item, 'config': effective.to_dict(), 'external_remaining': cfg.external_launch_limit - launches})
            append_event(trial / 'supervisor-events.jsonl', {'kind': 'trial_start_reserved', 'time': time.time()})
            args = [sys.executable, str(Path(__file__).resolve().parents[3] / 'scripts/run_harness_workflow_eval.py'), '--worker', str(request)]
            if module_root:
                args += ['--module-root', module_root]
            if effective.target_root:
                args += ['--target-root', effective.target_root]
            children, proc, cleanup = {}, None, {}
            trial_started = time.monotonic()
            try:
                with (trial / 'worker.log').open('w') as log:
                    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    start_identity = process_table().get(proc.pid, (None, None))[1]
                    while proc.poll() is None:
                        children.update(descendants(proc.pid))
                        duration = time.monotonic() - trial_started
                        atomic_json(trial / 'process-state.json', {'pid': proc.pid, 'start_identity': start_identity, 'children': children, 'elapsed': duration})
                        persist_state()
                        if campaign:
                            campaign.progress(trial, duration)
                        campaign_expired = campaign and campaign.state()['active_seconds'] >= cfg.campaign_wall_seconds
                        if elapsed() >= cfg.suite_wall_seconds or duration > cfg.trial_wall_seconds or campaign_expired:
                            cleanup = stop(proc, children, cfg.cleanup_grace_seconds)
                            break
                        time.sleep(.5)
            except BaseException as exc:
                append_event(trial / 'supervisor-events.jsonl', {'kind': 'supervisor_error', 'error': type(exc).__name__ + ': ' + str(exc)[:300]})
                if proc is not None:
                    cleanup = stop(proc, children, cfg.cleanup_grace_seconds)
                if not isinstance(exc, Exception):
                    raise
            finally:
                duration = time.monotonic() - trial_started
                cleanup.update(cleanup_children(children))
                cleanup.update(close_isolated_runs(trial))
                persist_state()
            row = score_result(trial, item)
            if proc is None or proc.returncode not in (0, None):
                row['infrastructure_failure'] = True
                row['error'] = row.get('error') or 'worker_exit_failure'
                row['passed'] = False
        row.update(item, trial_id=identity, config_hash=fingerprint)
        row['supervisor_cleanup'] = cleanup
        if cleanup.get('remaining_children') or cleanup.get('remaining_active_eval_runs'):
            row.update(infrastructure_failure=True, passed=False, error='evaluation_cleanup_incomplete')
        if campaign:
            campaign.recover_trial(trial, row, duration)
        tokens += row.get('known_total_tokens', row.get('total_tokens') or 0)
        launches += row.get('external_launches', 0)
        rows.append(row)
        append_event(journal, row)
        persist_state()
        if row.get('infrastructure_failure'):
            reason = 'infrastructure_failure'
        report()
        if reason:
            break
    persist_state()
    return report()
