"""AsyncToolExecutor — concurrent-safe tool execution with order-preserving segmentation.

Walks through tool calls in original index order and groups consecutive
concurrent-safe calls for parallel execution.  When a non-safe call is
encountered, the preceding safe batch is awaited first, then the unsafe
call runs alone.  This preserves causal dependencies within a single
LLM turn (e.g. write-then-read sequences).

Phase 2.1 of the architecture evolution (see docs/architecture-evolution.md).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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


async def _run_one(call: PendingToolCall) -> None:
    """Execute a single PendingToolCall and store its result."""
    try:
        call.result = await call.coroutine_factory()
    except Exception as exc:
        logger.warning(
            "[AsyncToolExecutor] Tool %s raised: %s", call.tool_name, exc,
        )
        call.result = {
            "success": False,
            "error": str(exc),
            "summary": f"{call.tool_name} failed: {exc}",
        }


async def execute_with_concurrency(
    calls: List[PendingToolCall],
) -> List[Dict[str, Any]]:
    """Execute tool calls with order-preserving concurrency.

    Walks through *calls* in index order and segments them into runs of
    consecutive concurrent-safe tools.  Each safe run executes in parallel;
    each unsafe call executes alone after the preceding batch completes.

    Example ordering for ``[safe, safe, unsafe, safe]``::

        asyncio.gather(safe_0, safe_1)   # parallel batch
        await unsafe_2                    # sequential
        await safe_3                      # single item, no gather overhead

    Results are returned in the original ``index`` order.
    """
    if not calls:
        return []

    if len(calls) == 1:
        await _run_one(calls[0])
        return [calls[0].result]

    # Segment into consecutive runs: [(is_safe, [call, ...]), ...]
    segments: List[tuple[bool, List[PendingToolCall]]] = []
    for call in calls:
        if segments and segments[-1][0] == call.is_concurrent_safe:
            segments[-1][1].append(call)
        else:
            segments.append((call.is_concurrent_safe, [call]))

    for is_safe, segment in segments:
        if is_safe and len(segment) > 1:
            logger.debug(
                "[AsyncToolExecutor] Running %d concurrent-safe tool(s) in parallel: %s",
                len(segment),
                [c.tool_name for c in segment],
            )
            results = await asyncio.gather(
                *[c.coroutine_factory() for c in segment],
                return_exceptions=True,
            )
            for call, result in zip(segment, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "[AsyncToolExecutor] Concurrent tool %s raised: %s",
                        call.tool_name, result,
                    )
                    call.result = {
                        "success": False,
                        "error": str(result),
                        "summary": f"{call.tool_name} failed: {result}",
                    }
                else:
                    call.result = result
        else:
            for call in segment:
                logger.debug(
                    "[AsyncToolExecutor] Running tool sequentially: %s",
                    call.tool_name,
                )
                await _run_one(call)

    calls.sort(key=lambda c: c.index)
    return [c.result for c in calls]


def classify_tool_concurrency(tool_name: str) -> bool:
    """Check if a tool is marked as concurrent-safe in the registry.

    Falls back to False (sequential) if the tool is not registered or
    has no metadata.
    """
    from tool_box.tools import get_tool_registry

    registry = get_tool_registry()
    tool_def = registry.get_tool(tool_name)
    if tool_def is None:
        return False
    return bool(tool_def.is_concurrent_safe)
