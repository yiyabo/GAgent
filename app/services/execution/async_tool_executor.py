"""AsyncToolExecutor — concurrent-safe tool execution with safety classification.

Splits tool calls into two buckets based on ``ToolDefinition.is_concurrent_safe``:
- **Concurrent-safe** tools (web_search, grep, etc.) run in parallel via asyncio.gather
- **Non-safe** tools (code_executor, phagescope, etc.) run sequentially after safe tools

Results are returned in the original submission order regardless of completion order.

Phase 2.1 of the architecture evolution (see docs/architecture-evolution.md).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PendingToolCall:
    """A tool call waiting to be executed."""

    index: int
    tool_name: str
    coroutine_factory: Callable[[], Awaitable[Dict[str, Any]]]
    is_concurrent_safe: bool = False
    result: Optional[Dict[str, Any]] = None


async def execute_with_concurrency(
    calls: List[PendingToolCall],
) -> List[Dict[str, Any]]:
    """Execute tool calls with concurrency control.

    Concurrent-safe calls run in parallel; non-safe calls run sequentially
    after all safe calls complete.  Results are returned in the original
    ``index`` order.

    Args:
        calls: List of PendingToolCall objects.  Each must have a
            ``coroutine_factory`` that returns a fresh awaitable on each
            invocation (do NOT pass an already-awaited coroutine).

    Returns:
        List of result dicts, sorted by ``index``.
    """
    if not calls:
        return []

    # Single call — skip classification overhead
    if len(calls) == 1:
        calls[0].result = await calls[0].coroutine_factory()
        return [calls[0].result]

    safe = [c for c in calls if c.is_concurrent_safe]
    unsafe = [c for c in calls if not c.is_concurrent_safe]

    if safe:
        logger.debug(
            "[AsyncToolExecutor] Running %d concurrent-safe tool(s) in parallel: %s",
            len(safe),
            [c.tool_name for c in safe],
        )
        results = await asyncio.gather(
            *[c.coroutine_factory() for c in safe],
            return_exceptions=True,
        )
        for call, result in zip(safe, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "[AsyncToolExecutor] Concurrent tool %s raised: %s",
                    call.tool_name,
                    result,
                )
                call.result = {
                    "success": False,
                    "error": str(result),
                    "summary": f"{call.tool_name} failed: {result}",
                }
            else:
                call.result = result

    for call in unsafe:
        logger.debug(
            "[AsyncToolExecutor] Running non-concurrent tool sequentially: %s",
            call.tool_name,
        )
        try:
            call.result = await call.coroutine_factory()
        except Exception as exc:
            logger.warning(
                "[AsyncToolExecutor] Sequential tool %s raised: %s",
                call.tool_name,
                exc,
            )
            call.result = {
                "success": False,
                "error": str(exc),
                "summary": f"{call.tool_name} failed: {exc}",
            }

    # Return in original submission order
    calls.sort(key=lambda c: c.index)
    return [c.result for c in calls]


def classify_tool_concurrency(tool_name: str) -> bool:
    """Check if a tool is marked as concurrent-safe in the registry.

    Falls back to False (sequential) if the tool is not registered or
    has no metadata.  Uses a lazy import to avoid circular dependency
    (deep_think_agent → async_tool_executor → tool_box → deep_think_agent).
    """
    from tool_box.tools import get_tool_registry  # lazy to break circular import

    registry = get_tool_registry()
    tool_def = registry.get_tool(tool_name)
    if tool_def is None:
        return False
    return bool(tool_def.is_concurrent_safe)
