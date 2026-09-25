"""``execute_code`` stops when the run is cancelled — for real.

``ToolContext.abort_event`` was read by ``execute_code``'s kernel poll but never
written by anyone, so the check was dead: a stopped chat run left the cell
running.  The live signal is the thread-safe cancel token the orchestrator binds
once per run (``app/services/cancellation.py``), which is also the only signal
that can cross into the kernel's worker thread.  These tests pin that contract at
the level the tool actually observes: a real kernel cell must be interrupted.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services import cancellation
from app.services.cancellation import CancelToken
from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module
from tool_box.tools_impl.execute_code.tool import execute_code_handler


@pytest.fixture(autouse=True)
def _clean_state():
    cancellation.set_cancel_token(None)
    yield
    cancellation.set_cancel_token(None)
    kernel_module.shutdown_all_kernels()


@pytest.fixture
def kernel_context(monkeypatch: pytest.MonkeyPatch, tmp_path) -> ToolContext:
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    return ToolContext(session_id="cancel-token-test", work_dir=str(tmp_path))


async def test_cancel_token_interrupts_a_running_cell(
    kernel_context: ToolContext,
) -> None:
    token = CancelToken()
    token.set("chat_run_cancelled")
    cancellation.set_cancel_token(token)

    started_at = time.monotonic()
    result = await execute_code_handler(
        code="import time; time.sleep(30)", tool_context=kernel_context
    )
    elapsed = time.monotonic() - started_at

    assert result["success"] is False
    assert result["status"] == "interrupted"
    assert "interrupted" in result["error"].lower()
    # The proof that the cell did not run to completion.
    assert elapsed < 10.0


async def test_cancel_token_set_mid_cell_interrupts_it(
    kernel_context: ToolContext,
) -> None:
    token = CancelToken()
    cancellation.set_cancel_token(token)

    async def _cancel_soon() -> None:
        await asyncio.sleep(0.3)
        token.set("chat_run_cancelled")

    canceller = asyncio.create_task(_cancel_soon())
    started_at = time.monotonic()
    result = await execute_code_handler(
        code="import time; time.sleep(30)", tool_context=kernel_context
    )
    elapsed = time.monotonic() - started_at
    await canceller

    assert result["status"] == "interrupted"
    assert result["success"] is False
    assert elapsed < 10.0


async def test_without_a_cancel_signal_the_cell_runs_normally(
    kernel_context: ToolContext,
) -> None:
    """Control: no signal bound ⇒ nothing interrupts the cell."""
    result = await execute_code_handler(code="print('ran')", tool_context=kernel_context)

    assert result["success"] is True
    assert result["status"] == "success"
    assert "ran" in result["output"]


async def test_a_caller_set_abort_event_still_interrupts(
    kernel_context: ToolContext,
) -> None:
    """The legacy field keeps working when a caller does set it."""
    event = asyncio.Event()
    event.set()
    kernel_context.abort_event = event

    result = await execute_code_handler(
        code="import time; time.sleep(30)", tool_context=kernel_context
    )

    assert result["status"] == "interrupted"


def test_is_cancelled_tracks_the_ambient_token() -> None:
    assert ToolContext().is_cancelled is False

    token = CancelToken()
    cancellation.set_cancel_token(token)
    assert ToolContext().is_cancelled is False

    token.set("chat_run_cancelled")
    assert ToolContext().is_cancelled is True

    # An explicit (legacy) event is honored on its own, with no token bound.
    cancellation.set_cancel_token(None)
    event = asyncio.Event()
    event.set()
    assert ToolContext(abort_event=event).is_cancelled is True
