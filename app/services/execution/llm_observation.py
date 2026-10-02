"""Optional per-run diagnostics. No provider payload or billing semantics change."""
from contextvars import ContextVar
from typing import Callable

observer: ContextVar[Callable | None] = ContextVar('llm_observer', default=None)


class ObserverRejected(RuntimeError):
    """An execution observer denied dispatch; transport retries must not resume it."""


def emit(kind: str, **fields):
    callback = observer.get()
    if callback:
        callback({'kind': kind, **fields})
