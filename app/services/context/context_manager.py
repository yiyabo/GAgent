"""Token-aware context window manager with proactive compaction.

Tracks token usage across the message list and triggers LLM-based
summarization when approaching the model's context window limit.

Phase 4 of the architecture evolution (see docs/architecture-evolution.md).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

from functools import lru_cache


@lru_cache(maxsize=1)
def _get_encoder():
    """Return a cached tiktoken encoder (lazy-loaded, cached after first call)."""
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text: str) -> int:
    """Estimate token count using cl100k_base encoding.

    This is an approximation — exact counts depend on the actual model
    tokenizer, but cl100k_base is a reasonable proxy for Qwen/OpenAI/Claude.
    """
    if not text:
        return 0
    try:
        return len(_get_encoder().encode(text))
    except Exception:
        # Fallback: ~4 chars per token for English, ~2 for CJK
        return max(1, len(text) // 3)


def estimate_message_tokens(message: Dict[str, Any]) -> int:
    """Estimate tokens in a single message dict.

    Accounts for role, content, and optional tool_calls/function_call fields.
    Adds overhead for message framing (~4 tokens per message).
    """
    tokens = 4  # message framing overhead
    content = message.get("content")
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    elif isinstance(content, list):
        # Multi-part content (images, text blocks)
        for part in content:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content") or ""
                if isinstance(text, str):
                    tokens += estimate_tokens(text)
            elif isinstance(part, str):
                tokens += estimate_tokens(part)

    # Tool calls add their serialized JSON
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            tokens += estimate_tokens(json.dumps(tc, ensure_ascii=False))

    return tokens


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens across a message list."""
    return sum(estimate_message_tokens(m) for m in messages)


# ---------------------------------------------------------------------------
# Context usage tracking
# ---------------------------------------------------------------------------

# Known context window sizes (in tokens) for common models.
# Conservative estimates — actual limits may be higher.
_MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "qwen-plus": 131072,
    "qwen-max": 131072,
    "qwen-turbo": 131072,
    "qwen3.6-plus": 256000,
    "qwen3.5-plus": 131072,
    "qwen-long": 1000000,
    "kimi-k2.5": 131072,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
}

# Fallback context window when model is unknown
_DEFAULT_CONTEXT_WINDOW = 131072


def get_context_window(model: str) -> int:
    """Return context window size for a model, with fallback."""
    if not model:
        return _DEFAULT_CONTEXT_WINDOW
    model_lower = model.strip().lower()
    # Exact match
    if model_lower in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[model_lower]
    # Prefix match (e.g., "qwen-plus-latest" → "qwen-plus")
    for key, value in _MODEL_CONTEXT_WINDOWS.items():
        if model_lower.startswith(key):
            return value
    return _DEFAULT_CONTEXT_WINDOW


