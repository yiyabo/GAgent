"""
Chat memory middleware.

Uses LLM signals to decide whether chat content should be saved to long-term memory.
"""

import json
import logging
from typing import Optional

from ...llm import get_default_client
from ...models_memory import ImportanceLevel, MemoryType
from .memory_hooks import get_memory_hooks

logger = logging.getLogger(__name__)


class ChatMemoryMiddleware:
    """LLM-driven memory save decision middleware for chat messages."""

    def __init__(self):
        self.hooks = get_memory_hooks()
        self.llm_client = get_default_client()
        self.enabled = True

    async def process_message(
        self,
        content: str,
        role: str = "user",
        session_id: Optional[str] = None,
        force_save: bool = False,
    ) -> Optional[str]:
        """
        Process one chat message and conditionally persist it to memory.

        Args:
            content: Message content.
            role: Message role (`user`/`assistant`).
            session_id: Optional session ID.
            force_save: Force save without LLM decision.

        Returns:
            Saved memory ID, or `None` when not saved.
        """
        if not self.enabled and not force_save:
            return None

        should_save, importance, memory_type = await self._should_save_message(
            content, role, force_save
        )

        if not should_save:
            return None

        try:
            from ...models_memory import SaveMemoryRequest
            from .memory_service import get_memory_service

            memory_service = get_memory_service()

            memory_content = f"[{role}] {content}"

            tags = ["conversation", role]
            if session_id:
                tags.append(f"session:{session_id}")

            request = SaveMemoryRequest(
                content=memory_content,
                memory_type=memory_type,
                importance=importance,
                tags=tags,
            )

            response = await memory_service.save_memory(request)
            memory_id = response.memory_id

            if memory_id:
                logger.info(
                    f"Saved memory ({memory_type.value}/{importance.value}): {memory_id[:8]}..."
                )

            return memory_id

        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            return None

    async def _should_save_message(
        self,
        content: str,
        role: str,
        force_save: bool = False,
    ) -> tuple[bool, ImportanceLevel, Optional[MemoryType]]:
        """
        Ask LLM whether a message should be saved as memory.

        Returns:
            Tuple of `(should_save, importance, memory_type)`.
        """
        if force_save:
            return True, ImportanceLevel.HIGH, MemoryType.CONVERSATION

        if len(content) < 10:
            return False, ImportanceLevel.LOW, None

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"LLM retry {attempt + 1}/{max_retries}")

                prompt = f"""You are an intelligent memory system analyzer. Analyze the following conversation message and determine if it's worth saving as long-term memory.

Role: {role}
Message Content: {content}

Analyze from the following dimensions:
1. Does it contain important knowledge, experience, or insights?
2. Is it a critical question, error, or solution?
3. Does it have reference value for future conversations or tasks?
4. Does it contain configuration, settings, or important decisions?

Return your judgment in JSON format:
{{
    "should_save": true/false,  // Whether to save
    "importance": "low/medium/high/critical",  // Importance level
    "memory_type": "knowledge/experience/conversation/context",  // Memory type
    "reason": "Brief explanation"
}}

Only return JSON, no other content."""

                import asyncio
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.llm_client.chat(prompt, temperature=0.3)
                )

                response_text = response.strip() if isinstance(response, str) else response.get("content", "").strip()

                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0].strip()

                result = json.loads(response_text)

                should_save = result.get("should_save", False)
                importance_str = result.get("importance", "low").lower()
                memory_type_str = result.get("memory_type", "conversation").lower()
                reason = result.get("reason", "")

                importance_map = {
                    "low": ImportanceLevel.LOW,
                    "medium": ImportanceLevel.MEDIUM,
                    "high": ImportanceLevel.HIGH,
                    "critical": ImportanceLevel.CRITICAL,
                }
                importance = importance_map.get(importance_str, ImportanceLevel.MEDIUM)

                memory_type_map = {
                    "knowledge": MemoryType.KNOWLEDGE,
                    "experience": MemoryType.EXPERIENCE,
                    "conversation": MemoryType.CONVERSATION,
                    "context": MemoryType.CONTEXT,
                }
                memory_type = memory_type_map.get(memory_type_str, MemoryType.CONVERSATION)

                if should_save:
                    logger.info(f"LLM decision: save ({importance_str}) - {reason}")
                else:
                    logger.debug(f"LLM decision: do not save - {reason}")

                return should_save, importance, memory_type

            except Exception as e:
                last_error = e
                logger.warning(f"LLM analysis failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    continue
                break

        logger.error(f"LLM analysis failed after {max_retries} attempts: {last_error}")
        return False, ImportanceLevel.LOW, None

    async def process_assistant_response(
        self,
        content: str,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Process assistant response and conditionally save to memory.

        Args:
            content: Assistant response content.
            session_id: Optional session ID.

        Returns:
            Saved memory ID, or `None`.
        """
        return await self.process_message(
            content=content,
            role="assistant",
            session_id=session_id,
            force_save=False
        )

    def enable(self):
        """Enable chat memory middleware."""
        self.enabled = True
        logger.info("Chat memory middleware enabled")

    def disable(self):
        """Disable chat memory middleware."""
        self.enabled = False
        logger.info("Chat memory middleware disabled")


_chat_memory_middleware: Optional[ChatMemoryMiddleware] = None


def get_chat_memory_middleware() -> ChatMemoryMiddleware:
    """Get singleton chat memory middleware instance."""
    global _chat_memory_middleware
    if _chat_memory_middleware is None:
        _chat_memory_middleware = ChatMemoryMiddleware()
    return _chat_memory_middleware
