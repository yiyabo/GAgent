"""Continue chat runs that a server restart interrupted, without a browser.

The browser can resume an interrupted run itself, but only while the tab is
open. After a deploy the server does it: once the old workers' leases have
expired, every recently interrupted run that is safe to continue gets one
continuation run (``client_message_id = resume:<run_id>``), exactly like the
"继续此任务" button. Runs the user already moved past are left alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def enabled() -> bool:
    raw = os.getenv("CHAT_RUN_AUTO_RESUME_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def window_hours() -> float:
    try:
        return max(0.1, float(os.getenv("CHAT_RUN_AUTO_RESUME_WINDOW_HOURS", "12")))
    except ValueError:
        return 12.0


def resume_key(run_id: str) -> str:
    return f"resume:{run_id}"


def _superseded(row: dict[str, Any]) -> bool:
    """True when the session has a newer run, so the user already moved on."""
    from app.repository.chat_runs import list_session_runs

    for other in list_session_runs(row["session_id"], limit=20):
        if other["run_id"] != row["run_id"] and str(other.get("created_at") or "") > str(row.get("created_at") or ""):
            return True
    return False


def continuation_request(row: dict[str, Any]) -> Any:
    """Rebuild the original request as a continuation of ``row``."""
    from app.routers.chat.models import ChatRequest

    try:
        payload = json.loads(row.get("request_json") or "")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"run {row.get('run_id')} has no reconstructable request") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"run {row.get('run_id')} has no reconstructable request")
    payload["session_id"] = row["session_id"]
    payload["client_message_id"] = resume_key(row["run_id"])
    # The stored payload already carries the identity bound when the user sent
    # it (platform runs keep their trusted project/user binding in context).
    payload["context"] = {
        **(payload.get("context") or {}),
        "resume_from_run_id": row["run_id"],
        "auto_resumed_after_restart": True,
    }
    return ChatRequest.model_validate(payload)


def resume_one(row: dict[str, Any]) -> dict[str, Any]:
    from app.repository.chat_runs import get_chat_run_by_idempotency_key
    from app.routers.chat.run_routes import start_background_chat_run
    from app.services.run_resume import resume_info

    outcome: dict[str, Any] = {"run_id": row["run_id"], "session_id": row["session_id"]}
    if get_chat_run_by_idempotency_key(row["session_id"], resume_key(row["run_id"])) is not None:
        return {**outcome, "status": "already_resumed"}
    if _superseded(row):
        return {**outcome, "status": "superseded"}
    info = resume_info(row)
    if not info.get("can_resume") or not info.get("automatic_resume_allowed"):
        return {**outcome, "status": "not_resumable", "reason_code": info.get("reason_code")}
    child = start_background_chat_run(
        continuation_request(row), session_id=row["session_id"], owner_id=row["owner_id"],
    )
    return {**outcome, "status": "resumed", "new_run_id": child}


def resume_interrupted_runs() -> list[dict[str, Any]]:
    """Must run on the event loop thread (workers are spawned as loop tasks)."""
    from app.repository.chat_runs import list_restart_interrupted_runs

    if not enabled():
        return []
    outcomes: list[dict[str, Any]] = []
    for row in list_restart_interrupted_runs(window_hours=window_hours()):
        try:
            outcomes.append(resume_one(row))
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the rest
            logger.warning("Could not auto-resume chat run %s: %s", row.get("run_id"), exc)
            outcomes.append({"run_id": row.get("run_id"), "status": "error", "error": str(exc)})
    return outcomes


async def resume_after_restart(stop: asyncio.Event | None = None) -> list[dict[str, Any]]:
    """Wait for the old workers' leases to lapse, reap them, then continue them."""
    from app.repository.chat_runs import reap_expired_chat_runs
    from app.services.chat_run_signals import lease_ttl_seconds

    if not enabled():
        return []
    ttl = lease_ttl_seconds()
    try:
        if stop is not None:
            await asyncio.wait_for(stop.wait(), timeout=ttl + 5)
            return []  # shutting down again
        await asyncio.sleep(ttl + 5)
    except asyncio.TimeoutError:
        pass
    try:
        await asyncio.to_thread(reap_expired_chat_runs, ttl_seconds=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat run reap before auto-resume failed: %s", exc)
    outcomes = resume_interrupted_runs()
    resumed = [o for o in outcomes if o.get("status") == "resumed"]
    if outcomes:
        logger.info("Chat runs after restart: %d resumed, outcomes=%s", len(resumed), outcomes)
    return outcomes


__all__ = [
    "continuation_request",
    "enabled",
    "resume_after_restart",
    "resume_interrupted_runs",
    "resume_key",
    "resume_one",
    "window_hours",
]
