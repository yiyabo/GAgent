"""Per-invocation metering for tools billed per call rather than per token.

Some billable capabilities (web_search, literature_pipeline) make no LLM call
of their own — the token cost rides inside the enclosing chat/deep-think call,
while the upstream charges per search/pipeline invocation.  To keep every
registered billing key observable in the ledger (口径 2026-09-29: 每个注册
key 都必须能点到量), each invocation writes one zero-token row carrying the
tool's billing key and the per-call fee (env-knobbed; defaults follow the
upstream list price).  Rows are marked ``provider="tool_invocation"`` /
``call_purpose="tool_invocation"`` so token-based reports can separate fee
rows from LLM rows.  Recording never raises and never blocks the tool path.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_FEES_CNY = {
    "web_search": 0.004,  # upstream list price: 4 CNY / 1K calls
    "literature_pipeline": 0.0,  # retrieval-only; fee stays 0 until a cost model is confirmed
}

_ENV_OVERRIDES = {
    "web_search": "WEB_SEARCH_INVOCATION_CNY",
    "literature_pipeline": "LITERATURE_PIPELINE_INVOCATION_CNY",
}

_BILLING_KEYS = {
    "web_search": "tool.web_search",
    "literature_pipeline": "tool.literature_pipeline",
}


def _invocation_fee_cny(tool_name: str) -> float:
    env_name = _ENV_OVERRIDES.get(tool_name)
    if env_name:
        raw = os.getenv(env_name)
        if raw is not None and str(raw).strip():
            try:
                return max(0.0, float(str(raw).strip()))
            except (TypeError, ValueError):
                logger.warning("Invalid %s=%r; falling back to default", env_name, raw)
    return _DEFAULT_FEES_CNY.get(tool_name, 0.0)


def record_tool_invocation(
    *,
    tool_name: str,
    call_status: str = "ok",
    duration_ms: Optional[float] = None,
) -> None:
    """Write one zero-token fee row for a per-call billable tool invocation."""
    try:
        from app.llm import current_usage_context
        from app.repository.llm_usage import log_llm_usage

        ctx = current_usage_context()
        log_llm_usage(
            provider="tool_invocation",
            model=tool_name,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            session_id=ctx.get("session_id"),
            plan_id=ctx.get("plan_id"),
            task_id=ctx.get("task_id"),
            run_id=ctx.get("run_id"),
            call_purpose="tool_invocation",
            tool_name=tool_name,
            billing_key=_BILLING_KEYS.get(tool_name),
            call_status=call_status or "ok",
            duration_ms=duration_ms,
            input_cost=0.0,
            output_cost=0.0,
            estimated_cost=_invocation_fee_cny(tool_name),
            cost_currency="CNY",
        )
    except Exception as exc:  # never block the tool path
        logger.warning("record_tool_invocation(%s) failed: %s", tool_name, exc)


class invocation_timer:
    """Context manager timing a tool invocation and metering it on exit."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.call_status = "ok"
        self._started = 0.0

    def __enter__(self) -> "invocation_timer":
        self._started = time.monotonic()
        return self

    def mark_error(self) -> None:
        self.call_status = "error"

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.call_status = "error"
        record_tool_invocation(
            tool_name=self.tool_name,
            call_status=self.call_status,
            duration_ms=(time.monotonic() - self._started) * 1000.0,
        )
        return False
