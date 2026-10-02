"""Durable evaluation evidence and conservative, shared campaign admission.

Token limits are checked after a response and before the next observable call.
CLI internal requests are not observable; launches are a separate counter.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4
from app.services.execution.llm_observation import ObserverRejected


class EvaluationLimit(ObserverRejected):
    pass


def append_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def read_events(path):
    path = Path(path)
    if not path.exists():
        return []
    result = []
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            # Only a torn last append is recoverable. Earlier corruption is fatal.
            if index != len(lines) - 1:
                raise
            result.append({'kind': 'evidence_incomplete'})
    return result


def valid_usage(value):
    if not isinstance(value, dict):
        return None
    total = value.get('total_tokens')
    if isinstance(total, bool) or not isinstance(total, (int, float)) or total < 0:
        return None
    return {key: value.get(key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}


def event_key(event):
    if event['kind'] == 'attempt':
        return f"provider:{event['logical_call_id']}:{event['attempt_no']}"
    if event['kind'] in {'external_launch', 'external_usage'}:
        return 'external:' + str(event['launch_id'])
    if event['kind'] == 'recovered_usage':
        return event['accounting_key']
    return None


def summarize(events):
    calls = {}
    evidence_missing = False
    for event in events:
        evidence_missing |= event.get('kind') == 'evidence_incomplete'
        key = event_key(event)
        if key is None:
            continue
        old = calls.setdefault(key, {'usage': None, 'source': 'missing'})
        usage = valid_usage(event.get('usage'))
        if usage is not None:
            old.update(usage=usage, source=event.get('usage_source', 'provider'))
    known = [item for item in calls.values() if item['usage'] is not None]
    missing = sum(item['usage'] is None or item['source'] == 'missing' for item in calls.values()) + int(evidence_missing)
    totals = {}
    for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [item['usage'].get(field) for item in known]
        totals['known_' + field] = sum(v for v in values if isinstance(v, (int, float)))
        totals[field] = totals['known_' + field] if not missing and all(v is not None for v in values) else None
    return {
        **totals, 'usage_complete': missing == 0,
        'missing_usage_count': missing,
        'usage_source': 'missing' if missing else 'estimated' if any(item['source'] != 'provider' for item in known) else 'provider',
        'provider_attempts': sum(key.startswith('provider:') for key in calls),
        'external_launches': sum(key.startswith('external:') for key in calls),
        'usage_breakdown': {source: sum(item['usage']['total_tokens'] for item in known if item['source'] == source) for source in ('provider', 'estimated')},
    }


class CampaignLedger:
    """A single SQLite ledger reserves before starting work, even across suites."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = Path(cfg.campaign_root) / 'campaign.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        policy = {name: getattr(cfg, name) for name in (
            'campaign_trial_limit', 'campaign_wall_seconds', 'campaign_token_stop_threshold',
            'campaign_provider_attempt_limit', 'campaign_external_launch_limit')}
        with self.transaction() as con:
            con.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS trials (trial TEXT PRIMARY KEY, state TEXT NOT NULL, elapsed REAL NOT NULL DEFAULT 0, usage_missing INTEGER NOT NULL DEFAULT 0)')
            con.execute('CREATE TABLE IF NOT EXISTS calls (trial TEXT NOT NULL, call_key TEXT NOT NULL, event TEXT NOT NULL, PRIMARY KEY(trial,call_key))')
            frozen = con.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()
            encoded = json.dumps(policy, sort_keys=True)
            if frozen and frozen[0] != encoded:
                raise ValueError('campaign limits changed; existing allowances cannot be reset')
            con.execute("INSERT OR IGNORE INTO metadata VALUES ('policy',?)", (encoded,))

    @contextmanager
    def transaction(self):
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute('PRAGMA synchronous=FULL')
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _state(self, con):
        events = []
        for trial, encoded in con.execute('SELECT trial,event FROM calls'):
            event = json.loads(encoded)
            key = event_key(event)
            prefix, _, suffix = key.partition(':')
            events.append({'kind': 'recovered_usage', 'accounting_key': f'{prefix}:{trial}:{suffix}',
                           'usage': event.get('usage'), 'usage_source': event.get('usage_source', 'provider')})
        state = summarize(events)
        row = con.execute('SELECT COUNT(*), COALESCE(SUM(elapsed),0), COALESCE(SUM(usage_missing),0) FROM trials').fetchone()
        return {**state, 'started_trials': row[0], 'active_seconds': row[1], 'finished_with_missing_usage': row[2]}

    def _check(self, con, kind):
        state = self._state(con)
        checks = [
            (state['finished_with_missing_usage'], 'campaign_missing_usage'),
            (state['known_total_tokens'] >= self.cfg.campaign_token_stop_threshold, 'campaign_token_stop'),
            (state['active_seconds'] >= self.cfg.campaign_wall_seconds, 'campaign_wall_limit'),
            (kind == 'trial' and state['started_trials'] >= self.cfg.campaign_trial_limit, 'campaign_trial_limit'),
            (kind in {'trial', 'attempt'} and state['provider_attempts'] >= self.cfg.campaign_provider_attempt_limit, 'campaign_provider_attempt_limit'),
            (kind == 'external_launch' and state['external_launches'] >= self.cfg.campaign_external_launch_limit, 'campaign_external_launch_limit'),
        ]
        for condition, reason in checks:
            if condition:
                raise EvaluationLimit(reason)

    def start_trial(self, trial):
        with self.transaction() as con:
            if con.execute('SELECT 1 FROM trials WHERE trial=?', (str(trial),)).fetchone():
                raise EvaluationLimit('campaign_trial_already_started')
            if con.execute("SELECT 1 FROM trials WHERE state='running'").fetchone():
                raise EvaluationLimit('campaign_unsettled_trial')
            self._check(con, 'trial')
            con.execute("INSERT INTO trials(trial,state) VALUES (?, 'running')", (str(trial),))

    def record(self, trial, event, *, recovered=False):
        key = event_key(event)
        if key is None:
            return
        with self.transaction() as con:
            if not recovered and not con.execute("SELECT 1 FROM trials WHERE trial=? AND state='running'", (str(trial),)).fetchone():
                raise EvaluationLimit('campaign_trial_not_reserved')
            previous = con.execute('SELECT event FROM calls WHERE trial=? AND call_key=?', (str(trial), key)).fetchone()
            if not recovered and previous is None and event['kind'] in {'attempt', 'external_launch'}:
                self._check(con, event['kind'])
            if previous and valid_usage(event.get('usage')) is None and valid_usage(json.loads(previous[0]).get('usage')):
                return
            con.execute('INSERT OR REPLACE INTO calls VALUES (?,?,?)', (str(trial), key, json.dumps(event)))

    def progress(self, trial, elapsed, *, final=False, usage_missing=False):
        with self.transaction() as con:
            con.execute('UPDATE trials SET elapsed=MAX(elapsed,?), state=?, usage_missing=? WHERE trial=?',
                        (elapsed, 'finished' if final else 'running', int(usage_missing), str(trial)))

    def state(self):
        with self.transaction() as con:
            return self._state(con)

    def recover_trial(self, trial, result, elapsed):
        with self.transaction() as con:
            con.execute("INSERT OR IGNORE INTO trials(trial,state) VALUES (?, 'running')", (str(trial),))
        for event in result.get('call_events', []):
            self.record(trial, event, recovered=True)
        if not result.get('call_events') and result.get('known_total_tokens'):
            self.record(trial, {'kind': 'recovered_usage', 'accounting_key': 'historical:total',
                               'usage': {'total_tokens': result['known_total_tokens']},
                               'usage_source': result.get('usage_source', 'missing')}, recovered=True)
        self.progress(trial, elapsed, final=True, usage_missing=not result.get('usage_complete', False))


