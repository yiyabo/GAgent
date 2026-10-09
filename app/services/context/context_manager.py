"""Token-aware context window manager with proactive compaction.

Tracks token usage across the message list and triggers LLM-based
summarization when approaching the model's context window limit.

"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    """Positive-int env knob; malformed or blank values fall back to the default."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


# Framing for the compacted-history message. Wording follows the Hermes
# compaction handoff: the summary is reference material, only the newest user
# message is the active task, and topic overlap does not license resuming old
# work — two shipped regressions came from a weaker framing on both counts.
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the "
    "summary below. This is a handoff from a previous context window: treat it as "
    "background reference, NOT as active instructions, and do not re-run work it "
    "describes. Respond ONLY to the latest user message that appears AFTER this "
    "summary — that message is the single source of truth for what to do now. "
    "Topic overlap with the summary does NOT mean you should resume its task: the "
    "latest user message WINS, and stale items from the summary must be discarded "
    "unless that message explicitly asks for them. Your tools remain fully active "
    "for the current task — keep calling them normally instead of only describing "
    "what you would do."
)

_IMAGE_PART_TYPES = frozenset({"image", "image_url", "input_image"})

# Dedupe is lossless (the newest copy of identical content is kept), so it uses
# a low fixed floor like the Hermes pass-1; only the demotion pass tracks the
# CONTEXT_PRUNE_MIN_CHARS knob.
_DEDUPE_MIN_CHARS = 200


def _is_image_part(part: Any) -> bool:
    """True for a multimodal content part carrying an image payload."""
    if not isinstance(part, dict):
        return False
    if str(part.get("type") or "").strip().lower() in _IMAGE_PART_TYPES:
        return True
    return isinstance(part.get("image_url"), (str, dict))


# Image payloads can be multi-megabyte base64; estimate on a bounded sample and
# scale, so reclaim accounting never pays a full tokenizer pass over a blob.
_IMAGE_ESTIMATE_SAMPLE_CHARS = 40_000


