"""Shared worker-thread event loop for plan auto-review (``agent`` cluster ③).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
③ ``review_loop.py``): the single daemon-thread asyncio loop that plan
auto-review reuses, plus the two helpers that own it.  ``agent.py`` re-exports
all four names, so the facade class keeps calling
``_run_blocking_on_review_loop(...)`` (agent.py 5557 / 5596) unchanged.

Patch surface: none of these four names is patched anywhere in ``app/`` or
``app/tests/``, and nothing in this cluster reads a patched ``agent`` binding —
so this cluster carries **zero body deviations**.

``_plan_review_loop`` is *rebound* (``None`` -> loop) by
``_get_plan_review_loop``, so the rebindable state lives here with its only
consumers; the facade's re-exported binding is an import-time snapshot that
nothing reads (documented, not a behaviour change).

No logger is used in this cluster; every call expression is unchanged.
"""

from __future__ import annotations

import asyncio
import threading

# Plan auto-review runs in a worker thread (no ambient loop). Reuse ONE
# daemon-thread loop instead of asyncio.run() per call, which would create a
# fresh loop and fresh LLM/DB connections every time. Do not "simplify" back.
_plan_review_loop = None
_plan_review_loop_lock = threading.Lock()


def _get_plan_review_loop():
    global _plan_review_loop
    with _plan_review_loop_lock:
        if _plan_review_loop is None or _plan_review_loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                name="plan-review-loop",
                daemon=True,
            )
            thread.start()
            _plan_review_loop = loop
    return _plan_review_loop


def _run_blocking_on_review_loop(coro):
    """asyncio.run() equivalent for worker threads, on the shared review loop."""
    future = asyncio.run_coroutine_threadsafe(coro, _get_plan_review_loop())
    return future.result()
