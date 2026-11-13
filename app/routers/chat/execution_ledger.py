"""Cross-turn verified execution ledger (Phase 0, 2026-11-13).

Live audit of session ``session_1794560809820_5661bikhg`` ("Scope-Agent联动测试"):
in turn 3 the model claimed "no tool was ever executed", although the run
database recorded 23 real tool calls in turn 1. Chat history only carries
``(user, assistant)`` text, so on follow-up turns the model has no objective
evidence of its own earlier tool activity and can hallucinate a denial.

This module rebuilds that evidence from ``plan_registry`` (``chat_runs`` +
``run_steps``) and appends a compact *system-verified* ledger to the most
recent assistant history messages. The ledger is a statement of recorded fact,
not a model-generated summary; the DeepThink system prompt instructs the model
to treat it as ground truth.

Fail-open by design: any database error returns the history unchanged.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

MAX_LEDGER_RUNS = 3
MAX_LEDGER_CHARS_PER_RUN = 400
_MAX_ARTIFACTS_PER_RUN = 2
_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


def execution_ledger_enabled() -> bool:
    return os.environ.get("CHAT_EXECUTION_LEDGER_ENABLED", "1").strip() != "0"


def build_run_ledger(run: dict[str, Any]) -> str | None:
    """Render one compact ledger line for a finished chat run, or None.

    ``run`` is a row from ``chat_runs.list_session_runs``. Steps are read from
    ``run_steps``; a run without recorded steps produces no ledger.
    """
    from app.repository import run_steps as run_steps_repo

    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        return None
    steps = run_steps_repo.list_steps(run_id)
    if not steps:
        return None

    per_tool: Counter[str] = Counter()
    failures = 0
    artifacts: list[str] = []
    for step in steps:
        tool = str(getattr(step, "tool_name", "") or "unknown")
        per_tool[tool] += 1
        status = str(getattr(step, "status", "") or "")
        if status not in {"succeeded", "ok", "success"}:
            failures += 1
        for ref in getattr(step, "output_refs", ()) or ():
            if isinstance(ref, str) and ref and len(ref) < 160 and ref not in artifacts:
                artifacts.append(ref)

    total = sum(per_tool.values())
    tool_bits = ", ".join(f"{name}×{count}" for name, count in per_tool.most_common(6))
    if len(per_tool) > 6:
        tool_bits += ", …"
    status = str(run.get("status") or "finished")
    ledger = (
        f"[Verified Execution Ledger run={run_id} status={status}: "
        f"{total} tool call(s) recorded in the run database — {tool_bits}; "
        f"{total - failures} succeeded, {failures} not-succeeded."
    )
    if artifacts:
        shown = "; ".join(artifacts[:_MAX_ARTIFACTS_PER_RUN])
        ledger += f" Artifacts: {shown}."
    ledger += (
        " These executions are system-recorded facts. Never deny that they "
        "happened and never fabricate executions that are not listed here.]"
    )
    if len(ledger) > MAX_LEDGER_CHARS_PER_RUN:
        ledger = ledger[: MAX_LEDGER_CHARS_PER_RUN - 2].rstrip() + "…]"
    return ledger


def apply_execution_ledger(
    history: list[dict[str, Any]],
    session_id: str | None,
    *,
    max_runs: int = MAX_LEDGER_RUNS,
) -> list[dict[str, Any]]:
    """Attach verified execution ledgers to trailing assistant messages.

    The most recent finished run with tool steps maps to the last assistant
    message, the previous run to the assistant message before it, and so on.
    Returns ``history`` unchanged when disabled, empty, or on any error.
    """
    if not execution_ledger_enabled() or not session_id or not history:
        return history
    try:
        from app.repository.chat_runs import list_session_runs

        runs = [
            run
            for run in list_session_runs(session_id, limit=12)
            if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES
        ]
        ledgers: list[str] = []
        for run in runs:
            ledger = build_run_ledger(run)
            if ledger:
                ledgers.append(ledger)
            if len(ledgers) >= max_runs:
                break
        if not ledgers:
            return history

        updated = [dict(message) for message in history]
        ledger_iter = iter(ledgers)
        for message in reversed(updated):
            if message.get("role") != "assistant":
                continue
            try:
                ledger = next(ledger_iter)
            except StopIteration:
                break
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = content + "\n\n" + ledger
        return updated
    except Exception as exc:  # fail-open: never block a chat turn on the ledger
        logger.debug("[CHAT][LEDGER] skipped for session=%s: %s", session_id, exc)
        return history
