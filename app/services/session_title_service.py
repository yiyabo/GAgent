"""Utilities for generating readable chat session titles."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from ..database import get_db

logger = logging.getLogger(__name__)


@dataclass
class SessionTitleResult:
    """Represents an auto-title generation attempt."""

    session_id: str
    title: str
    source: str
    previous_title: Optional[str]
    updated: bool
    skipped_reason: Optional[str] = None


class SessionTitleError(Exception):
    """Base error for session title operations."""


class SessionNotFoundError(SessionTitleError):
    """Raised when the requested session does not exist."""


class SessionTitleService:
    """Generate concise titles for chat sessions."""

    DEFAULT_LIMIT = 50

    def __init__(
        self,
        *,
        message_limit: int = 10,
        max_length: int = 18,
    ) -> None:
        self.message_limit = message_limit
        self.max_length = max_length

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_for_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        strategy: Optional[str] = None,
    ) -> SessionTitleResult:
        """Generate (or refresh) the title for a single session."""
        strategy_key = (strategy or "auto").lower()
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    name,
                    name_source,
                    is_user_named,
                    plan_id,
                    plan_title,
                    current_task_id,
                    current_task_name
                FROM chat_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if not row:
                raise SessionNotFoundError(f"Session {session_id} not found")

            current_title = row["name"]
            current_source = row["name_source"]
            is_user_named = bool(row["is_user_named"]) if row["is_user_named"] is not None else False

            effective_current_title = current_title or self._default_title(session_id)
            effective_current_source = current_source or ("user" if is_user_named else "default")

            if is_user_named and not force:
                return SessionTitleResult(
                    session_id=session_id,
                    title=effective_current_title,
                    source=effective_current_source,
                    previous_title=current_title,
                    updated=False,
                    skipped_reason="user_named",
                )

            messages = conn.execute(
                """
                SELECT role, content, metadata
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, self.message_limit),
            ).fetchall()

            # Convert to chronological order for downstream heuristics
            history = list(reversed(messages))

            new_title, source = self._derive_best_title(
                session_id=session_id,
                plan_title=row["plan_title"],
                current_task_name=row["current_task_name"],
                history=history,
                strategy=strategy_key,
            )

            if not new_title:
                new_title = self._default_title(session_id)
                source = "default"

            new_title = self._enforce_length(new_title)

            updated = (
                force
                or not current_title
                or new_title != current_title
                or (current_source or "") != source
                or is_user_named
            )

            if updated:
                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET
                        name = ?,
                        name_source = ?,
                        is_user_named = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (new_title, source, session_id),
                )
                conn.commit()

            return SessionTitleResult(
                session_id=session_id,
                title=new_title,
                source=source,
                previous_title=current_title,
                updated=updated,
                skipped_reason=None if updated else "unchanged",
            )

    def bulk_generate(
        self,
        *,
        session_ids: Optional[Sequence[str]] = None,
        force: bool = False,
        strategy: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[SessionTitleResult]:
        """Generate titles for a batch of sessions."""
        target_ids = list(session_ids or [])
        if not target_ids:
            target_ids = self._pick_candidate_sessions(limit=limit)

        results: List[SessionTitleResult] = []
        for session_id in target_ids:
            try:
                result = self.generate_for_session(
                    session_id,
                    force=force,
                    strategy=strategy,
                )
            except SessionNotFoundError as exc:
                logger.warning("Cannot auto-title missing session %s: %s", session_id, exc)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Failed to auto-title session %s: %s", session_id, exc)
            else:
                results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _pick_candidate_sessions(self, *, limit: Optional[int]) -> List[str]:
        """Select sessions that still carry default titles."""
        max_items = limit if limit and limit > 0 else self.DEFAULT_LIMIT
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM chat_sessions
                WHERE
                    (name IS NULL OR name = '' OR name_source IS NULL OR name_source = 'default')
                    AND (is_user_named IS NULL OR is_user_named = 0)
                ORDER BY COALESCE(last_message_at, updated_at, created_at) DESC, id ASC
                LIMIT ?
                """,
                (max_items,),
            ).fetchall()
            return [row["id"] for row in rows]

    def _derive_best_title(
        self,
        *,
        session_id: str,
        plan_title: Optional[str],
        current_task_name: Optional[str],
        history: Sequence[Any],
        strategy: str,
    ) -> Tuple[str, str]:
        """Choose the best possible title from available context."""
        if plan_title:
            combined = self._combine_plan_context(plan_title, current_task_name)
            return combined, "plan"

        if strategy in {"auto", "heuristic"}:
            heuristic = self._heuristic_from_history(history)
            if heuristic:
                return heuristic, "heuristic"

        return self._default_title(session_id), "default"

    def _combine_plan_context(self, plan_title: str, task_name: Optional[str]) -> str:
        """Merge plan title and current task into a concise label."""
        title = self._enforce_length(plan_title.strip())
        if task_name:
            task_clean = self._enforce_length(task_name.strip(), clamp=12)
            if task_clean:
                merged = f"{title} · {task_clean}"
                return self._enforce_length(merged, clamp=self.max_length + 8)
        return title

    def _heuristic_from_history(self, history: Sequence[Any]) -> Optional[str]:
        """Build a concise title from recent user messages."""
        for record in reversed(history):
            try:
                role = record["role"]
                content = record["content"]
            except (TypeError, KeyError):
                continue
            if role != "user":
                continue
            candidate = self._clean_sentence(content)
            if candidate:
                return self._enforce_length(candidate)
        return None

    def _clean_sentence(self, text: Optional[str]) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"\s+", " ", str(text)).strip()
        # Drop leading prompt words
        drop_prefixes = ("帮我", "请帮我", "请", "我想", "希望", "能否", "需要")
        for prefix in drop_prefixes:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 2:
                cleaned = cleaned[len(prefix) :].strip()
                break
        # Remove trailing punctuation that does not affect meaning
        cleaned = cleaned.strip("。！？?!;；")
        return cleaned

    def _enforce_length(self, text: str, *, clamp: Optional[int] = None) -> str:
        """Clamp text to a maximum length with an ellipsis when appropriate."""
        limit = clamp or self.max_length
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip("，,、.。;；!?！？」》）") + "…"

    def _default_title(self, session_id: str) -> str:
        return f"会话 {session_id[-8:]}"
