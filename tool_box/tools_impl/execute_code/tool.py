"""execute_code tool: Programmatic Tool Calling for GAgent ("code mode").

The orchestration model writes Python directly; the code runs in a persistent
session kernel and calls tool_box tools as plain Python functions
(``from gagent_tools import web_search``). This is the default coding surface
(decision 2026-09-27): ``code_executor`` (the delegated-coding harness) is
offer-gated off by default, and whole-goal delegation goes through
``delegate_task``.

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
    "This is YOUR default way to run code: one script (reading or filtering a "
    "file, one-off statistics, a single plot, a loop over many tool calls) "
    "belongs here; hand a long self-contained GOAL (multi-file refactors, "
    "end-to-end dataset production) to delegate_task instead. "
    "Use it when you need 3+ tool calls with logic between them: loops over "
    "pages/files/accessions, filtering or reducing large tool outputs BEFORE "
    "they enter your context, branching, or retries. Use a normal tool call "
    "for a single call or results you must reason over in full. "
    "The kernel keeps variables, imports, and loaded data across execute_code "
    "calls (pass reset=true to start fresh); a timed-out or interrupted call "
    "kills the kernel and LOSES that state — the result's kernel metadata "
    "(reused, execution_count, state_reset) always tells the truth about it. "
    "Tools are importable Python functions, e.g. "
    "`from gagent_tools import web_search`; each returns an ALREADY-PARSED "
    "dict — never json.loads() it. "
    "The cell starts in your SESSION WORKSPACE: save output files with relative "
    "paths under results/ (e.g. plt.savefig('results/chart.png')); files you "
    "create are reported back in produced_files (session-relative), images "
    "under results/ are inlined into the final answer automatically, and "
    "deliverable_submit publishes them into Deliverables. "
    "Limits: 5-minute cell timeout, max 50 tool calls per cell, stdout shown "
    "up to 50KB (head/tail; the full text is auto-saved to a file whose path "
    "rides in the result). The kernel cannot see host env secrets by design. "
    "Available functions (from gagent_tools import ...):"
)


# The delegate_task pointer inside BASE_DESCRIPTION. Every *built* description
# swaps it out while the delegation surface is offer-gated off
# (DELEGATE_TASK_ENABLED != 1, LOCAL_INFRA §115) so the model is never pointed
# at a tool it cannot call. The static text keeps the pointer as the
# enabled-state source of truth: the native mirror and its drift lock are
# untouched, and the swap is a plain text replace pinned by
# app/tests/tools/test_execute_code_delegation_gate.py.
DELEGATE_POINTER = (
    "; hand a long self-contained GOAL (multi-file refactors, "
    "end-to-end dataset production) to delegate_task instead. "
)
DELEGATE_POINTER_OFF = (
    "; a long self-contained GOAL (multi-file refactors, end-to-end dataset "
    "production) is yours too: drive it here in stages, keeping state in the kernel. "
)


def apply_delegation_gate(text: str, *, enabled: Optional[bool] = None) -> str:
    """Return ``text`` with the delegate_task pointer swapped out while the tool is not offered."""
    offered = config.delegate_task_offered() if enabled is None else bool(enabled)
    if offered:
        return text
    return text.replace(DELEGATE_POINTER, DELEGATE_POINTER_OFF)


def build_description(allowed: Optional[List[str]] = None, *, progressive: Optional[bool] = None) -> str:
    """BASE_DESCRIPTION (delegation pointer gated) + the dynamic per-allowlist signature list."""
    base = apply_delegation_gate(BASE_DESCRIPTION)
    names = list(allowed) if allowed is not None else config.allowed_tools()
    from app.services.deep_think.runtime_policy import configured_policy
    use_progressive = configured_policy()['schemas'] if progressive is None else progressive
    if use_progressive:
        return base + "\nUse gagent_tools.list_tools() and gagent_tools.describe(name) to inspect signatures locally. Tools: " + ", ".join(names)
    lines = signature_lines(names)
    if not lines:
        return base + " (none resolved — check CODE_MODE_ALLOWED_TOOLS)"
    return base + "\n" + "\n".join(f"  {line}" for line in lines)


async def execute_code_handler(
    code: str = "",
    reset: bool = False,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Run one Python cell in the caller's session kernel.

    ``code`` defaults to empty so a call whose arguments arrived truncated
    (no ``code`` at all) is answered by the ``empty_code`` branch below instead
    of a raw TypeError leaking to the model and the user (LOCAL_INFRA §109).
    """
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

    from .artifacts import snapshot,observe
    before=await asyncio.to_thread(snapshot,work_dir)
    cell = asyncio.create_task(asyncio.to_thread(
        kernel_module.run_cell,
        code_text,
        session_id=session_id,
        work_dir=work_dir,
        reset=bool(reset),
        tool_context=tool_context,
        abort_check=abort_check,
    ))
    try:
        result=await asyncio.shield(cell)
        return await asyncio.to_thread(observe,result,before,work_dir)
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
