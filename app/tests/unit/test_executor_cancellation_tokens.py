"""Cross-thread cancellation tokens: set/read across the hops a chat run takes.

The cancel signal is produced on the event loop (route, durable pump, bus
consumer) while the delegated work it must interrupt runs in a worker thread
(``asyncio.to_thread``) under its own loop (``execute_sync`` → ``asyncio.run``).
These tests pin the propagation contract for every hop of that chain, including
the one gap where ``ThreadPoolExecutor.submit`` does not copy contextvars.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import cancellation
from app.services.cancellation import CancelToken


@pytest.fixture(autouse=True)
def _clean_token_context():
    cancellation.set_cancel_token(None)
    yield
    cancellation.set_cancel_token(None)


def test_no_token_is_bound_by_default() -> None:
    assert cancellation.current_cancel_token() is None


def test_set_is_idempotent_and_records_the_reason() -> None:
    token = CancelToken()
    assert token.cancelled is False

    assert token.set("chat_run_cancelled") is True
    assert token.cancelled is True
    assert token.reason == "chat_run_cancelled"
    # Idempotent: a second cancel does not flip anything nor rewrite the reason.
    assert token.set("other") is False
    assert token.reason == "chat_run_cancelled"


def test_token_is_readable_from_a_worker_thread_after_set() -> None:
    token = CancelToken()

    def _worker() -> bool:
        return token.wait(timeout=5.0)

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiter = pool.submit(_worker)
        threading.Timer(0.05, lambda: token.set("chat_run_cancelled")).start()
        assert waiter.result(timeout=5.0) is True


async def test_token_survives_the_asyncio_to_thread_hop() -> None:
    """Chat path: the delegation is handed to a thread by ``asyncio.to_thread``."""
    token = CancelToken()
    cancellation.set_cancel_token(token)

    seen = await asyncio.to_thread(cancellation.current_cancel_token)

    assert seen is token
    assert seen is not None and seen.cancelled is False


async def test_token_survives_the_execute_sync_plan_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan path: thread → ``execute_sync`` → ``asyncio.run`` keeps the token.

    Mirrors the delegation shape of ``CodeAgentTaskDelegateExecutor.execute``.
    """
    import tool_box
    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        UnifiedToolExecutor,
    )

    token = CancelToken()
    cancellation.set_cancel_token(token)
    seen: list[object] = []

    async def _fake_execute_tool(tool_name, **kwargs):  # noqa: ANN001
        seen.append(cancellation.current_cancel_token())
        return {"success": True}

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    payload = await asyncio.to_thread(
        lambda: UnifiedToolExecutor().execute_sync(
            "code_executor",
            {"task": "do the thing"},
            context=ToolExecutionContext(session_id="session-x"),
        )
    )

    assert payload["success"] is True
    assert seen and seen[0] is token


async def test_execute_sync_from_a_running_loop_rebinds_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``ThreadPoolExecutor.submit`` gap: the token must be re-bound by hand.

    ``execute_sync`` offloads to a ``ThreadPoolExecutor`` when a loop is already
    running, and ``submit`` does not copy the caller's contextvars.  (A plain
    ``run_in_executor`` hop drops the token too, so this call is made from the
    loop thread itself — the shape the branch exists for.)
    """
    import tool_box
    from app.services.execution.tool_executor import UnifiedToolExecutor

    token = CancelToken()
    cancellation.set_cancel_token(token)
    seen: list[object] = []

    async def _fake_execute_tool(tool_name, **kwargs):  # noqa: ANN001
        seen.append(cancellation.current_cancel_token())
        assert asyncio.get_running_loop().is_running() is True
        return {"success": True}

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    payload = UnifiedToolExecutor().execute_sync("code_executor", {"task": "t"})

    assert payload["success"] is True
    assert seen and seen[0] is token


def test_call_with_cancel_token_without_a_token_adds_no_binding() -> None:
    assert cancellation.current_cancel_token() is None
    assert cancellation.call_with_cancel_token(None, lambda: cancellation.current_cancel_token()) is None


def test_set_and_reset_restore_the_previous_token() -> None:
    outer = CancelToken()
    inner = CancelToken()
    cancellation.set_cancel_token(outer)

    handle = cancellation.set_cancel_token(inner)
    assert cancellation.current_cancel_token() is inner

    cancellation.reset_cancel_token(handle)
    assert cancellation.current_cancel_token() is outer


def test_hub_cancel_sets_the_run_token_and_cleanup_clears_it() -> None:
    from app.services import chat_run_hub as hub

    run_id = "run_cancel_token_unit"
    token = hub.ensure_cancel_token(run_id)
    assert token.cancelled is False

    hub.request_cancel(run_id)

    assert hub.cancel_token(run_id) is token
    assert token.cancelled is True
    assert token.reason == "chat_run_cancelled"
    # The loop-side event keeps its own contract.
    assert hub.ensure_cancel_event(run_id).is_set() is True

    hub.cleanup_run_signals(run_id)

    assert hub.cancel_token(run_id) is None
