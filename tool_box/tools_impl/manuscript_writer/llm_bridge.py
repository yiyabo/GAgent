"""LLM plumbing for the manuscript pipeline.

Owns the three sanctioned top-level ``app.*`` imports of this package
(``update_usage_context``, ``LLMClient``, ``get_llm_service``) so no other
sibling adds one, and keeps the existing lazy/duck-typed streaming fallback
exactly as it was.

``update_usage_context`` is monkeypatched on the package namespace (7 test
sites), so ``_chat_inner`` reads it through the late-bound ``_facade()``
accessor rather than a module global. ``_chat`` itself is re-exported by the
facade and is what callers patch; the pipeline calls it through the facade.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Dict, List, Optional, Tuple

from app.llm import update_usage_context

from app.llm import LLMClient
from app.services.llm.llm_service import LLMService, get_llm_service

from .config import (
    _DEFAULT_HEARTBEAT_LOG_SEC,
    _DEFAULT_LLM_CALL_TIMEOUT_SEC,
    _DEFAULT_SECTION_TIMEOUT_SEC,
    _VALID_ARTICLE_MODES,
)

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import manuscript_writer as facade

    return facade


def _is_review_article_task(task: str) -> bool:
    text = str(task or "").strip().lower()
    if not text:
        return False
    review_markers = (
        "review",
        "review article",
        "literature review",
        "systematic review",
        "narrative review",
        "survey article",
        "state-of-the-art review",
        "write a review",
        "review on",
        "综述",
        "文献综述",
        "系统综述",
        "叙述性综述",
        "综述文章",
    )
    return any(marker in text for marker in review_markers)


def _normalize_article_mode(article_mode: Optional[str]) -> str:
    raw = str(article_mode or "").strip().lower()
    if not raw:
        return "auto"
    aliases = {
        "review_article": "review",
        "literature_review": "review",
        "review_synthesis": "review",
        "synthesis": "review",
        "original": "research",
        "original_research": "research",
        "research_article": "research",
        "study": "research",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in _VALID_ARTICLE_MODES:
        return "auto"
    return normalized


def _resolve_article_mode(article_mode: Optional[str], task: str) -> Tuple[str, bool]:
    requested = _normalize_article_mode(article_mode)
    if requested == "review":
        return requested, True
    if requested == "research":
        return requested, False
    resolved_review_mode = _is_review_article_task(task)
    return ("review" if resolved_review_mode else "research"), resolved_review_mode


def _resolve_model_name(model_name: Optional[str]) -> Optional[str]:
    if not model_name:
        return None
    env_value = os.getenv(model_name)
    if env_value:
        return env_value.strip() or None
    return model_name.strip()


def _build_llm_service(
    provider: Optional[str],
    model: Optional[str],
    *,
    timeout: Optional[float] = None,
) -> Tuple[LLMService, Optional[str]]:
    if timeout is not None:
        client = LLMClient(provider=provider, model=model, timeout=timeout)
        return LLMService(client), model
    if provider:
        client = LLMClient(provider=provider, model=model)
        return LLMService(client), model
    return get_llm_service(), model


async def _chat(
    llm: LLMService,
    prompt: str,
    model: Optional[str],
    max_tokens: Optional[int] = None,
    purpose: Optional[str] = None,
) -> str:
    label = purpose or "manuscript_writer"
    return await _await_with_deadline(
        _chat_inner(llm, prompt, model, max_tokens=max_tokens, purpose=purpose),
        timeout_sec=_llm_call_timeout_sec(),
        heartbeat_sec=_heartbeat_log_sec(),
        label=label,
    )


async def _chat_inner(
    llm: LLMService,
    prompt: str,
    model: Optional[str],
    max_tokens: Optional[int] = None,
    purpose: Optional[str] = None,
) -> str:
    _facade().update_usage_context(
        call_purpose=purpose or "manuscript_writer", tool_name="manuscript_writer", phase="tool"
    )
    kwargs: Dict[str, Any] = {}
    if model:
        kwargs["model"] = model
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    # Stream first: long-form section generation exceeds the upstream gateway's
    # idle-window on non-streaming calls, which produced silent 504 cut-offs and
    # looked like a hang. Streaming keeps bytes flowing; fall back to the
    # retrying non-streaming path only when streaming is unavailable or fails.
    stream_fn = getattr(llm, "stream_chat_async", None)
    if callable(stream_fn):
        try:
            chunks: List[str] = []
            async for delta in stream_fn(prompt, **kwargs):
                if isinstance(delta, str):
                    chunks.append(delta)
                else:
                    text = getattr(delta, "text", None) or getattr(delta, "content", None)
                    if text:
                        chunks.append(str(text))
            joined = "".join(chunks)
            if joined.strip():
                return joined
            logger.warning("manuscript_writer streaming returned empty content; falling back to chat_async")
        except Exception as exc:
            logger.warning("manuscript_writer streaming failed (%s); falling back to chat_async", exc)
    return await llm.chat_async(prompt, **kwargs)


async def _maybe_wait_with_timeout(
    operation: Awaitable[str],
    timeout_sec: Optional[float],
) -> str:
    if timeout_sec is None or timeout_sec <= 0:
        return await operation
    return await asyncio.wait_for(operation, timeout=timeout_sec)


def _env_timeout_sec(name: str, default: float) -> Optional[float]:
    """Overall-deadline env knob in seconds; <= 0 disables the deadline."""
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else None


def _llm_call_timeout_sec() -> Optional[float]:
    return _env_timeout_sec("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", _DEFAULT_LLM_CALL_TIMEOUT_SEC)


def _section_timeout_sec() -> Optional[float]:
    return _env_timeout_sec("MANUSCRIPT_SECTION_TIMEOUT_SEC", _DEFAULT_SECTION_TIMEOUT_SEC)


def _heartbeat_log_sec() -> float:
    try:
        value = float(os.getenv("MANUSCRIPT_HEARTBEAT_LOG_SEC", str(_DEFAULT_HEARTBEAT_LOG_SEC)) or _DEFAULT_HEARTBEAT_LOG_SEC)
    except (TypeError, ValueError):
        value = _DEFAULT_HEARTBEAT_LOG_SEC
    return max(0.0, value)


def _silence_task(task: "asyncio.Task[Any]") -> None:
    """Consume a cancelled task's terminal exception so it is never logged as
    'exception was never retrieved' after we abandon it on a deadline."""

    def _consume(done: "asyncio.Task[Any]") -> None:
        try:
            done.exception()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task.add_done_callback(_consume)


async def _await_with_deadline(
    operation: Awaitable[str],
    *,
    timeout_sec: Optional[float],
    heartbeat_sec: float,
    label: str,
) -> str:
    """Await *operation* under an overall deadline, logging periodic heartbeats.

    A bare httpx read timeout only bounds the gap between bytes: an upstream
    that trickles SSE keep-alives while never finishing the generation keeps
    resetting it and the call hangs forever (observed in production as the
    manuscript stage sitting silent for >900s). Bounding the whole coroutine
    instead makes every stage finish, fail, or be cancellable in finite time.
    """
    if (timeout_sec is None or timeout_sec <= 0) and heartbeat_sec <= 0:
        return await operation

    task = asyncio.ensure_future(operation)
    started_at = time.monotonic()
    try:
        while True:
            wait: Optional[float] = heartbeat_sec if heartbeat_sec > 0 else None
            if timeout_sec is not None and timeout_sec > 0:
                remaining = timeout_sec - (time.monotonic() - started_at)
                if remaining <= 0:
                    task.cancel()
                    _silence_task(task)
                    raise asyncio.TimeoutError(
                        f"manuscript_writer: {label} exceeded the {timeout_sec:g}s overall deadline"
                    )
                wait = remaining if wait is None else min(wait, remaining)
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=wait)
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - started_at
                if timeout_sec is not None and timeout_sec > 0 and elapsed >= timeout_sec:
                    task.cancel()
                    _silence_task(task)
                    raise asyncio.TimeoutError(
                        f"manuscript_writer: {label} exceeded the {timeout_sec:g}s overall deadline"
                    )
                logger.info(
                    "manuscript_writer heartbeat: %s still running (%.0fs elapsed)",
                    label,
                    elapsed,
                )
    except asyncio.CancelledError:
        task.cancel()
        _silence_task(task)
        raise