class TrialAccounting:
    def __init__(self, root, cfg, external_remaining=None):
        self.root, self.cfg = Path(root), cfg
        self.path = self.root / 'calls.jsonl'
        self.events = read_events(self.path)
        self.lock = threading.RLock()
        self.campaign = CampaignLedger(cfg) if cfg.campaign_root else None
        self.external_limit = min(1, cfg.external_launch_limit if external_remaining is None else external_remaining)
        self.last_launch = next((e['launch_id'] for e in reversed(self.events) if e['kind'] == 'external_launch'), None)
        self.explicit_missing = False

    def _append(self, event):
        event = {'event_id': uuid4().hex, 'recorded_at': time.time(), **event}
        append_event(self.path, event)
        self.events.append(event)

    def observe(self, incoming):
        with self.lock:
            event = dict(incoming)
            state = summarize(self.events)
            kind = event['kind']
            existing = {event_key(e) for e in self.events}
            new_call = kind == 'external_launch' or (kind == 'attempt' and event_key(event) not in existing)
            if new_call:
                reason = None
                if self.explicit_missing:
                    reason = 'missing_usage_before_next_call'
                elif self.cfg.per_trial_token_stop_threshold is not None and state['known_total_tokens'] >= self.cfg.per_trial_token_stop_threshold:
                    reason = 'trial_token_stop_before_next_call'
                elif kind == 'attempt' and state['provider_attempts'] >= self.cfg.provider_attempt_limit:
                    reason = 'provider_attempt_limit'
                elif kind == 'external_launch' and state['external_launches'] >= self.external_limit:
                    reason = 'external_launch_limit'
                if reason:
                    self._append({'kind': 'call_denied', 'reason': reason})
                    raise EvaluationLimit(reason)
            if kind == 'external_launch':
                event['launch_id'] = uuid4().hex
                self.last_launch = event['launch_id']
            if kind == 'external_usage':
                event['launch_id'] = event.get('launch_id') or self.last_launch or 'unattributed'
                if valid_usage(event.get('usage')) is None:
                    self.explicit_missing = True
            if kind == 'native_result':
                arguments = event.pop('arguments', [])
                raw = json.dumps(arguments, ensure_ascii=False)
                filename = 'arguments-' + str(len(self.events)) + '.json'
                (self.root / filename).write_text(raw)
                event.update(arguments_ref=filename, arguments_sha256=hashlib.sha256(raw.encode()).hexdigest(), arguments_chars=len(raw))
                if valid_usage(event.get('usage')) is None:
                    self.explicit_missing = True
            if self.campaign:
                try:
                    self.campaign.record(self.root, event)
                except EvaluationLimit as exc:
                    self._append({'kind': 'call_denied', 'reason': str(exc)})
                    raise
            self._append(event)

    def reconcile_rows(self, rows):
        """Recover missing observer receipts without adding CLI parent totals twice."""
        seen = {event_key(e): e for e in self.events if event_key(e)}
        recovered_rows = {e.get('database_row_id') for e in self.events if e.get('database_row_id') is not None}
        external = [e for e in self.events if e['kind'] == 'external_usage' and valid_usage(e.get('usage'))]
        used = set()
        for row in rows:
            if row.get('id') in recovered_rows:
                continue
            if row.get('logical_call_id'):
                key = f"provider:{row['logical_call_id']}:{row.get('attempt_no') or 1}"
            elif row.get('provider', '').endswith('_cli'):
                match = next((i for i, event in enumerate(external) if i not in used and all(event['usage'].get(field) == row.get(field) for field in ('provider', 'model', 'total_tokens'))), None)
                if match is not None:
                    used.add(match)
                    continue
                # A CLI row without its observer receipt lacks source attribution.
                pending = [key for key, event in seen.items() if key.startswith('external:') and valid_usage(event.get('usage')) is None]
                key = pending[0] if len(pending) == 1 else 'external:db:' + str(row['id'])
            else:
                key = 'db:' + str(row['id'])
            if key in seen and valid_usage(seen[key].get('usage')):
                continue
            source = 'missing' if row.get('provider', '').endswith('_cli') else 'provider'
            self.observe({'kind': 'recovered_usage', 'accounting_key': key, 'database_row_id': row.get('id'), 'usage': row, 'usage_source': source})

    def summary(self):
        return summarize(self.events)


