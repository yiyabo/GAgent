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

Phase 1.2 of the architecture evolution (see docs/architecture-evolution.md).
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

    # --- environment ---
    work_dir: str = ""
    data_dir: str = ""
    capability_floor: str = "tools"

    # --- orchestration state ---
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    """Tools invoked earlier in this agent turn, with name + success status."""

    # --- cancellation ---
    abort_event: Optional[asyncio.Event] = None
    """Set by the orchestrator when the user cancels; tools should check periodically."""

    # --- progress callback ---
    on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    """Async callback for reporting intermediate progress to the UI."""

    # --- extensible metadata ---
    extra: Dict[str, Any] = field(default_factory=dict)
    """Bag for future or caller-specific data without breaking the interface."""

    @property
    def is_cancelled(self) -> bool:
        """Check if the abort signal has been set."""
        return self.abort_event is not None and self.abort_event.is_set()
