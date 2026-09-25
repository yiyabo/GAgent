"""ToolContext — structured execution context passed to tool handlers.

Handlers that want access to orchestration context can declare a
``tool_context: ToolContext`` keyword parameter.  Handlers that don't
declare it will never see it (``prepare_handler_kwargs`` strips unknown
kwargs automatically).

Usage in a tool handler::

    async def my_handler(query: str, tool_context: ToolContext | None = None):
        if tool_context:
            logger.info("Running in session %s", tool_context.session_id)
        ...

"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class ToolContext:
    """Execution context injected into tool handlers by the orchestrator.

    All fields are optional with safe defaults so a bare ``ToolContext()``
    is always valid.
    """

    # --- identity ---
    session_id: Optional[str] = None
    plan_id: Optional[int] = None
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    job_id: Optional[str] = None
    owner_id: Optional[str] = None

    # --- environment ---
    work_dir: str = ""
    data_dir: str = ""

    # --- orchestration state ---
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    """Tools invoked earlier in this agent turn, with name + success status."""

    # --- cancellation ---
    abort_event: Optional[asyncio.Event] = None
    """Legacy per-context abort flag; the orchestrator does **not** populate it.

    Handlers may set it themselves and it is still honored when present, but it
    is not the live cancellation signal: the orchestrator sets the thread-safe
    ambient ``CancelToken`` once per run (``app.services.cancellation``), because
    an ``asyncio.Event`` belongs to one loop and cannot cross the worker-thread
    hop a delegated CLI takes.  Check :attr:`is_cancelled`, not this field.
    """

    # --- progress callback ---
    on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    """Async callback for reporting intermediate progress to the UI."""

    on_progress_loop: Optional[asyncio.AbstractEventLoop] = None
    """Event loop that owns :attr:`on_progress`.

    A handler running on another thread (``delegate_task`` → ``execute_sync`` →
    ``asyncio.run``) cannot await the callback in place and must post the payload
    back to this loop; ``None`` means the callback's loop is the caller's own.
    Set by whoever injects :attr:`on_progress`.
    """

    # --- extensible metadata ---
    model_provider: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    """Bag for future or caller-specific data without breaking the interface."""

    @property
    def is_cancelled(self) -> bool:
        """Whether this execution has been asked to stop.

        Consults a caller-set :attr:`abort_event` when there is one, plus the
        ambient cancel token the orchestrator binds for every cancellable run —
        the token is what actually reaches a delegated CLI's worker thread.
        """
        if self.abort_event is not None and self.abort_event.is_set():
            return True
        try:
            from app.services.cancellation import current_cancel_token
        except ImportError:  # pragma: no cover - tool_box used without the app
            return False
        token = current_cancel_token()
        return bool(token is not None and token.cancelled)
