"""Record embedding usage into llm_usage for billing attribution.

Every embedding API call (and local-model computation) funnels through here
so volume is visible in the usage views regardless of which credential
channel served it. Attribution (session/plan/task/run) is inherited from the
ambient usage context; the billing key is pinned to
``internal.memory_embedding`` via the ``memory_embedding`` purpose, never to
whatever tool happened to trigger the call.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def estimate_embedding_tokens(texts: List[str]) -> int:
    """Fallback token estimate when the API response omits usage (~4 chars/token)."""
    try:
        return max(1, sum(len(str(t)) for t in texts) // 4)
    except Exception:  # pragma: no cover - defensive
        return 1


def record_embedding_usage(
    *,
    provider: str,
    model: str,
    texts: List[str],
    response_usage: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[float] = None,
    call_status: str = "ok",
) -> None:
    """Best-effort usage record; never raises into the embedding call path."""
    try:
        from app.llm import _log_usage, _usage_context, set_usage_context
    except Exception:  # pragma: no cover - llm module unavailable
        return
    try:
        prompt_tokens = 0
        if isinstance(response_usage, dict):
            for key in ("prompt_tokens", "total_tokens", "input_tokens"):
                value = response_usage.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    prompt_tokens = int(value)
                    break
        if prompt_tokens <= 0:
            prompt_tokens = estimate_embedding_tokens(texts)
        ctx = _usage_context.get() or {}
        token = set_usage_context(
            session_id=ctx.get("session_id"),
            plan_id=ctx.get("plan_id"),
            task_id=ctx.get("task_id"),
            call_purpose="memory_embedding",
            run_id=ctx.get("run_id"),
            phase=ctx.get("phase"),
        )
        try:
            _log_usage(
                provider,
                model,
                prompt_tokens,
                0,
                prompt_tokens,
                call_status=call_status,
                duration_ms=duration_ms,
            )
        finally:
            _usage_context.reset(token)
    except Exception as exc:  # pragma: no cover - observability must not break embeddings
        logger.warning("[EMBEDDING] Failed to record usage: %s", exc)
