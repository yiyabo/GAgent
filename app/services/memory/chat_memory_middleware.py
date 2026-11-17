"""
Chat memory middleware.

After a user turn, asks the LLM to extract 0-3 atomic, durable facts /
preferences / constraints / paths and saves each as its own memory (deduped
per owner by ``save_memory``). Assistant replies and trivial acknowledgements
are never dumped verbatim into long-term memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ...llm import get_default_client
from ...models_memory import ImportanceLevel, MemoryType
from .memory_hooks import get_memory_hooks

logger = logging.getLogger(__name__)

MAX_EXTRACTED_ENTRIES = 3
MIN_EXTRACTABLE_CHARS = 6
MAX_ENTRY_CHARS = 500

_TRIVIAL_ACKS = frozenset(
    {
        "ok", "okay", "k", "yes", "no", "y", "n", "sure", "thanks", "thank you", "thx",
        "go", "go on", "continue", "next", "done", "got it", "fine", "great", "cool",
        "好", "好的", "好滴", "行", "可以", "嗯", "嗯嗯", "对", "是", "是的", "不是",
        "继续", "继续吧", "接着", "下一步", "谢谢", "收到", "明白", "了解", "没问题", "开始",
    }
)

_IMPORTANCE_MAP = {
    "low": ImportanceLevel.LOW,
    "medium": ImportanceLevel.MEDIUM,
    "high": ImportanceLevel.HIGH,
    "critical": ImportanceLevel.CRITICAL,
}

_MEMORY_TYPE_MAP = {
    "knowledge": MemoryType.KNOWLEDGE,
    "experience": MemoryType.EXPERIENCE,
    "conversation": MemoryType.CONVERSATION,
    "context": MemoryType.CONTEXT,
}

_EXTRACTION_PROMPT = """You maintain a user's long-term memory. Read the user's chat message and extract \
0 to {max_entries} ATOMIC, DURABLE items worth remembering across future conversations:
- stable user preferences (language, style, tools, formats)
- facts about the user, their project, data, or environment
- explicit constraints or requirements
- important file paths, dataset names, identifiers, decisions

Do NOT extract: greetings, acknowledgements, one-off questions, transient task chatter, \
or anything only meaningful inside this single turn. Rewrite each item as one standalone \
sentence in the message's language. Never copy the whole message.

User message:
<<<
{content}
>>>

Return ONLY JSON:
{{"should_save": true/false, "entries": [{{"content": "...", "memory_type": \
"knowledge|experience|context|conversation", "importance": "low|medium|high|critical", \
"keywords": ["..."], "tags": ["preference|fact|constraint|path|decision"]}}]}}"""


def is_trivial_message(content: str) -> bool:
    """True for short acknowledgements that must never reach the LLM."""
    text = re.sub(r"[\s\u3000]+", " ", str(content or "")).strip()
    if len(text) < MIN_EXTRACTABLE_CHARS and not re.search(r"[/\\.]", text):
        stripped = re.sub(r"[^\w\u4e00-\u9fff ]+", "", text).strip().casefold()
        if len(stripped) <= 3 or stripped in _TRIVIAL_ACKS:
            return True
    stripped = re.sub(r"[^\w\u4e00-\u9fff ]+", "", text).strip().casefold()
    return not stripped or stripped in _TRIVIAL_ACKS


def _parse_json_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        text = str(response.get("content") or "")
    else:
        text = str(response or "")
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("extraction payload is not a JSON object")
    return parsed


def _str_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text[:60])
    return out[:limit]


def normalize_extraction(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate an LLM extraction payload into at most 3 clean entries."""
    if not payload.get("should_save"):
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    cleaned: list[dict[str, Any]] = []
    seen = set()
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content or content.casefold() in seen:
            continue
        seen.add(content.casefold())
        cleaned.append(
            {
                "content": content[:MAX_ENTRY_CHARS],
                "memory_type": _MEMORY_TYPE_MAP.get(
                    str(raw.get("memory_type") or "").lower(), MemoryType.KNOWLEDGE
                ),
                "importance": _IMPORTANCE_MAP.get(
                    str(raw.get("importance") or "").lower(), ImportanceLevel.MEDIUM
                ),
                "keywords": _str_list(raw.get("keywords")),
                "tags": _str_list(raw.get("tags")),
            }
        )
        if len(cleaned) >= MAX_EXTRACTED_ENTRIES:
            break
    return cleaned


