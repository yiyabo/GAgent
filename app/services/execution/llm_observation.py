"""Optional per-run diagnostics. No provider payload or billing semantics change."""
from contextvars import ContextVar
from typing import Callable

observer: ContextVar[Callable | None] = ContextVar('llm_observer', default=None)


def emit(kind: str, **fields):
    callback = observer.get()
    if callback:
        callback({'kind': kind, **fields})
