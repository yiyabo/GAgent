"""Inherited foreground deadlines and cancellation across loops and threads.

One absolute monotonic deadline belongs to the run, including manual pauses and
task handoffs. Work stops before a reserved closeout window. Standalone calls
without a budget retain their existing tool timeout. A Python thread cannot be
forcibly killed: delegated subprocesses/kernel cells observe the shared token,
and nested async work inherits this same deadline rather than starting a new one.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional, TypeVar

from app.services.cancellation import CancelToken, current_cancel_token

logger = logging.getLogger(__name__)
_T = TypeVar("_T")
DEADLINE_REASON = "run_deadline_exceeded"


class RunDeadlineExceeded(RuntimeError):
    """Run-wide deadline, distinct from a retryable per-tool timeout."""


@dataclass
class RunBudget:
    total_seconds: float
    close_reserve_seconds: float = 10.0
    cancel_token: CancelToken = field(default_factory=lambda: current_cancel_token() or CancelToken())
    started_at: float = field(default_factory=time.monotonic)
    closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.total_seconds) or self.total_seconds <= 0:
            raise ValueError("a run budget must be positive")
        parent = current_run_budget()
        if parent is not None and parent.cancel_token is self.cancel_token:
            self.started_at = parent.started_at
            self.total_seconds = min(self.total_seconds, parent.total_seconds)
            self.close_reserve_seconds = max(self.close_reserve_seconds, parent.close_reserve_seconds)
        self.close_reserve_seconds = min(max(0.0, self.close_reserve_seconds), self.total_seconds / 2)
        self.cancel_token.set_deadline(self.deadline_at - self.close_reserve_seconds, DEADLINE_REASON)

    @property
    def deadline_at(self) -> float:
        return self.started_at + self.total_seconds

    def remaining_seconds(self, *, closeout: bool = False) -> float:
        reserve = 0.0 if closeout else self.close_reserve_seconds
        return max(0.0, self.deadline_at - reserve - time.monotonic())

    def remaining_work_seconds(self) -> float:
        from app.services.foundation.settings import get_settings
        reserve=getattr(get_settings(),'chat_run_synthesis_reserve_seconds',0)
        reserve=min(reserve,max(0,self.total_seconds-self.close_reserve_seconds)*.2)
        return max(0,self.remaining_seconds()-reserve)

    def should_finalize(self) -> bool:
        return self.remaining_work_seconds()<=0

    def expire(self) -> None:
        self.cancel_token.set(DEADLINE_REASON)

    def close(self) -> None:
        self.closed = True
        self.cancel_token.close()


_run_budget: contextvars.ContextVar[Optional[RunBudget]] = contextvars.ContextVar("run_budget", default=None)


def current_run_budget() -> Optional[RunBudget]:
    return _run_budget.get()


def bind_run_budget(budget: Optional[RunBudget]) -> contextvars.Token:
    return _run_budget.set(budget)


def reset_run_budget(handle: contextvars.Token) -> None:
    _run_budget.reset(handle)


def call_with_run_budget(budget: Optional[RunBudget], func: Callable[[], _T]) -> _T:
    handle = bind_run_budget(budget)
    try:
        return func()
    finally:
        reset_run_budget(handle)


def configured_run_budget(token: CancelToken) -> Optional[RunBudget]:
    """CHAT_RUN_BUDGET_SECONDS=0 disables only the shared deadline.

    Unset configuration honors the older execute budget customization. Tool
    caps are unchanged; larger foreground research budgets can be configured.
    """
    raw = os.getenv("CHAT_RUN_BUDGET_SECONDS")
    if raw is None:
        raw = os.getenv("DEEP_THINK_TIME_BUDGET_BREAK", "900")
    try:
        seconds = float(raw)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid foreground budget")
    except (TypeError, ValueError):
        seconds = 900.0
    try:
        reserve = float(os.getenv("CHAT_RUN_CLOSE_RESERVE_SECONDS", "10"))
        if not math.isfinite(reserve) or reserve < 0:
            raise ValueError("invalid closeout reserve")
    except (TypeError, ValueError):
        reserve = 10.0
    return RunBudget(seconds, reserve, token) if seconds else None


def _cancellation_error(token: Optional[CancelToken], cancel_event: Optional[asyncio.Event]) -> Optional[BaseException]:
    if token is not None and token.cancelled:
        if token.reason == DEADLINE_REASON:
            return RunDeadlineExceeded("The foreground run reached its wall-clock deadline.")
        return asyncio.CancelledError(token.reason or "Run cancelled.")
    if cancel_event is not None and cancel_event.is_set():
        return asyncio.CancelledError("Run cancelled.")
    return None


def check_run_active() -> None:
    """Stop synchronous plan control flow before retries or late finalization."""
    error = _cancellation_error(current_cancel_token(), None)
    if error is not None:
        raise error


async def _wait_cancel(token: Optional[CancelToken], cancel_event: Optional[asyncio.Event]) -> BaseException:
    while True:
        error = _cancellation_error(token, cancel_event)
        if error is not None:
            return error
        await asyncio.sleep(0.05)


async def cancel_and_join(task: asyncio.Future, *, grace_seconds: Optional[float] = None) -> None:
    """Join cooperative task teardown without allowing cleanup to reset time."""
    if not task.done():
        task.cancel()
    budget = current_run_budget()
    grace = grace_seconds if grace_seconds is not None else (
        min(budget.remaining_seconds(closeout=True), max(0.1, budget.close_reserve_seconds)) if budget else 5.0
    )
    done, _ = await asyncio.wait({task}, timeout=max(0.0, grace))
    if task in done:
        try:
            task.result()
        except BaseException:
            pass
    else:
        logger.warning("Run task did not settle before its cleanup deadline")
        # The caller must close its event sink before teardown. Consume a late
        # exception without permitting an abandoned producer to publish again.
        task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)


async def run_stage(
    awaitable: Awaitable[_T], *, stage: str, timeout: Optional[float] = None,
    cancel_event: Optional[asyncio.Event] = None, closeout: bool = False,
) -> _T:
    """Bound an active stage by min(its own cap, the inherited remaining time)."""
    budget = current_run_budget()
    token = budget.cancel_token if budget else current_cancel_token()
    # Closeout may persist deterministic partial/error reports after expiry;
    # it never grants additional execution time or fresh provider/tool work.
    watched_token = None if closeout else token
    if budget is None and watched_token is None and cancel_event is None:
        return await asyncio.wait_for(awaitable, timeout=timeout) if timeout is not None else await awaitable

    remaining = budget.remaining_seconds(closeout=closeout) if budget else None
    budget_limited = remaining is not None and (timeout is None or remaining <= timeout)
    limit = min(timeout, remaining) if timeout is not None and remaining is not None else (remaining if remaining is not None else timeout)
    error = _cancellation_error(watched_token, cancel_event)
    if error is None and limit is not None and limit <= 0:
        if budget_limited and budget is not None:
            budget.expire()
            error = _cancellation_error(token, cancel_event) or RunDeadlineExceeded(f"Run deadline reached before {stage}.")
        else:
            error = asyncio.TimeoutError(f"{stage} exceeded its {timeout}s timeout")
    if error is not None:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        elif isinstance(awaitable, asyncio.Future):
            await cancel_and_join(awaitable)
        raise error

    work = asyncio.ensure_future(awaitable)
    control = asyncio.create_task(_wait_cancel(watched_token, cancel_event))
    try:
        done, _ = await asyncio.wait({work, control}, timeout=limit, return_when=asyncio.FIRST_COMPLETED)
        if control in done:
            raise control.result()
        if work in done:
            if budget is not None and not closeout and budget.remaining_seconds() <= 0:
                budget.expire()
            error = _cancellation_error(watched_token, cancel_event)
            if error is not None:
                raise error
            return work.result()
        if budget_limited:
            assert budget is not None
            budget.expire()
            error = _cancellation_error(token, cancel_event)
            raise error or RunDeadlineExceeded(f"Run deadline reached during {stage}.")
        raise asyncio.TimeoutError(f"{stage} exceeded its {timeout}s timeout")
    finally:
        control.cancel()
        await asyncio.gather(control, return_exceptions=True)
        if not work.done():
            await cancel_and_join(work)


async def watch_run_owner(owner_task: asyncio.Task, budget: Optional[RunBudget], token: CancelToken, stop: asyncio.Event) -> None:
    """Interrupt the original worker context, including setup and detached work."""
    while not stop.is_set():
        if budget is not None and budget.remaining_seconds() <= 0:
            budget.expire()
        if token.cancelled:
            owner_task.cancel(token.reason)
            return
        delay = min(0.05, budget.remaining_seconds()) if budget is not None else 0.05
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.001, delay))
        except asyncio.TimeoutError:
            pass


async def iterate_stage(iterator: AsyncIterator[_T], *, stage: str, cancel_event: Optional[asyncio.Event] = None) -> AsyncIterator[_T]:
    """Supervise each active provider read against the same absolute deadline."""
    try:
        while True:
            try:
                item = await run_stage(iterator.__anext__(), stage=stage, cancel_event=cancel_event)
            except StopAsyncIteration:
                return
            yield item
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None and not getattr(iterator, "ag_running", False):
            try:
                await run_stage(close(), stage=f"{stage}:close", timeout=5.0, closeout=True)
            except (RunDeadlineExceeded, asyncio.CancelledError):
                raise
            except Exception as exc:
                logger.warning("Provider stream cleanup failed: %s", type(exc).__name__)


class SoftFinalize(RuntimeError):pass


def should_finalize():
    budget=current_run_budget()
    return bool(budget and budget.should_finalize())


async def run_work_stage(awaitable,*,stage,cancel_event=None):
    budget=current_run_budget()
    from app.services.foundation.settings import get_settings
    soft=getattr(get_settings(),'chat_run_synthesis_reserve_seconds',0)>0
    try:
        return await run_stage(awaitable,stage=stage,timeout=budget.remaining_work_seconds() if budget and soft else None,cancel_event=cancel_event)
    except asyncio.TimeoutError:
        if budget and soft and budget.should_finalize():raise SoftFinalize('work_window_ended')
        raise


async def iterate_work_stage(iterator,*,stage,cancel_event=None):
    try:
        while True:
            try:yield await run_work_stage(iterator.__anext__(),stage=stage,cancel_event=cancel_event)
            except StopAsyncIteration:return
    finally:
        close=getattr(iterator,'aclose',None)
        if close and not getattr(iterator,'ag_running',False):await run_stage(close(),stage=stage+':close',timeout=5,closeout=True)
