"""
Memory hooks service.

Provides convenience hooks to persist important runtime signals into memory.
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime

from ...models_memory import (
    ImportanceLevel,
    MemoryType,
    SaveMemoryRequest,
)
from .memory_service import get_memory_service

logger = logging.getLogger(__name__)


class MemoryHooks:
    """Hook callbacks for saving structured memory entries."""

    def __init__(self):
        self.memory_service = get_memory_service()
        self.enabled = True
        self.stats = {
            "total_saved": 0,
            "by_type": {},
            "last_save_time": None,
        }

    async def on_task_complete(
        self,
        task_id: int,
        task_name: str,
        task_content: str,
        task_result: Optional[str] = None,
        success: bool = True,
    ) -> Optional[str]:
        """
        Persist task execution memory after task completion.

        Args:
            task_id: Task ID.
            task_name: Task name.
            task_content: Task input/instruction content.
            task_result: Optional execution output.
            success: Whether task execution succeeded.

        Returns:
            Saved memory ID, or `None` when save fails.
        """
        if not self.enabled:
            return None

        try:
            content_parts = [f"Task: {task_name}"]

            if task_content:
                content_parts.append(f"Input: {task_content}")

            if task_result:
                status = "succeeded" if success else "failed"
                content_parts.append(f"Result status: {status}")
                content_parts.append(f"Output: {task_result}")

            content = "\n".join(content_parts)

            importance = ImportanceLevel.HIGH if success else ImportanceLevel.CRITICAL

            tags = ["task_execution"]
            if success:
                tags.append("success")
            else:
                tags.append("failed")

            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.TASK_OUTPUT,
                importance=importance,
                tags=tags,
                related_task_id=task_id,
            )

            response = await self.memory_service.save_memory(request)

            self._update_stats(MemoryType.TASK_OUTPUT)

            logger.info(f"Saved task memory for task {task_id}: {response.memory_id}")
            return response.memory_id

        except Exception as e:
            logger.error(f"Failed to save task memory: {e}")
            return None

    async def on_conversation_important(
        self,
        content: str,
        role: str = "user",
        session_id: Optional[str] = None,
        importance: ImportanceLevel = ImportanceLevel.MEDIUM,
    ) -> Optional[str]:
        """
        Persist important conversation content to memory.

        Args:
            content: Message content.
            role: Message role (`user`/`assistant`).
            session_id: Optional session ID.
            importance: Desired memory importance level.

        Returns:
            Saved memory ID, or `None`.
        """
        if not self.enabled:
            return None

        try:
            memory_content = f"[{role}] {content}"

            tags = ["conversation", role]
            if session_id:
                tags.append(f"session:{session_id}")

            request = SaveMemoryRequest(
                content=memory_content,
                memory_type=MemoryType.CONVERSATION,
                importance=importance,
                tags=tags,
            )

            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.CONVERSATION)

            logger.info(f"Saved conversation memory: {response.memory_id}")
            return response.memory_id

        except Exception as e:
            logger.error(f"Failed to save conversation memory: {e}")
            return None

    async def on_error_occurred(
        self,
        error_message: str,
        error_type: str,
        context: Optional[Dict[str, Any]] = None,
        task_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        Persist runtime error information to memory.

        Args:
            error_message: Error message text.
            error_type: Error type/category.
            context: Optional structured context.
            task_id: Optional related task ID.

        Returns:
            Saved memory ID, or `None`.
        """
        if not self.enabled:
            return None

        try:
            content_parts = [
                f"Error type: {error_type}",
                f"Error: {error_message}",
            ]

            if context:
                content_parts.append(f"Context: {context}")

            content = "\n".join(content_parts)

            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.EXPERIENCE,
                importance=ImportanceLevel.HIGH,
                tags=["error", error_type],
                related_task_id=task_id,
            )

            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.EXPERIENCE)

            logger.info(f"Saved error memory: {response.memory_id}")
            return response.memory_id

        except Exception as e:
            logger.error(f"Failed to save error memory: {e}")
            return None

    async def on_knowledge_learned(
        self,
        knowledge: str,
        source: str,
        tags: Optional[list] = None,
    ) -> Optional[str]:
        """
        Persist newly learned knowledge to memory.

        Args:
            knowledge: Learned knowledge content.
            source: Knowledge source identifier.
            tags: Optional additional tags.

        Returns:
            Saved memory ID, or `None`.
        """
        if not self.enabled:
            return None

        try:
            content = f"Source: {source}\nKnowledge: {knowledge}"

            request_tags = tags or []
            request_tags.extend(["knowledge", source])

            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.KNOWLEDGE,
                importance=ImportanceLevel.MEDIUM,
                tags=request_tags,
            )

            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.KNOWLEDGE)

            logger.info(f"Saved knowledge memory: {response.memory_id}")
            return response.memory_id

        except Exception as e:
            logger.error(f"Failed to save knowledge memory: {e}")
            return None

    async def on_evaluation_complete(
        self,
        task_id: int,
        evaluation_result: Dict[str, Any],
        score: float,
    ) -> Optional[str]:
        """
        Persist task evaluation result to memory.

        Args:
            task_id: Task ID.
            evaluation_result: Evaluation payload.
            score: Numeric evaluation score.

        Returns:
            Saved memory ID, or `None`.
        """
        if not self.enabled:
            return None

        try:
            content = f"Task evaluation\nScore: {score}\nResult: {evaluation_result}"

            if score >= 0.8:
                importance = ImportanceLevel.HIGH
            elif score >= 0.5:
                importance = ImportanceLevel.MEDIUM
            else:
                importance = ImportanceLevel.LOW

            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.EVALUATION,
                importance=importance,
                tags=["evaluation", f"score:{score:.2f}"],
                related_task_id=task_id,
            )

            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.EVALUATION)

            logger.info(f"Saved evaluation memory: {response.memory_id}")
            return response.memory_id

        except Exception as e:
            logger.error(f"Failed to save evaluation memory: {e}")
            return None

    def _update_stats(self, memory_type: MemoryType):
        """Update in-memory save statistics."""
        self.stats["total_saved"] += 1
        self.stats["last_save_time"] = datetime.now()

        type_key = memory_type.value
        if type_key not in self.stats["by_type"]:
            self.stats["by_type"][type_key] = 0
        self.stats["by_type"][type_key] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Return current memory hook statistics."""
        return {
            "enabled": self.enabled,
            "total_saved": self.stats["total_saved"],
            "by_type": self.stats["by_type"],
            "last_save_time": self.stats["last_save_time"].isoformat() if self.stats["last_save_time"] else None,
        }

    def enable(self):
        """Enable memory hooks."""
        self.enabled = True
        logger.info("Memory hooks enabled")

    def disable(self):
        """Disable memory hooks."""
        self.enabled = False
        logger.info("Memory hooks disabled")


_memory_hooks: Optional[MemoryHooks] = None


def get_memory_hooks() -> MemoryHooks:
    """Get singleton memory hooks instance."""
    global _memory_hooks
    if _memory_hooks is None:
        _memory_hooks = MemoryHooks()
    return _memory_hooks