def _estimate_retired_image_tokens(text: str) -> int:
    if not text:
        return 0
    sample = text[:_IMAGE_ESTIMATE_SAMPLE_CHARS]
    tokens = estimate_tokens(sample)
    if len(text) > len(sample):
        tokens = int(tokens * len(text) / len(sample))
    return tokens

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
        encoded_tokens = len(_get_encoder().encode(text))
        cjk_chars = _count_cjk_chars(text)
        if cjk_chars:
            return max(encoded_tokens, cjk_chars)
        return encoded_tokens
    except Exception:
        cjk_chars = _count_cjk_chars(text)
        if cjk_chars:
            return max(1, cjk_chars + (len(text) - cjk_chars) // 4)
        return max(1, len(text) // 4)


def _count_cjk_chars(text: str) -> int:
    """Count CJK codepoints for conservative token estimation.

    Some tokenizer proxies undercount compact Chinese/Japanese/Korean text for
    the model families used by this service.  A character floor prevents the
    context manager from suppressing compaction on CJK-heavy conversations.
    """
    ranges = (
        (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF),
        (0xF900, 0xFAFF),
        (0x3040, 0x30FF),
        (0xAC00, 0xD7AF),
        (0x20000, 0x2A6DF),
        (0x2A700, 0x2B73F),
        (0x2B740, 0x2B81F),
        (0x2B820, 0x2CEAF),
    )
    count = 0
    for char in text:
        codepoint = ord(char)
        if any(start <= codepoint <= end for start, end in ranges):
            count += 1
    return count


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

# Known context window sizes (in tokens).
_MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "qwen3.7-max": 1000000,
    "qwen3.6-plus": 1000000,
    "qwen-long": 1000000,
}

# Conservative fallback for unknown or empty model names.  Using 128K
# instead of 1M avoids suppressing compaction on smaller-context backends
# that would hit their real API limit long before the manager warns.
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
    breakdown: Dict[str, int] = field(default_factory=dict)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class ContextWindowManager:
    """Manages context window budget with proactive compaction.

    Usage::

        mgr = ContextWindowManager(model="qwen3.7-max")
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

    # Minimum recent messages to keep intact during compaction. A tool-call
    # batch crossing this boundary is retained in full, including all results.
    KEEP_RECENT = 6

    # Minimum messages required before compaction triggers.
    # No point compacting a 4-message conversation.
    MIN_MESSAGES_FOR_COMPACTION = 8

    # --- Deterministic pre-compaction reclaim (no LLM) ---
    # Tool/call bodies at or below this many characters are left alone: too
    # small for a rewrite to pay for the prompt-cache prefix it breaks.
    PRUNE_MIN_CHARS = 2000
    # A commit rewrites earlier messages and therefore breaks the prompt cache.
    # Require a reclaim batch at least this large, or a full regrowth runway
    # since the last commit, so compaction episodes stay episodic.
    MIN_RECLAIM_TOKENS = 4096
    # Newest image-bearing parts kept live; older ones retire to a text note.
    KEEP_TOOL_IMAGES = 3

    def __init__(
        self,
        model: str = "",
        max_context_tokens: Optional[int] = None,
        warning_ratio: float = WARNING_RATIO,
        critical_ratio: float = CRITICAL_RATIO,
        budget_tokens: Optional[int] = None,
    ):
        self.max_context_tokens = max_context_tokens or get_context_window(model)
        self.warning_ratio = warning_ratio
        self.critical_ratio = critical_ratio
        # Working-set budget (tokens): compaction warning fires at
        # min(model_window * warning_ratio, budget).  The model-window ratio
        # alone only guards against hitting the provider limit — with 1M-token
        # windows that means a 30-iteration run can reach 100K input tokens
        # per call without ever compacting.  The budget makes compaction a
        # cost/latency control instead of just an overflow guard.
        try:
            budget = int(budget_tokens) if budget_tokens is not None else None
        except (TypeError, ValueError):
            budget = None
        self.budget_tokens = budget if budget and budget > 0 else None
        self._compaction_count = 0
        # Deterministic-reclaim knobs. Defaults are class constants; env
        # overrides follow the deep_think env-knob pattern.
        self.prune_min_chars = _env_int(
            "CONTEXT_PRUNE_MIN_CHARS", self.PRUNE_MIN_CHARS
        )
        self.min_reclaim_tokens = _env_int(
            "CONTEXT_COMPACTION_MIN_RECLAIM_TOKENS", self.MIN_RECLAIM_TOKENS
        )
        # Post-commit usage; the next cache-breaking commit needs the context to
        # grow back by at least ``min_reclaim_tokens`` from here.
        self._last_commit_used_tokens: Optional[int] = None

    def check_usage(self, messages: List[Dict[str, Any]], *, tool_schemas=None, output_reserve_tokens=0,component_texts=None) -> ContextUsage:
        """Estimate token usage and return a usage snapshot."""
        from .request_budget import breakdown
        parts=breakdown(messages,tool_schemas,output_reserve_tokens,component_texts)
        used = sum(parts.values())
        ratio = used / self.max_context_tokens if self.max_context_tokens > 0 else 0.0
        warning_line = self.warning_ratio * self.max_context_tokens
        if self.budget_tokens:
            warning_line = min(warning_line, float(self.budget_tokens))
        return ContextUsage(
            used_tokens=used,
            max_tokens=self.max_context_tokens,
            ratio=ratio,
            warning=used >= warning_line,
            critical=ratio >= self.critical_ratio,
            breakdown=parts,
        )

    async def compact_if_needed(
        self,
        messages: List[Dict[str, Any]],
        *,
        summarizer: Callable[[str], Awaitable[str]],
        force: bool = False,
        tool_schemas=None, output_reserve_tokens=0, anchors=None,component_texts=None,
    ) -> List[Dict[str, Any]]:
        """Compact messages if context usage exceeds the warning threshold.

        Args:
            messages: Current message list.
            summarizer: Async function that takes a block of text and returns
                a concise summary. Typically wraps an LLM call.
            force: Force compaction even if below threshold.

        Returns:
            Potentially shortened message list. The first message (system
            prompt) and at least the last KEEP_RECENT messages are preserved.
            The boundary never splits a tool call from its retained results.

        Order of work, cheapest first: deterministic reclaim (dedupe identical
        tool results, demote oversized tool bodies, retire stale images) →
        return the reclaimed list if that alone clears the threshold → only then
        LLM summarization. A deterministic commit is skipped when it reclaims
        less than ``min_reclaim_tokens`` and the context has not regrown since
        the last commit — every commit breaks the prompt-cache prefix — except
        under force, critical pressure, or overflow, which always proceed.
        """
        from .request_budget import ContextBudgetExceeded,anchor_text
        usage = self.check_usage(messages,tool_schemas=tool_schemas,output_reserve_tokens=output_reserve_tokens,component_texts=component_texts)
        from app.services.execution.llm_observation import emit
        emit("context_budget",estimated=True,parts=usage.breakdown,context_window=self.max_context_tokens)
        if len(messages) < self.MIN_MESSAGES_FOR_COMPACTION:
            if usage.used_tokens>self.max_context_tokens:raise ContextBudgetExceeded("context_budget_exceeded")
            return messages
        if not force and not usage.warning:
            return messages

        logger.info(
            "[CONTEXT] Compaction triggered: used=%d/%d tokens (%.0f%%), budget=%s, compaction_count=%d",
            usage.used_tokens,
            usage.max_tokens,
            usage.ratio * 100,
            self.budget_tokens,
            self._compaction_count,
        )

        # Partition: [system] + [compactable...] + [recent...]
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        start_idx = 1 if system_msg else 0
        keep_count = min(self.KEEP_RECENT, len(messages) - start_idx)
        split_point = len(messages) - keep_count
        split_point = self._tool_safe_split_point(messages, split_point, start_idx)

        if split_point <= start_idx:
            logger.info("[CONTEXT] Not enough compactable messages, skipping")
            return messages

        compactable = messages[start_idx:split_point]
        recent = messages[split_point:]

        # --- Deterministic reclaim before any LLM call -------------------
        # Retire stale image payloads anywhere in the retained window, then
        # dedupe/demote tool bodies inside the compactable segment. Content-only
        # edits: message count, tool_call ids and assistant tool_call arguments
        # are untouched, so call/result pairing survives.
        reclaimed_all, retired_images, retired_image_text = self._retire_old_images(messages)
        if retired_images:
            system_msg = reclaimed_all[0] if system_msg else None
            compactable = reclaimed_all[start_idx:split_point]
            recent = reclaimed_all[split_point:]
        compactable, deduped = self._dedupe_tool_results(compactable)
        compactable, demoted = self._demote_oversized_tool_bodies(compactable)
        reclaim_changes = retired_images + deduped + demoted

        if reclaim_changes:
            reclaimed_messages = ([system_msg] if system_msg else []) + compactable + recent
            reclaimed_usage = self.check_usage(
                reclaimed_messages, tool_schemas=tool_schemas,
                output_reserve_tokens=output_reserve_tokens, component_texts=component_texts,
            )
            # The shared estimator scores image parts as 0 tokens, so retired
            # image payloads are weighed by their serialized text size.
            reclaimed_tokens = max(
                0, usage.used_tokens - reclaimed_usage.used_tokens
            ) + _estimate_retired_image_tokens(retired_image_text)
            overflow = usage.used_tokens > self.max_context_tokens
            # Cache hysteresis: only a non-critical, non-forced, non-overflowing
            # compaction may be deferred. Overflow protection always wins.
            if not force and not usage.critical and not overflow:
                rearmed = (
                    self._last_commit_used_tokens is not None
                    and usage.used_tokens - self._last_commit_used_tokens >= self.min_reclaim_tokens
                )
                if reclaimed_tokens < self.min_reclaim_tokens and not rearmed:
                    logger.info(
                        "[CONTEXT] Compaction skipped by cache hysteresis: "
                        "reclaim=%d < min_reclaim=%d and not rearmed (last_commit=%s)",
                        reclaimed_tokens,
                        self.min_reclaim_tokens,
                        self._last_commit_used_tokens,
                    )
                    return messages
            logger.info(
                "[CONTEXT] Deterministic reclaim: %d change(s), %d→%d tokens",
                reclaim_changes,
                usage.used_tokens,
                reclaimed_usage.used_tokens,
            )
            if not force and not reclaimed_usage.warning:
                # The reclaim alone brought us back under the threshold: skip
                # the LLM summary (and the prompt-cache invalidation it costs).
                self._last_commit_used_tokens = reclaimed_usage.used_tokens
                logger.info(
                    "[CONTEXT] Reclaim sufficient; skipping LLM summary: %d→%d messages, %d→%d tokens",
                    len(messages),
                    len(reclaimed_messages),
                    usage.used_tokens,
                    reclaimed_usage.used_tokens,
                )
                return reclaimed_messages

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
            # Never a second system message: alternate against the tail's first
            # role so provider-side role alternation holds.
            "role": self._summary_role(recent),
            "content": (
                f"{SUMMARY_PREFIX}\n"
                f"[Context Summary — compacted from {len(compactable)} earlier messages]\n\n"
                f"{summary.strip()}\n\n{anchor_text(anchors)}"
            ),
        }

        result = []
        if system_msg:
            result.append(system_msg)
        result.append(summary_msg)
        result.extend(recent)

        new_usage = self.check_usage(result,tool_schemas=tool_schemas,output_reserve_tokens=output_reserve_tokens,component_texts=component_texts)
        if (tool_schemas is not None or anchors is not None) and new_usage.used_tokens>=usage.used_tokens:
            if usage.used_tokens>self.max_context_tokens:raise ContextBudgetExceeded("nonshrinking_context_summary")
            return messages
        if (tool_schemas is not None or anchors is not None) and new_usage.used_tokens>self.max_context_tokens:raise ContextBudgetExceeded("context_budget_exceeded")
        self._last_commit_used_tokens = new_usage.used_tokens
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
    def _summary_role(recent: List[Dict[str, Any]]) -> str:
        """Role for the compaction summary message.

        Never a second ``system`` row: alternate against the first retained
        message so template-visible role alternation holds.
        """
        first_role = str(recent[0].get("role") or "") if recent else ""
        return "assistant" if first_role == "user" else "user"

    @classmethod
    def _tool_names_by_call_id(cls, messages: List[Dict[str, Any]]) -> Dict[str, str]:
        """Map ``tool_call_id`` → tool name from the assistant call rows."""
        names: Dict[str, str] = {}
        for message in messages:
            if message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict) or not call.get("id"):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                names[str(call["id"])] = str(
                    function.get("name") or call.get("name") or "tool"
                )
        return names

    def _retire_old_images(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int, str]:
        """Keep the newest ``KEEP_TOOL_IMAGES`` image parts; retire older ones.

        Image payloads are the largest re-sent blocks and cannot be reclaimed by
        any later pass, so stale frames retire to a text note. Content-only edit.
        Returns the messages, the retired count, and the retired payload text
        (the shared token estimator scores image parts as 0, so the reclaim
        accounting has to weigh them separately).
        """
        positions: List[tuple[int, int]] = []
        for index, message in enumerate(messages):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part_index, part in enumerate(content):
                if _is_image_part(part):
                    positions.append((index, part_index))
        if len(positions) <= self.KEEP_TOOL_IMAGES:
            return messages, 0, ""

        stale = set(positions[: len(positions) - self.KEEP_TOOL_IMAGES])
        result = list(messages)
        retired = 0
        retired_text: List[str] = []
        for index in sorted({position[0] for position in stale}):
            message = dict(result[index])
            parts: List[Any] = []
            for part_index, part in enumerate(message["content"]):
                if (index, part_index) in stale:
                    retired_text.append(
                        json.dumps(part, ensure_ascii=False, default=str)
                    )
                    parts.append(
                        {
                            "type": "text",
                            "text": "[older image omitted from context — superseded by a newer image]",
                        }
                    )
                    retired += 1
                else:
                    parts.append(part)
            message["content"] = parts
            result[index] = message
        return result, retired, "".join(retired_text)

    def _dedupe_tool_results(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int]:
        """Pass 1: keep only the newest copy of byte-identical tool results."""
        names = self._tool_names_by_call_id(messages)
        seen: set = set()
        result = list(messages)
        pruned = 0
        for index in range(len(result) - 1, -1, -1):
            message = result[index]
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str) or len(content) < _DEDUPE_MIN_CHARS:
                continue
            digest = hashlib.md5(content.encode("utf-8", "replace")).hexdigest()
            if digest in seen:
                tool_name = names.get(str(message.get("tool_call_id") or ""), "tool")
                result[index] = {
                    **message,
                    "content": (
                        f"[duplicate of latest {tool_name} result — identical content, omitted]"
                    ),
                }
                pruned += 1
            else:
                seen.add(digest)
        return result, pruned

    def _demote_oversized_tool_bodies(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int]:
        """Pass 2: bound tool bodies over ``prune_min_chars`` to a head/tail stub.

        Only ``content`` is rewritten; the ``tool_call_id`` pairing and every
        assistant ``tool_calls`` argument stay byte-exact.
        """
        result = list(messages)
        pruned = 0
        for index, message in enumerate(result):
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str) or len(content) <= self.prune_min_chars:
                continue
            result[index] = {
                **message,
                "content": self._demoted_tool_body(content, self.prune_min_chars),
            }
            pruned += 1
        return result, pruned

    @staticmethod
    def _demoted_tool_body(content: str, limit: int) -> str:
        """Head/tail stub within ``limit`` characters, naming the omission."""
        marker = (
            f"\n…[tool output demoted before compaction: "
            f"{len(content) - limit} of {len(content)} chars omitted]…\n"
        )
        keep = max(0, limit - len(marker))
        head = int(keep * 0.4)
        tail = keep - head
        return content[:head] + marker + (content[-tail:] if tail else "")

    @staticmethod
    def _tool_safe_split_point(
        messages: List[Dict[str, Any]], split_point: int, start_idx: int,
    ) -> int:
        """Keep every call needed by a retained tool result on the same side.

        Moving backwards may expose results from another batch, so repeat
        until the boundary is stable. If the whole history is one batch,
        retaining it is preferable to constructing an invalid conversation.
        """
        call_positions: Dict[str, int] = {}
        result_call_positions: Dict[int, int] = {}
        for index, message in enumerate(messages):
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict) and call.get("id"):
                        call_positions[str(call["id"])] = index
            elif message.get("role") == "tool":
                # Some providers reuse call IDs in later rounds. Capture the
                # nearest preceding call now, before a later batch replaces it.
                call_index = call_positions.get(str(message.get("tool_call_id") or ""))
                if call_index is not None:
                    result_call_positions[index] = call_index
        while split_point > start_idx:
            earlier = split_point
            for result_index, call_index in result_call_positions.items():
                if result_index >= split_point and start_idx <= call_index < earlier:
                    earlier = call_index
            if earlier == split_point:
                break
            split_point = earlier
        return split_point

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
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str) if content else ""
            call_parts: List[str] = []
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                call_parts.append("Tool calls: " + json.dumps(tool_calls, ensure_ascii=False, default=str))
            if msg.get("function_call"):
                call_parts.append("Function call: " + json.dumps(msg["function_call"], ensure_ascii=False, default=str))
            if call_parts:
                content = "\n".join([*call_parts, content]).strip()
            if not content:
                continue
            # Truncate very long messages to keep summarization prompt manageable
            if len(content) > 2000:
                content = content[:1800] + "\n...[truncated]"
            result_id = msg.get("tool_call_id")
            label = f"{role} result of {result_id}" if result_id else role
            lines.append(f"[{label}]: {content}")
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
