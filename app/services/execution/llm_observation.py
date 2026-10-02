"""Optional per-run diagnostics. No provider payload or billing semantics change."""
from contextvars import ContextVar
from typing import Callable

observer: ContextVar[Callable | None] = ContextVar('llm_observer', default=None)


class ObserverRejected(RuntimeError):
    """An execution observer denied dispatch; transport retries must not resume it."""


def emit(kind: str, **fields):
    callback = observer.get()
    if callback:
        try:
            callback({'kind': kind, **fields})
        except ObserverRejected:
            raise
        except Exception as exc:
            # Losing the journal is not a provider network failure. Retrying
            # inference here could spend without durable observation.
            raise ObserverRejected('observation_failed:' + type(exc).__name__) from exc