@dataclass
class ContextUsage:
    """Snapshot of context window utilization."""
    used_tokens: int
    max_tokens: int
    ratio: float
    warning: bool
    critical: bool

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class ContextWindowManager:
    """Manages context window budget with proactive compaction.

    Usage::

        mgr = ContextWindowManager(model="qwen3.6-plus")
        messages = [...]

        # Check if compaction needed before LLM call
        usage = mgr.check_usage(messages)
        if usage.warning:
            messages = await mgr.compact(messages, summarizer=llm_summarize)

    Thresholds:
        - warning at 75% — log warning, suggest compaction
        - critical at 90% — force compaction
    """

    WARNING_RATIO = 0.75
    CRITICAL_RATIO = 0.90

    # How many recent messages to keep intact during compaction.
    # These are never summarized — only older messages are compressed.
    KEEP_RECENT = 6

    # Minimum messages required before compaction triggers.
    # No point compacting a 4-message conversation.
    MIN_MESSAGES_FOR_COMPACTION = 8

    def __init__(
        self,
        model: str = "",
        max_context_tokens: Optional[int] = None,
        warning_ratio: float = WARNING_RATIO,
        critical_ratio: float = CRITICAL_RATIO,
    ):
        self.max_context_tokens = max_context_tokens or get_context_window(model)
        self.warning_ratio = warning_ratio
        self.critical_ratio = critical_ratio
        self._compaction_count = 0

    def check_usage(self, messages: List[Dict[str, Any]]) -> ContextUsage:
        """Estimate token usage and return a usage snapshot."""
        used = estimate_messages_tokens(messages)
        ratio = used / self.max_context_tokens if self.max_context_tokens > 0 else 0.0
        return ContextUsage(
            used_tokens=used,
            max_tokens=self.max_context_tokens,
            ratio=ratio,
            warning=ratio >= self.warning_ratio,
            critical=ratio >= self.critical_ratio,
        )

    async def compact_if_needed(
        self,
        messages: List[Dict[str, Any]],
        *,
        summarizer: Callable[[str], Awaitable[str]],
        force: bool = False,
    ) -> List[Dict[str, Any]]:
        """Compact messages if context usage exceeds the warning threshold.

        Args:
            messages: Current message list.
            summarizer: Async function that takes a block of text and returns
                a concise summary. Typically wraps an LLM call.
            force: Force compaction even if below threshold.

        Returns:
            Potentially shortened message list. The first message (system
            prompt) and last KEEP_RECENT messages are always preserved.
        """
        if len(messages) < self.MIN_MESSAGES_FOR_COMPACTION:
            return messages

        usage = self.check_usage(messages)
        if not force and not usage.warning:
            return messages

        logger.info(
            "[CONTEXT] Compaction triggered: used=%d/%d tokens (%.0f%%), compaction_count=%d",
            usage.used_tokens,
            usage.max_tokens,
            usage.ratio * 100,
            self._compaction_count,
        )

        # Partition: [system] + [compactable...] + [recent...]
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        start_idx = 1 if system_msg else 0
        keep_count = min(self.KEEP_RECENT, len(messages) - start_idx)
        split_point = len(messages) - keep_count

        if split_point <= start_idx:
            logger.info("[CONTEXT] Not enough compactable messages, skipping")
            return messages

        compactable = messages[start_idx:split_point]
        recent = messages[split_point:]

        # Build text block for summarization
        text_block = self._messages_to_text(compactable)
        if not text_block.strip():
            return messages

        try:
            summary = await summarizer(text_block)
        except Exception as exc:
            logger.warning("[CONTEXT] Summarization failed: %s; skipping compaction", exc)
            return messages

        if not summary or not summary.strip():
            logger.warning("[CONTEXT] Summarizer returned empty result; skipping compaction")
            return messages

        self._compaction_count += 1

        summary_msg: Dict[str, Any] = {
            "role": "system",
            "content": (
                f"[Context Summary — compacted from {len(compactable)} earlier messages]\n\n"
                f"{summary.strip()}"
            ),
        }

        result = []
        if system_msg:
            result.append(system_msg)
        result.append(summary_msg)
        result.extend(recent)

        new_usage = self.check_usage(result)
        logger.info(
            "[CONTEXT] Compaction done: %d→%d messages, %d→%d tokens (%.0f%%→%.0f%%)",
            len(messages),
            len(result),
            usage.used_tokens,
            new_usage.used_tokens,
            usage.ratio * 100,
            new_usage.ratio * 100,
        )

        return result

    @staticmethod
    def _messages_to_text(messages: List[Dict[str, Any]]) -> str:
        """Convert messages to a plain text block for summarization."""
        lines: List[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text from multi-part content
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        parts.append(part.get("text") or part.get("content") or "")
                    elif isinstance(part, str):
                        parts.append(part)
                content = "\n".join(p for p in parts if p)
            if not content:
                continue
            # Truncate very long messages to keep summarization prompt manageable
            if len(content) > 2000:
                content = content[:1800] + "\n...[truncated]"
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Summarization prompt builder
# ---------------------------------------------------------------------------

def build_summarization_prompt(conversation_text: str) -> str:
    """Build a prompt for the LLM to summarize a conversation block.

    The summary must preserve:
    - Key decisions and conclusions
    - File paths and artifact locations
    - Tool results and their outcomes
    - User preferences and constraints
    """
    return (
        "Summarize the following conversation concisely, preserving:\n"
        "- Key decisions and conclusions\n"
        "- Important file paths and artifact locations\n"
        "- Tool execution results (what worked, what failed)\n"
        "- User preferences and constraints\n"
        "- Active task context (plan IDs, task IDs)\n\n"
        "Omit: greetings, filler, thinking-out-loud, repeated content.\n"
        "Keep it under 500 words. Use bullet points.\n\n"
        "---\n"
        f"{conversation_text}\n"
        "---\n\n"
        "Concise summary:"
    )
