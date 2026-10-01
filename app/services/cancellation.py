"""Cancellation tokens that cross the loop/thread boundary of a chat run.

A chat run's cancel signal always originates inside the event loop (the
``/cancel`` route, the durable signal pump, or the realtime-bus control
consumer) but the work it has to interrupt runs somewhere else: ``delegate_task``
and ``code_executor`` hand a run over to a synchronous delegator through
``asyncio.to_thread``, and the CLI subprocess is supervised from there.

``asyncio.Event`` cannot serve as the shared flag for that hop: it is not
thread-safe and only its owning loop may set it.  ``threading.Event`` is
readable and waitable from any thread, so a single primitive serves both sides —
the loop thread sets it, the worker thread polls or waits on it.  No
``loop.call_soon_threadsafe`` bridge is needed because nothing waits for the
token on the loop side: the consumers are the synchronous CLI supervisor loops
and the tool-execution watchdog.

The token travels by ``contextvars``, which ``asyncio.to_thread`` copies into
the worker thread (the same mechanism ``app/llm.py`` uses for usage context).
``concurrent.futures.ThreadPoolExecutor.submit`` does NOT copy the context, so
``UnifiedToolExecutor.execute_sync`` re-binds the ambient token inside its
submitted callable — see ``call_with_cancel_token``.
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import Callable, Optional, TypeVar

_T = TypeVar("_T")


class CancelToken:
    """A thread-safe cancellation flag shared by the loop and worker threads."""

    __slots__ = ("_event", "_lock", "_reason", "_deadline", "_deadline_reason", "_closed")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""
        self._deadline: Optional[float] = None
        self._deadline_reason = "run_deadline_exceeded"
        self._closed = False

    @property
    def cancelled(self) -> bool:
        # Thread/CLI supervisors enforce the deadline even if their parent
        # event loop is temporarily blocked by a synchronous bridge.
        with self._lock:
            if not self._event.is_set() and self._deadline is not None and time.monotonic() >= self._deadline:
                self._reason = self._deadline_reason
                self._event.set()
        return self._event.is_set()

    @property
    def reason(self) -> str:
        _ = self.cancelled
        with self._lock:
            return self._reason

    def is_set(self) -> bool:
        return self.cancelled

    def set_deadline(self, deadline: float, reason: str = "run_deadline_exceeded") -> None:
        """Nested calls may shorten the inherited absolute deadline, never renew it."""
        with self._lock:
            if self._deadline is None or deadline < self._deadline:
                self._deadline = deadline
                self._deadline_reason = reason

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        """Fence inherited producer contexts after their owning run exits."""
        _ = self.cancelled
        with self._lock:
            self._closed = True
            if not self._event.is_set():
                self._reason = "run_scope_closed"
                self._event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block the calling thread until cancelled or *timeout* elapses."""
        with self._lock:
            remaining = max(0.0, self._deadline - time.monotonic()) if self._deadline is not None else None
        if remaining is not None:
            timeout = remaining if timeout is None else min(timeout, remaining)
        self._event.wait(timeout)
        return self.cancelled

    def set(self, reason: str = "") -> bool:
        """Cancel; idempotent. Returns True only for the call that flipped it."""
        if self.cancelled:
            return False
        with self._lock:
            if self._event.is_set():
                return False
            if reason:
                self._reason = str(reason)
            self._event.set()
            return True

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"CancelToken(cancelled={self.cancelled})"


_cancel_token: contextvars.ContextVar[Optional[CancelToken]] = contextvars.ContextVar(
    "delegation_cancel_token", default=None
)


def set_cancel_token(token: Optional[CancelToken]) -> contextvars.Token:
    """Bind *token* for this context; pass the handle to ``reset_cancel_token``."""
    return _cancel_token.set(token)


def reset_cancel_token(handle: contextvars.Token) -> None:
    _cancel_token.reset(handle)


def current_cancel_token() -> Optional[CancelToken]:
    """The token of the run whose context this execution inherits, if any."""
    token = _cancel_token.get()
    if token is None:
        # A caller binding a budget directly still gets its shared token in
        # kernel/RPC/CLI thread consumers. Lazy import avoids a module cycle.
        from app.services.run_budget import current_run_budget

        budget = current_run_budget()
        token = budget.cancel_token if budget is not None else None
    return token


def call_with_cancel_token(token: Optional[CancelToken], func: Callable[[], _T]) -> _T:
    """Run *func* with *token* re-bound (the ``ThreadPoolExecutor.submit`` gap)."""
    if token is None:
        return func()
    handle = _cancel_token.set(token)
    try:
        return func()
    finally:
        _cancel_token.reset(handle)
