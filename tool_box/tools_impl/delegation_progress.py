"""Progress reporting for a delegated CLI run (the code_executor sub-agent lane).

``code_executor``'s CLI lanes (qwen / claude) hand the work to an external coding
agent that can run for tens of minutes.  Until now only the *local* lane reported
through ``ToolContext.on_progress`` (``code_executor_backend._execute_task_locally``),
so a delegated run left the parent's activity stream blank from start to finish.

Two facts about the CLI lanes shape this module:

* **The callback does not always live on this thread.**  ``delegate_task`` hands
  the delegation over with ``asyncio.to_thread``, and the CLI is supervised from
  the worker thread's own loop (``UnifiedToolExecutor.execute_sync`` →
  ``asyncio.run``), while ``on_progress`` belongs to the loop that started the run.
  That is the same loop/thread split the cancel token has to cross
  (``app/services/cancellation.py``), so the payload is delivered with
  ``asyncio.run_coroutine_threadsafe`` onto the owning loop; the same-loop case is
  awaited in place.  The owning loop travels with the callback as
  ``ToolContext.on_progress_loop``.
* **Reporting must never endanger the delegation.**  A callback that raises, a
  closed loop, or an owning loop that never drains the future is logged and
  swallowed; the delegation's own result is untouched.

Payload contract (unchanged, the shape the local lane and ``bio_tools`` already
send): ``{"stage": ..., "message": ..., **extra}``.  The chat bridge renders
``message`` as the progress label and ``detail`` as its subtitle
(``app/routers/chat/agent.py:on_tool_progress``); every other key travels for
non-SSE consumers.  ``stage`` reuses the existing vocabulary
(``started`` / ``running`` / ``completed`` / ``failed``) plus ``cancelled``, the
terminal state S3b introduced for a user-stopped delegation.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

ENV_PROGRESS_ENABLED = "DELEGATION_PROGRESS_ENABLED"
ENV_HEARTBEAT_SECONDS = "DELEGATION_PROGRESS_HEARTBEAT_SECONDS"
ENV_DELIVERY_TIMEOUT_SECONDS = "DELEGATION_PROGRESS_DELIVERY_TIMEOUT_SECONDS"

#: Interval between "still running" reports.  The heartbeat rides on the cancel
#: watcher's 0.25s poll, so this knob is what keeps a long delegation from either
#: going silent or flooding the stream.  0 disables heartbeats entirely.
_DELEGATION_HEARTBEAT_SECONDS = 15.0
_HEARTBEAT_MAX_SECONDS = 600.0

#: Bound on how long a reporting thread waits for the owning loop to run the
#: callback.  Delivery is awaited (not fire-and-forget) so a terminal report
#: cannot land after the tool result it belongs to; the bound keeps a busy or
#: blocked owning loop from ever stalling the delegation.
_DELEGATION_DELIVERY_TIMEOUT_SECONDS = 2.0
_DELIVERY_TIMEOUT_MAX_SECONDS = 30.0


def delegation_progress_enabled() -> bool:
    """Whether the CLI lane reports progress at all (default on)."""
    raw = str(os.getenv(ENV_PROGRESS_ENABLED, "")).strip().lower()
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off"}


def _resolve_heartbeat_seconds() -> float:
    raw = str(os.getenv(ENV_HEARTBEAT_SECONDS, "")).strip()
    if not raw:
        return _DELEGATION_HEARTBEAT_SECONDS
    try:
        return max(0.0, min(_HEARTBEAT_MAX_SECONDS, float(raw)))
    except ValueError:
        return _DELEGATION_HEARTBEAT_SECONDS


def _resolve_delivery_timeout_seconds() -> float:
    raw = str(os.getenv(ENV_DELIVERY_TIMEOUT_SECONDS, "")).strip()
    if not raw:
        return _DELEGATION_DELIVERY_TIMEOUT_SECONDS
    try:
        return max(0.05, min(_DELIVERY_TIMEOUT_MAX_SECONDS, float(raw)))
    except ValueError:
        return _DELEGATION_DELIVERY_TIMEOUT_SECONDS


def format_duration(seconds: float) -> str:
    """``3720.4`` → ``1h02m``; the shape a progress label can carry."""
    total = max(0, int(round(float(seconds or 0.0))))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class DelegationProgressReporter:
    """Interval-gated, thread-aware progress reporter for one CLI delegation.

    Every method is fail-open: it returns False instead of raising when reporting
    is off or delivery failed, and a failure is only ever logged.
    """

    def __init__(
        self,
        callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        run_id: str = "",
        backend: str = "",
        lane: str = "",
        heartbeat_seconds: Optional[float] = None,
        delivery_timeout_seconds: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._callback = callback
        self._loop = loop
        self._run_id = str(run_id or "")
        self._backend = str(backend or "")
        self._lane = str(lane or "")
        self._heartbeat_seconds = (
            _resolve_heartbeat_seconds()
            if heartbeat_seconds is None
            else max(0.0, float(heartbeat_seconds))
        )
        self._delivery_timeout = (
            _resolve_delivery_timeout_seconds()
            if delivery_timeout_seconds is None
            else max(0.05, float(delivery_timeout_seconds))
        )
        self._clock = clock or time.monotonic
        self._started_at = self._clock()
        self._last_report_at: Optional[float] = None

    @property
    def enabled(self) -> bool:
        """True when a callback is present and reporting is not switched off."""
        return self._callback is not None and delegation_progress_enabled()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def _audit_fields(self) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        if self._run_id:
            fields["run_id"] = self._run_id
        if self._backend:
            fields["backend"] = self._backend
        if self._lane:
            fields["lane"] = self._lane
        return fields

    async def report(
        self,
        stage: str,
        message: str,
        *,
        detail: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        """Send one ``{"stage", "message", ...}`` payload.

        Returns True when the payload was handed to the callback (a callback that
        itself raised is logged, not propagated).
        """
        if not self.enabled:
            return False
        collapsed = " ".join(str(message or "").split())
        if not collapsed:
            return False
        payload: Dict[str, Any] = {
            **self._audit_fields(),
            "stage": str(stage or "running"),
            "message": collapsed,
        }
        if detail:
            payload["detail"] = " ".join(str(detail).split())
        for key, value in extra.items():
            if value is not None and key not in payload:
                payload[key] = value
        # Anchor the heartbeat interval on the attempt, not on its outcome: a
        # callback that always raises must not be retried every poll interval.
        self._last_report_at = self._clock()
        try:
            await self._deliver(payload)
        except Exception as exc:  # noqa: BLE001 - a report never breaks the run
            logger.warning(
                "[DELEGATION_PROGRESS] %s report failed (stage=%s): %s: %s",
                self._run_id or "delegation",
                payload["stage"],
                type(exc).__name__,
                exc,
            )
            return False
        return True

    async def heartbeat(
        self,
        *,
        attempt: Optional[int] = None,
        total_attempts: Optional[int] = None,
        phase: Optional[str] = None,
    ) -> bool:
        """Send a "still running" report if the heartbeat interval has elapsed."""
        if not self.enabled or self._heartbeat_seconds <= 0.0:
            return False
        anchor = self._last_report_at
        if anchor is None:
            anchor = self._started_at
        if self._clock() - anchor < self._heartbeat_seconds:
            return False
        elapsed = self.elapsed_seconds
        detail_parts = [f"run {self._run_id}"] if self._run_id else []
        if isinstance(attempt, int) and isinstance(total_attempts, int) and total_attempts > 0:
            detail_parts.append(f"attempt {attempt}/{total_attempts}")
        if phase and str(phase) != "primary":
            detail_parts.append(f"phase {phase}")
        return await self.report(
            "running",
            f"Sub-agent still running · {format_duration(elapsed)}",
            detail=" · ".join(detail_parts) or None,
            elapsed_seconds=round(elapsed, 1),
            attempt=attempt if isinstance(attempt, int) else None,
            phase=str(phase) if phase else None,
        )

    async def _deliver(self, payload: Dict[str, Any]) -> None:
        """Hand *payload* to the callback, hopping threads when they differ."""
        callback = self._callback
        loop = self._loop
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - callers always have a loop
            current = None
        if loop is None or loop is current or loop.is_closed():
            await self._invoke(callback, payload)
            return
        future = asyncio.run_coroutine_threadsafe(self._invoke(callback, payload), loop)
        await asyncio.wait_for(asyncio.wrap_future(future), timeout=self._delivery_timeout)

    async def _invoke(
        self,
        callback: Optional[Callable[[Dict[str, Any]], Any]],
        payload: Dict[str, Any],
    ) -> None:
        try:
            result = callback(payload) if callback is not None else None
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - fail-open by contract
            logger.warning(
                "[DELEGATION_PROGRESS] progress callback failed (%s): %s: %s",
                payload.get("stage"),
                type(exc).__name__,
                exc,
            )


def build_delegation_progress(
    tool_context: Optional[Any],
    *,
    run_id: str = "",
    backend: str = "",
    lane: str = "",
    heartbeat_seconds: Optional[float] = None,
    clock: Optional[Callable[[], float]] = None,
) -> DelegationProgressReporter:
    """Reporter bound to a handler's ``ToolContext`` progress channel.

    Reads the callback and the loop that owns it off the context; a context
    without ``on_progress`` yields an inert reporter (every method is a no-op).
    """
    callback = getattr(tool_context, "on_progress", None) if tool_context is not None else None
    loop = getattr(tool_context, "on_progress_loop", None) if tool_context is not None else None
    return DelegationProgressReporter(
        callback,
        loop=loop,
        run_id=run_id,
        backend=backend,
        lane=lane,
        heartbeat_seconds=heartbeat_seconds,
        clock=clock,
    )


__all__ = [
    "ENV_DELIVERY_TIMEOUT_SECONDS",
    "ENV_HEARTBEAT_SECONDS",
    "ENV_PROGRESS_ENABLED",
    "DelegationProgressReporter",
    "build_delegation_progress",
    "delegation_progress_enabled",
    "format_duration",
]
