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
from typing import Callable, Optional, TypeVar

_T = TypeVar("_T")


class CancelToken:
    """A thread-safe cancellation flag shared by the loop and worker threads."""

    __slots__ = ("_event", "_lock", "_reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block the calling thread until cancelled or *timeout* elapses."""
        return self._event.wait(timeout)

    def set(self, reason: str = "") -> bool:
        """Cancel; idempotent. Returns True only for the call that flipped it."""
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
    return _cancel_token.get()


def call_with_cancel_token(token: Optional[CancelToken], func: Callable[[], _T]) -> _T:
    """Run *func* with *token* re-bound (the ``ThreadPoolExecutor.submit`` gap)."""
    if token is None:
        return func()
    handle = _cancel_token.set(token)
    try:
        return func()
    finally:
        _cancel_token.reset(handle)
