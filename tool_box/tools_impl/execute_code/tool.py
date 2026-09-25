"""execute_code tool: Programmatic Tool Calling for GAgent ("code mode").

The orchestration model writes Python directly; the code runs in a persistent
session kernel and calls tool_box tools as plain Python functions
(``from gagent_tools import web_search``). Distinct from ``code_executor``,
which DELEGATES an agentic coding task to the pi coding harness.

The tool is env-gated (CODE_MODE_ENABLED=1); the offer-side gating lives in
``app/services/tool_schemas.py`` and ``app/routers/chat/request_routing.py``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional

from tool_box.context import ToolContext

from . import config, kernel as kernel_module
from .stub_gen import signature_lines

logger = logging.getLogger(__name__)

TOOL_NAME = "execute_code"

# Static teaching base — the main prompt carrier, mirroring the Hermes
# description. app/services/tool_schemas.py appends the dynamic signature
# list (per active allowlist) on top of this text.
BASE_DESCRIPTION = (
    "Run Python that calls GAgent tools programmatically in a PERSISTENT kernel. "
    "Use when you need 3+ tool calls with logic between them: loops over "
    "pages/files/accessions, filtering or reducing large tool outputs BEFORE "
    "they enter your context, branching, or retries. Use a normal tool call "
    "for a single call or results you must reason over in full. "
    "Division of labor: code_executor DELEGATES an agentic coding task to the "
    "pi coding harness (it writes and debugs the code); execute_code is YOU "
    "writing Python directly that calls tools as functions — prefer it for "
    "programmatic fan-out over tool results, not for general software tasks. "
    "The kernel keeps variables, imports, and loaded data across execute_code "
    "calls (pass reset=true to start fresh); a timed-out or interrupted call "
    "kills the kernel and LOSES that state — the result's kernel metadata "
    "(reused, execution_count, state_reset) always tells the truth about it. "
    "Tools are importable Python functions, e.g. "
    "`from gagent_tools import web_search`; each returns an ALREADY-PARSED "
    "dict — never json.loads() it. "
    "Limits: 5-minute cell timeout, max 50 tool calls per cell, stdout shown "
    "up to 50KB (head/tail; the full text is auto-saved to a file whose path "
    "rides in the result). The kernel cannot see host env secrets by design. "
    "Available functions (from gagent_tools import ...):"
)


def build_description(allowed: Optional[List[str]] = None) -> str:
    """BASE_DESCRIPTION + the dynamic per-allowlist signature list."""
    names = list(allowed) if allowed is not None else config.allowed_tools()
    lines = signature_lines(names)
    if not lines:
        return BASE_DESCRIPTION + " (none resolved — check CODE_MODE_ALLOWED_TOOLS)"
    return BASE_DESCRIPTION + "\n" + "\n".join(f"  {line}" for line in lines)


async def execute_code_handler(
    code: str,
    reset: bool = False,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Run one Python cell in the caller's session kernel."""
    if not config.code_mode_enabled():
        return {
            "success": False,
            "error": "code_mode_disabled",
            "summary": (
                "execute_code is disabled. Set CODE_MODE_ENABLED=1 to enable "
                "programmatic tool calling."
            ),
        }
    code_text = str(code or "")
    if not code_text.strip():
        return {
            "success": False,
            "error": "empty_code",
            "summary": "execute_code requires a non-empty 'code' string.",
        }

    work_dir = str(getattr(tool_context, "work_dir", "") or "") if tool_context else ""
    session_id = config.session_identity(tool_context)

    stop = threading.Event()

    def abort_check() -> bool:
        """Polled by the kernel wait loop (from the cell's worker thread).

        ``ToolContext.is_cancelled`` reports both a caller-set ``abort_event`` and
        the ambient cancel token the orchestrator binds once per run; the token is
        the one that actually crosses into this thread, which is why this check
        used to be dead (nothing ever set ``abort_event``).
        """
        if stop.is_set():
            return True
        return bool(
            tool_context is not None and getattr(tool_context, "is_cancelled", False)
        )

    cell = asyncio.to_thread(
        kernel_module.run_cell,
        code_text,
        session_id=session_id,
        work_dir=work_dir,
        reset=bool(reset),
        tool_context=tool_context,
        abort_check=abort_check,
    )
    try:
        return await cell
    except asyncio.CancelledError:
        # The orchestrator cancelled us: kill the process group (interrupt
        # contract), wait for the worker thread to finish the teardown, then
        # propagate the cancellation.
        stop.set()
        try:
            await asyncio.shield(cell)
        except Exception:  # noqa: BLE001 - cancellation wins regardless
            pass
        raise


execute_code_tool = {
    "name": TOOL_NAME,
    "description": build_description(),
    "category": "execution",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python source for one cell. Tool calls are plain function calls: "
                    "`from gagent_tools import web_search` then `web_search(query=...)`; "
                    "results are already-parsed dicts. Variables survive across cells."
                ),
            },
            "reset": {
                "type": "boolean",
                "description": (
                    "If true, discard the current kernel (all in-memory state) and "
                    "start a fresh one before running this cell."
                ),
                "default": False,
            },
        },
        "required": ["code"],
    },
    "handler": execute_code_handler,
    "tags": ["code", "execution", "python", "programmatic", "kernel"],
    "examples": [
        "Fetch 10 accessions in a loop and keep only the ones longer than 40kb",
        "Run 3 web searches, merge the results, and print a deduplicated table",
        "Query the knowledge graph for 5 entities and aggregate their relations",
    ],
}
