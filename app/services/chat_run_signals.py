"""Durable run-signal pump: DB-backed fallback delivery for cancel/steer.

Fast paths (in-process hub, realtime-bus routing) already deliver signals in
milliseconds when the owning worker is healthy. This pump closes the remaining
gap: when the routed control fails (worker unreachable, bus degraded), the
signal row persisted by the route handler is picked up here within
``CHAT_RUN_SIGNAL_POLL_SECONDS`` and applied to the in-process hub. All DB
errors are fail-open — the pump never breaks a run.
"""

from __future__ import annotations

import asyncio
import logging
import os

from app.repository.chat_runs import (
    fetch_unconsumed_chat_run_signals,
    mark_chat_run_signals_consumed,
    reap_expired_chat_runs,
)
from app.services import chat_run_hub as hub

logger = logging.getLogger(__name__)


def signal_poll_seconds() -> float:
    raw = str(os.getenv("CHAT_RUN_SIGNAL_POLL_SECONDS") or "").strip()
    try:
        return max(0.5, float(raw)) if raw else 1.5
    except ValueError:
        return 1.5


def lease_ttl_seconds() -> int:
    raw = str(os.getenv("CHAT_RUN_LEASE_TTL_SECONDS") or "").strip()
    try:
        return max(5, int(raw)) if raw else 30
    except ValueError:
        return 30


def sweep_interval_seconds() -> float:
    raw = str(os.getenv("CHAT_RUN_SWEEP_INTERVAL_SECONDS") or "").strip()
    try:
        return max(5.0, float(raw)) if raw else 30.0
    except ValueError:
        return 30.0


async def _apply_signals_once(run_id: str) -> None:
    rows = await asyncio.to_thread(fetch_unconsumed_chat_run_signals, run_id)
    if not rows:
        return
    consumed: list[int] = []
    for row in rows:
        kind = row["kind"]
        if kind == "cancel":
            hub.request_cancel(run_id)
            consumed.append(int(row["id"]))
            logger.info("[CHAT][RUN] durable cancel signal applied run=%s", run_id)
        elif kind == "steer":
            message = str(row["payload"].get("message") or "").strip()
            if message:
                hub.push_steer_message(run_id, message)
            consumed.append(int(row["id"]))
            logger.info("[CHAT][RUN] durable steer signal applied run=%s", run_id)
        else:
            # Unknown kinds are consumed so they do not accumulate.
            consumed.append(int(row["id"]))
            logger.warning("[CHAT][RUN] unknown signal kind=%s consumed run=%s", kind, run_id)
    await asyncio.to_thread(mark_chat_run_signals_consumed, consumed)


async def run_signal_pump(run_id: str, stop_event: asyncio.Event) -> None:
    """Poll and apply durable signals until ``stop_event`` is set."""
    poll = signal_poll_seconds()
    while not stop_event.is_set():
        try:
            await _apply_signals_once(run_id)
        except Exception as exc:  # fail-open: fast paths still deliver
            logger.warning(
                "[CHAT][RUN] signal pump iteration failed run=%s error=%s",
                run_id,
                type(exc).__name__,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll)
        except asyncio.TimeoutError:
            continue


async def run_lease_sweeper(stop_event: asyncio.Event) -> None:
    """Periodically reap runs whose worker lease expired (multi-instance safe)."""
    interval = sweep_interval_seconds()
    while not stop_event.is_set():
        try:
            reaped = await asyncio.to_thread(
                reap_expired_chat_runs, ttl_seconds=lease_ttl_seconds()
            )
            if reaped:
                logger.info("[CHAT][RUN] lease sweeper reaped %d expired run(s)", reaped)
        except Exception as exc:  # pragma: no cover - sweeper must not die
            logger.warning(
                "[CHAT][RUN] lease sweeper iteration failed error=%s", type(exc).__name__
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