class ChatMemoryMiddleware:
    """LLM-driven post-turn durable-fact extraction for chat messages."""

    def __init__(self):
        self.hooks = get_memory_hooks()
        self.llm_client = get_default_client()
        self.enabled = True

    async def process_message(
        self,
        content: str,
        role: str = "user",
        session_id: str | None = None,
        force_save: bool = False,
        model_provider: dict | None = None,
        owner_id: str | None = None,
    ) -> str | None:
        """Extract durable memories from one chat message.

        Returns the first saved (or deduped) memory id, or ``None``.
        """
        if not self.enabled and not force_save:
            return None
        if not owner_id:
            logger.debug("chat memory skipped: no owner_id")
            return None
        if role == "assistant" and not force_save:
            return None
        if not force_save and is_trivial_message(content):
            return None

        if force_save:
            entries = [
                {
                    "content": str(content).strip()[: MAX_ENTRY_CHARS * 4],
                    "memory_type": MemoryType.CONVERSATION,
                    "importance": ImportanceLevel.HIGH,
                    "keywords": [],
                    "tags": [],
                }
            ]
        else:
            entries = await self._extract_entries(content, model_provider=model_provider)
        if not entries:
            return None
        return await self._save_entries(entries, role, session_id, owner_id)

    async def _save_entries(
        self,
        entries: list[dict[str, Any]],
        role: str,
        session_id: str | None,
        owner_id: str,
    ) -> str | None:
        from ...models_memory import SaveMemoryRequest
        from .memory_service import get_memory_service

        memory_service = get_memory_service()
        first_id: str | None = None
        for entry in entries:
            tags = ["conversation", role, *entry["tags"]]
            if session_id:
                tags.append(f"session:{session_id}")
            request = SaveMemoryRequest(
                content=entry["content"],
                memory_type=entry["memory_type"],
                importance=entry["importance"],
                keywords=entry["keywords"],
                tags=list(dict.fromkeys(tags)),
                owner_id=owner_id,
            )
            try:
                response = await memory_service.save_memory(request)
            except Exception as exc:
                logger.error("Failed to save memory: %s", exc)
                continue
            if response.memory_id and first_id is None:
                first_id = response.memory_id
        return first_id

    def _resolve_client(self, model_provider: dict | None) -> Any:
        if model_provider and model_provider.get("base_url") and model_provider.get("api_key"):
            from ...llm import LLMClient

            return LLMClient(
                provider=model_provider.get("type") or "openai",
                url=model_provider["base_url"].rstrip("/") + "/v1/chat/completions",
                api_key=model_provider["api_key"],
                model=model_provider.get("model") or "qwen3.8-flash",
            )
        return self.llm_client

    async def _extract_entries(
        self,
        content: str,
        model_provider: dict | None = None,
        max_retries: int = 2,
    ) -> list[dict[str, Any]]:
        prompt = _EXTRACTION_PROMPT.format(
            max_entries=MAX_EXTRACTED_ENTRIES, content=str(content)[:6000]
        )
        client = self._resolve_client(model_provider)
        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(client.chat, prompt, temperature=0.2)
                return normalize_extraction(_parse_json_payload(response))
            except Exception as exc:
                logger.warning(
                    "Memory extraction failed (attempt %d/%d): %s", attempt + 1, max_retries, exc
                )
        return []

    async def process_assistant_response(
        self,
        content: str,
        session_id: str | None = None,
        owner_id: str | None = None,
    ) -> str | None:
        """Assistant replies are not dumped into memory (kept for API compatibility)."""
        return await self.process_message(
            content=content,
            role="assistant",
            session_id=session_id,
            force_save=False,
            owner_id=owner_id,
        )

    def enable(self):
        """Enable chat memory middleware."""
        self.enabled = True
        logger.info("Chat memory middleware enabled")

    def disable(self):
        """Disable chat memory middleware."""
        self.enabled = False
        logger.info("Chat memory middleware disabled")


_chat_memory_middleware: ChatMemoryMiddleware | None = None


def get_chat_memory_middleware() -> ChatMemoryMiddleware:
    """Get singleton chat memory middleware instance."""
    global _chat_memory_middleware
    if _chat_memory_middleware is None:
        _chat_memory_middleware = ChatMemoryMiddleware()
    return _chat_memory_middleware
