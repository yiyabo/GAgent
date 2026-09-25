"""code_executor job-log-stream bridge of ``agent`` (module-level cluster ⑦).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
⑦ ``code_executor_bridge.py``).  ``_code_executor_job_stream_loggers`` is the
only *module-level* member of that cluster: the rest of ⑦
(``_resolve_code_executor_task_context``, ``_should_route_code_executor_unscoped``,
``_prepare_code_executor_params``) are class-body methods of
``StructuredChatAgent`` and stay in ``agent.py`` for W5b/W5c.  ``agent.py``
re-exports this name, so its three class call sites are unchanged.

Patch surface (the reason for the two deviations): the returned hooks append to
``plan_decomposition_jobs``, which is patched **on the agent namespace** at 6
sites (app/tests/chat/test_no_fallback_policy.py).  The hooks run after the call
site returns, so both ``append_log`` call sites read the facade attribute
through ``_ag()`` at call time instead of binding the module object by value —
the call arguments (the "stdout"/"stderr" tags and the empty dict payload) are
unchanged.

No logger is used in this cluster.
"""

from __future__ import annotations

from typing import Any, Tuple


def _ag() -> Any:
    """Late-bound agent facade module (monkeypatch-friendly lookups)."""
    from . import agent

    return agent


def _code_executor_job_stream_loggers(job_id: str) -> Tuple[Any, Any]:
    """Stdout/stderr hooks for code_executor when a plan decomposition job log stream is active."""

    async def on_stdout(line: str) -> None:
        _ag().plan_decomposition_jobs.append_log(job_id, "stdout", line, {})

    async def on_stderr(line: str) -> None:
        _ag().plan_decomposition_jobs.append_log(job_id, "stderr", line, {})

    return on_stdout, on_stderr