def recover_result(root, row=None):
    """Return every known token while keeping incomplete usage explicitly null."""
    root = Path(root)
    row = dict(row or {})
    database = root / 'db_root/main/plan_registry.db'
    if database.exists():
        # A CLI can persist its receipt immediately before the observer or worker
        # is killed. Read only this trial's owner-scoped rows to preserve that cost.
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as con:
            con.row_factory = sqlite3.Row
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'llm_usage_log', 'chat_sessions'} <= tables:
                rows = [dict(r) for r in con.execute("SELECT * FROM llm_usage_log WHERE session_id IN (SELECT id FROM chat_sessions WHERE owner_id='harness-eval') ORDER BY id")]
                from .config import EvalSuiteConfig
                accounting = TrialAccounting(root, EvalSuiteConfig())
                accounting.reconcile_rows(rows)
    events = read_events(root / 'calls.jsonl')
    if events:
        row.update(summarize(events), call_events=events)
    elif 'total_tokens' not in row or row.get('usage_source') == 'missing':
        row.update(total_tokens=None, prompt_tokens=None, completion_tokens=None,
                   known_total_tokens=row.get('known_total_tokens', 0), usage_complete=False,
                   usage_source='missing', missing_usage_count=1)
    else:
        row.setdefault('known_total_tokens', row['total_tokens'] or 0)
        row.setdefault('usage_complete', row['total_tokens'] is not None)
    return row
