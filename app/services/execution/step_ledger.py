"""Resumable step decisions and immutable payload storage.

Confirmed successful calls replay stored observations. Only calls explicitly
classified read-only/idempotent may restart after an ambiguous interruption.
Mutating calls require reconciliation; this is not an exactly-once executor.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Optional
from uuid import uuid4

from app.config.database_config import get_database_config
from app.repository import run_steps as repository
from app.repository.run_steps import ReplayPolicy, RunStep, StepKey, StepStateConflict
from app.services.chat_run_state import chat_run_claim
from app.services.cancellation import current_cancel_token


class ResultUnavailable(RuntimeError):
    """A referenced observation or output cannot be verified."""


class LedgerCancelled(InterruptedError):
    """Persistence/hash work cooperatively stopped by the inherited run token."""


MAX_BLOB_BYTES = 32 * 1024 * 1024
logger = logging.getLogger(__name__)


def _check_cancel() -> None:
    token = current_cancel_token()
    if token is not None:
        if token.closed:
            raise LedgerCancelled('ledger run context closed')
        if token.is_set():
            raise LedgerCancelled('ledger work cancelled')


_CREDENTIAL_KEYS = {'api_key', 'password', 'authorization', 'access_token', 'refresh_token', 'client_secret', 'secret', 'cookie', 'set_cookie'}


def _redact(value: Any) -> Any:
    _check_cancel()
    if isinstance(value, dict):
        return {key: '[REDACTED]' if str(key).lower().replace('-', '_') in _CREDENTIAL_KEYS else _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        # Native tool messages often carry a JSON object encoded as content.
        # Preserve ordinary prose/unchanged JSON, but redact credential fields
        # inside that representation too.
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        redacted = _redact(decoded)
        if redacted != decoded:
            return json.dumps(redacted, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return value


def _canonical(value: Any) -> bytes:
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        chunks = []
        size = 0
        for fragment in encoder.iterencode(value):
            _check_cancel()
            chunk = fragment.encode('utf-8')
            size += len(chunk)
            if size > MAX_BLOB_BYTES:
                raise ValueError('checkpoint/result exceeds the bounded blob size')
            chunks.append(chunk)
        return b''.join(chunks)
    except (TypeError, ValueError) as exc:
        raise ValueError('checkpoint/result must contain only finite JSON values') from exc


def params_fingerprint(tool_name: str, params: Mapping[str, Any]) -> str:
    """Fingerprint original JSON parameters; never persist the parameter body."""
    return hashlib.sha256(_canonical({'tool_name': tool_name, 'params': dict(params)})).hexdigest()


class JsonBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def save(self, value: Any) -> tuple[str, str]:
        _check_cancel()
        payload = _canonical(_redact(value))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        ref = uuid4().hex
        temporary = self.root / f'.{ref}.tmp'
        destination = self.root / f'{ref}.json'
        try:
            with temporary.open('xb') as handle:
                os.chmod(temporary, 0o600)
                for offset in range(0, len(payload), 65536):
                    _check_cancel()
                    handle.write(payload[offset:offset + 65536])
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
        return ref, hashlib.sha256(payload).hexdigest()

    def load(self, ref: str, checksum: str) -> Any:
        if not re.fullmatch(r'[0-9a-f]{32}', ref or '') or not re.fullmatch(r'[0-9a-f]{64}', checksum or ''):
            raise ResultUnavailable('invalid opaque reference')
        try:
            chunks = []
            size = 0
            with (self.root / f'{ref}.json').open('rb') as handle:
                for chunk in iter(lambda: handle.read(65536), b''):
                    _check_cancel()
                    size += len(chunk)
                    if size > MAX_BLOB_BYTES:
                        raise ResultUnavailable('stored observation exceeds bounded size')
                    chunks.append(chunk)
            payload = b''.join(chunks)
            if hashlib.sha256(payload).hexdigest() != checksum:
                raise ResultUnavailable('stored observation checksum changed')
            return json.loads(payload)
        except LedgerCancelled:
            raise
        except (OSError, ValueError) as exc:
            raise ResultUnavailable('stored observation unavailable') from exc


@dataclass(frozen=True)
class OutputReference:
    path: str
    checksum: str

    @classmethod
    def capture(cls, path: str | Path) -> 'OutputReference':
        _check_cancel()
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise ResultUnavailable('referenced output file is missing')
        digest = hashlib.sha256()
        with resolved.open('rb') as handle:
            for chunk in iter(lambda: handle.read(65536), b''):
                _check_cancel()
                digest.update(chunk)
        return cls(str(resolved), digest.hexdigest())

    def validate(self) -> None:
        current = self.capture(self.path)
        if current.checksum != self.checksum:
            raise ResultUnavailable('referenced output file changed')


@dataclass
class RemainingBudget:
    iterations: int = 0
    tokens: Optional[int] = None
    seconds: Optional[float] = None

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f'{name} budget must be nonnegative')
        if not isinstance(self.iterations, int) or (self.tokens is not None and not isinstance(self.tokens, int)):
            raise ValueError('iteration/token budgets must be integers')
        _canonical(asdict(self))


@dataclass
class ControllerCheckpoint:
    run_id: str
    phase: str = 'ready'
    iteration: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_result_refs: list[dict[str, Any]] = field(default_factory=list)
    control_counters: dict[str, int | bool] = field(default_factory=dict)
    remaining_budget: RemainingBudget = field(default_factory=RemainingBudget)
    controller_state: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not self.run_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.phase):
            raise ValueError('unsupported controller checkpoint identity/schema')
        if isinstance(self.iteration, bool) or not isinstance(self.iteration, int) or self.iteration < 0:
            raise ValueError('checkpoint iteration must be a nonnegative integer')
        if isinstance(self.remaining_budget, dict):
            self.remaining_budget = RemainingBudget(**self.remaining_budget)
        if not isinstance(self.remaining_budget, RemainingBudget):
            raise ValueError('checkpoint requires typed remaining budget')
        if not isinstance(self.messages, list) or not isinstance(self.tool_result_refs, list) or not isinstance(self.control_counters, dict) or not isinstance(self.controller_state, dict):
            raise ValueError('checkpoint collections have invalid types')
        if not all(isinstance(item, dict) for item in self.messages + self.tool_result_refs):
            raise ValueError('messages and result refs must be JSON objects')
        if not all(isinstance(value, (int, bool)) and value >= 0 for value in self.control_counters.values()):
            raise ValueError('control counters must be nonnegative integers/booleans')
        _canonical(asdict(self))


@dataclass(frozen=True)
class StepDecision:
    action: Literal['execute', 'replay', 'busy', 'reconcile']
    step: RunStep
    result: Optional[dict[str, Any]] = None
    reason: Optional[str] = None


class StepLedger:
    def __init__(self, run_id: str, blob_root: Optional[Path] = None, worker_id: Optional[str] = None) -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self.blobs = JsonBlobStore(blob_root or Path(get_database_config().db_root) / 'run_blobs')

    def _claim_id(self) -> Optional[str]:
        claim = chat_run_claim.get()
        return self.worker_id or (claim[1] if claim and claim[0] == self.run_id else None)

    def _check_key(self, key: StepKey) -> None:
        if key.run_id != self.run_id:
            raise ValueError('step belongs to a different run')

    @contextmanager
    def _checkpoint_lock(self, *, write: bool):
        """Cross-process lock spans pointer+blob access, including pruning.

        Poll rather than block in flock so the owning deadline/cancel token can
        stop a host thread while another process holds the checkpoint store.
        """
        import fcntl

        self.blobs.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.blobs.root / '.checkpoint.lock').open('a+b') as handle:
            operation = fcntl.LOCK_EX if write else fcntl.LOCK_SH
            locked = False
            try:
                while not locked:
                    _check_cancel()
                    try:
                        fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                        locked = True
                    except BlockingIOError:
                        time.sleep(0.01)
                yield
            finally:
                if locked:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _prune_snapshot_refs(self, references: Iterable[str]) -> None:
        """Delete known obsolete/rejected snapshots, never scan for orphan blobs.

        Called under the exclusive store lock. A prune failure cannot turn an
        already committed checkpoint into a reported execution failure.
        """
        removed = False
        for ref in set(references):
            try:
                if not re.fullmatch(r'[0-9a-f]{32}', ref) or repository.is_blob_referenced(ref):
                    continue
                path = self.blobs.root / f'{ref}.json'
                if path.exists():
                    path.unlink()
                    removed = True
            except Exception:
                logger.warning('Could not prune a superseded checkpoint snapshot', exc_info=False)
        if removed:
            try:
                descriptor = os.open(self.blobs.root, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                logger.warning('Could not sync checkpoint snapshot pruning', exc_info=False)

    def load_result(self, step: RunStep) -> dict[str, Any]:
        payload = self.blobs.load(step.result_ref, step.result_checksum)
        if not isinstance(payload, dict):
            raise ResultUnavailable('tool observation must be an object')
        for ref in step.output_refs:
            try:
                OutputReference(**ref).validate()
            except LedgerCancelled:
                raise
            except (TypeError, OSError, ValueError) as exc:
                raise ResultUnavailable('referenced output unavailable') from exc
        return payload

    def prepare(self, tool_call_id: str, tool_name: str, params: Mapping[str, Any], *, replay_policy: ReplayPolicy = 'mutating', attempt: int = 1, resume: bool = True) -> StepDecision:
        fingerprint = params_fingerprint(tool_name, params)
        StepKey(self.run_id, tool_call_id, fingerprint, attempt)
        repository.assert_run_owned(self.run_id, worker_id=self.worker_id)
        previous = repository.latest_step(self.run_id, tool_call_id, fingerprint)
        if previous and (previous.tool_name != tool_name or previous.replay_policy != replay_policy):
            raise StepStateConflict('replay policy/tool differs from recorded call')
        if previous:
            if previous.status == 'succeeded':
                try:
                    result = self.load_result(previous)
                    repository.assert_run_owned(self.run_id, worker_id=self.worker_id)
                    return StepDecision('replay', previous, result)
                except ResultUnavailable:
                    if replay_policy == 'mutating':
                        return StepDecision('reconcile', previous, reason='successful_mutation_result_unavailable')
            elif previous.status == 'submitted':
                return StepDecision('execute', previous)
            elif previous.status == 'running':
                if previous.worker_id == self._claim_id() or not resume:
                    return StepDecision('busy', previous, reason='step_already_running')
                previous = repository.end_step(previous.key, 'interrupted', error_code='claim_interrupted', worker_id=self.worker_id, expected_step_worker=previous.worker_id)
            if previous.status != 'succeeded' and replay_policy == 'mutating':
                return StepDecision('reconcile', previous, reason='ambiguous_mutating_attempt')
            if not resume:
                return StepDecision('busy', previous, reason='retry_requires_resume')
            attempt = max(attempt, previous.key.attempt + 1)
        key = StepKey(self.run_id, tool_call_id, fingerprint, attempt)
        step = repository.submit_step(key, tool_name, replay_policy, worker_id=self.worker_id)
        return StepDecision('execute', step)

    def claim(self, key: StepKey) -> bool:
        self._check_key(key)
        _check_cancel()
        return repository.claim_step(key, worker_id=self.worker_id)

    def complete(self, key: StepKey, result: Mapping[str, Any], *, output_refs: Iterable[OutputReference | str | Path] = ()) -> RunStep:
        self._check_key(key)
        _check_cancel()
        repository.assert_run_owned(self.run_id, worker_id=self.worker_id)
        references = tuple(asdict(ref if isinstance(ref, OutputReference) else OutputReference.capture(ref)) for ref in output_refs)
        for ref in references:
            OutputReference(**ref).validate()
        ref, checksum = self.blobs.save(dict(result))
        return repository.finish_step(key, result_ref=ref, checksum=checksum, output_refs=references, worker_id=self.worker_id)

    def fail(self, key: StepKey, error_code: str = 'tool_failed') -> RunStep:
        self._check_key(key)
        return repository.end_step(key, 'failed', error_code=error_code, worker_id=self.worker_id)

    def interrupt(self, key: StepKey, error_code: str = 'interrupted') -> RunStep:
        self._check_key(key)
        return repository.end_step(key, 'interrupted', error_code=error_code, worker_id=self.worker_id)

    def reconcile(self, key: StepKey, result: Mapping[str, Any], *, output_refs: Iterable[OutputReference | str | Path] = ()) -> RunStep:
        """Caller has verified the actual external outcome; no tool is executed here."""
        self._check_key(key)
        _check_cancel()
        repository.assert_run_owned(self.run_id, worker_id=self.worker_id)
        previous = repository.get_step(key)
        if previous is not None and previous.status == 'succeeded':
            try:
                self.load_result(previous)
            except ResultUnavailable:
                pass
            else:
                raise StepStateConflict('a valid confirmed observation cannot be replaced')
        references = tuple(asdict(ref if isinstance(ref, OutputReference) else OutputReference.capture(ref)) for ref in output_refs)
        for ref in references:
            OutputReference(**ref).validate()
        ref, checksum = self.blobs.save(dict(result))
        return repository.reconcile_step(key, result_ref=ref, checksum=checksum, output_refs=references, worker_id=self.worker_id)

    def save_checkpoint(self, checkpoint: ControllerCheckpoint, expected_version: Optional[int] = None, *, checkpoint_key: str = 'controller') -> int:
        _check_cancel()
        if checkpoint.run_id != self.run_id:
            raise ValueError('checkpoint belongs to a different run')
        # Revalidate mutable lists/counters immediately before persistence.
        checkpoint = ControllerCheckpoint(**asdict(checkpoint))
        repository._checkpoint_key(checkpoint_key)
        with self._checkpoint_lock(write=True):
            repository.assert_run_owned(self.run_id, worker_id=self.worker_id)
            prior_refs = {row['checkpoint_ref'] for row in repository.list_checkpoint_history(self.run_id, checkpoint_key)}
            ref, checksum = self.blobs.save(asdict(checkpoint))
            try:
                version = repository.save_checkpoint_pointer(self.run_id, ref, checksum, schema_version=checkpoint.schema_version, expected_version=expected_version, worker_id=self.worker_id, checkpoint_key=checkpoint_key)
            except BaseException:
                # This is this producer's new snapshot, not a general orphan
                # sweep. Protect it if a commit actually became visible.
                self._prune_snapshot_refs([ref])
                raise
            try:
                retained_refs = {row['checkpoint_ref'] for row in repository.list_checkpoint_history(self.run_id, checkpoint_key)}
                self._prune_snapshot_refs(prior_refs - retained_refs)
            except Exception:
                logger.warning('Could not inspect superseded checkpoint snapshots for pruning', exc_info=False)
            return version

    def load_checkpoint(self, *, checkpoint_key: str = 'controller') -> Optional[ControllerCheckpoint]:
        repository._checkpoint_key(checkpoint_key)
        with self._checkpoint_lock(write=False):
            pointer = repository.load_checkpoint_pointer(self.run_id, checkpoint_key)
            if not pointer:
                return None
            try:
                checkpoint = ControllerCheckpoint(**self.blobs.load(pointer['checkpoint_ref'], pointer['checksum']))
            except (TypeError, ValueError) as exc:
                raise ResultUnavailable('controller checkpoint schema is invalid') from exc
            if checkpoint.run_id != self.run_id or checkpoint.schema_version != pointer['schema_version']:
                raise ResultUnavailable('controller checkpoint identity/schema changed')
            return checkpoint

    def import_from(self, source_run_id: str) -> int:
        """Import terminal same-owner/session progress into a newly claimed run."""
        _check_cancel()
        count = repository.import_steps(source_run_id, self.run_id, worker_id=self.worker_id)
        source = StepLedger(source_run_id, self.blobs.root)
        for pointer in repository.list_checkpoint_pointers(source_run_id):
            checkpoint_key = pointer['checkpoint_key']
            if repository.load_checkpoint_pointer(self.run_id, checkpoint_key) is not None:
                continue
            checkpoint = source.load_checkpoint(checkpoint_key=checkpoint_key)
            if checkpoint is not None:
                try:
                    self.save_checkpoint(replace(checkpoint, run_id=self.run_id), expected_version=0, checkpoint_key=checkpoint_key)
                except StepStateConflict:
                    if repository.load_checkpoint_pointer(self.run_id, checkpoint_key) is None:
                        raise
        return count
