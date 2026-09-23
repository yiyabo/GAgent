"""Log context propagation: run/session/owner ids via contextvars.

Set context once at an execution boundary (chat run worker, plan job, HTTP
request) and every log record emitted downstream carries the fields — the
JSON formatter already passes ``extra`` record attributes through, so no
call-site changes are needed beyond the boundary.

Usage::

    from app.services.foundation.logging_context import bind_log_context, log_context_scope

    bind_log_context(run_id=run_id, session_id=session_id)
    ...
    with log_context_scope(run_id=run_id):
        ...  # nested scope restores previous values on exit
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Dict, Iterator, Optional

_RUN_ID: ContextVar[Optional[str]] = ContextVar("log_run_id", default=None)
_SESSION_ID: ContextVar[Optional[str]] = ContextVar("log_session_id", default=None)
_OWNER_ID: ContextVar[Optional[str]] = ContextVar("log_owner_id", default=None)

_FIELDS = ("run_id", "session_id", "owner_id")


def bind_log_context(
    *,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> None:
    """Set whichever fields are provided; unset fields keep their value."""
    if run_id is not None:
        _RUN_ID.set(run_id)
    if session_id is not None:
        _SESSION_ID.set(session_id)
    if owner_id is not None:
        _OWNER_ID.set(owner_id)


def clear_log_context() -> None:
    _RUN_ID.set(None)
    _SESSION_ID.set(None)
    _OWNER_ID.set(None)


@contextlib.contextmanager
def log_context_scope(
    *,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Iterator[None]:
    """Scoped bind: previous values are restored on exit (nesting-safe)."""
    tokens = []
    if run_id is not None:
        tokens.append((_RUN_ID, _RUN_ID.set(run_id)))
    if session_id is not None:
        tokens.append((_SESSION_ID, _SESSION_ID.set(session_id)))
    if owner_id is not None:
        tokens.append((_OWNER_ID, _OWNER_ID.set(owner_id)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_log_context() -> Dict[str, str]:
    out: Dict[str, str] = {}
    run_id = _RUN_ID.get()
    session_id = _SESSION_ID.get()
    owner_id = _OWNER_ID.get()
    if run_id:
        out["run_id"] = run_id
    if session_id:
        out["session_id"] = session_id
    if owner_id:
        out["owner_id"] = owner_id
    return out


class LogContextFilter(logging.Filter):
    """Inject bound context fields onto every record (JSON formatter picks them up)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_log_context().items():
            # An explicit extra= at the call site wins over the bound context.
            if not hasattr(record, key):
                setattr(record, key, value)
        return True
