"""Explicit continuation creates a new run; terminal history stays immutable."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def current_resume_source() -> str | None:
    """Nested plan/tool controllers inherit the parent run's continuation intent."""
    from app.repository.chat_runs import get_chat_run
    from app.services.chat_run_state import chat_run_claim

    claim = chat_run_claim.get()
    if claim is None:
        return None
    row = get_chat_run(claim[0])
    payload = json.loads((row or {}).get("request_json") or "{}")
    source = (payload.get("context") or {}).get("resume_from_run_id")
    return str(source) if source else None


async def prepare_run_resume(run_id: str, context: dict[str, Any]) -> None:
    source = context.get("resume_from_run_id")
    if not source:
        return
    from app.services.execution.step_ledger import StepLedger

    ledger = StepLedger(run_id)
    await asyncio.to_thread(ledger.import_from, str(source))
