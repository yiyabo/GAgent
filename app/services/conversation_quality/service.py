"""Non-blocking orchestration of durable conversation-quality evaluation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.repository import conversation_quality as repository
from app.services.foundation.settings import get_settings

from .evaluator import ConversationQualityEvaluator
from .snapshot import build_run_snapshot

logger = logging.getLogger(__name__)


class ConversationQualityService:
    def __init__(self, evaluator: Optional[ConversationQualityEvaluator] = None) -> None:
        self._evaluator = evaluator
        self._semaphore: Optional[asyncio.Semaphore] = None

    @property
    def enabled(self) -> bool:
        return bool(get_settings().quality_evaluation_enabled)

    def _get_evaluator(self) -> ConversationQualityEvaluator:
        if self._evaluator is None:
            self._evaluator = ConversationQualityEvaluator()
        return self._evaluator

    def _get_semaphore(self) -> asyncio.Semaphore:
        limit = get_settings().quality_evaluation_max_concurrency
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(limit)
        return self._semaphore

    def _next_user_message(self, run: dict) -> Optional[dict]:
        return repository.get_next_user_message(
            session_id=str(run["session_id"]),
            after_message_id=run.get("user_message_id"),
        )

    def _schedule_follow_up_evaluation(self, run_id: str) -> None:
        evaluation = repository.get_evaluation_by_run(run_id)
        if evaluation is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self.evaluate_one(
                int(evaluation["id"]),
                evaluation_basis="follow_up_message",
            )
        )

    def capture_completed_run(self, run_id: str) -> bool:
        """Persist a bounded evidence snapshot. Safe to call repeatedly."""
        if not self.enabled:
            return False
        settings = get_settings()
        snapshot = build_run_snapshot(
            run_id,
            max_chars=settings.quality_evaluation_max_snapshot_chars,
        )
        if snapshot is None:
            logger.warning("[QUALITY] skipped missing run=%s", run_id)
            return False
        from app.repository.chat_runs import get_chat_run

        row = get_chat_run(run_id)
        if not row:
            return False
        observed_until = datetime.now(timezone.utc) + timedelta(
            hours=settings.quality_evaluation_observation_hours
        )
        feedback_message = self._next_user_message(row)
        if feedback_message is not None:
            observed_until = datetime.now(timezone.utc)
        created = repository.create_pending_evaluation(
            target_run_id=run_id,
            session_id=str(row["session_id"]),
            owner_id=str(row["owner_id"]),
            snapshot=snapshot,
            observed_until=observed_until,
            feedback_message_id=(
                int(feedback_message["id"]) if feedback_message is not None else None
            ),
            feedback_received_at=(
                datetime.fromisoformat(str(feedback_message["created_at"]))
                if feedback_message is not None and feedback_message.get("created_at")
                else None
            ),
        )
        if created and feedback_message is not None:
            self._schedule_follow_up_evaluation(run_id)
        if created:
            logger.info("[QUALITY] captured pending run=%s", run_id)
        return created

    async def handle_follow_up(
        self,
        *,
        session_id: str,
        owner_id: str,
        feedback_message_id: int,
    ) -> None:
        """Associate a persisted user message, then evaluate without touching chat delivery."""
        if not self.enabled:
            return
        evaluation_id = repository.attach_feedback_to_latest_pending(
            session_id=session_id,
            owner_id=owner_id,
            feedback_message_id=feedback_message_id,
        )
        if evaluation_id is not None:
            await self.evaluate_one(evaluation_id, evaluation_basis="follow_up_message")

    async def evaluate_due(self, *, limit: int = 20) -> int:
        if not self.enabled:
            return 0
        ids = repository.list_due_evaluation_ids(limit=limit)

        async def _evaluate(evaluation_id: int) -> bool:
            return await self.evaluate_one(evaluation_id)

        outcomes = await asyncio.gather(*(_evaluate(item) for item in ids), return_exceptions=True)
        return sum(outcome is True for outcome in outcomes)

    async def evaluate_one(
        self,
        evaluation_id: int,
        *,
        evaluation_basis: Optional[str] = None,
    ) -> bool:
        settings = get_settings()
        row = repository.claim_evaluation(
            evaluation_id,
            evaluation_basis=evaluation_basis or "observation_timeout",
            max_attempts=settings.quality_evaluation_max_attempts,
        )
        if row is None:
            return False
        resolved_basis = str(row.get("evaluation_basis") or "observation_timeout")
        try:
            feedback = repository.get_feedback_message(row)
            async with self._get_semaphore():
                result, prompt_version = await self._get_evaluator().evaluate(
                    snapshot=row["snapshot"],
                    feedback_message=feedback,
                    evaluation_basis=resolved_basis,
                    session_id=str(row["session_id"]),
                )
            terminal_status = "final" if feedback is not None else "provisional"
            repository.complete_evaluation(
                evaluation_id,
                status=terminal_status,
                result=result.model_dump(),
                evaluator_provider=self._get_evaluator().provider,
                evaluator_model=self._get_evaluator().model,
                prompt_version=prompt_version,
            )
            logger.info(
                "[QUALITY] evaluated id=%s status=%s level=%s confidence=%.2f",
                evaluation_id,
                terminal_status,
                result.satisfaction_level,
                result.confidence,
            )
            return True
        except Exception as exc:
            repository.record_evaluation_failure(
                evaluation_id,
                error=str(exc),
                max_attempts=settings.quality_evaluation_max_attempts,
                retry_at=datetime.now(timezone.utc) + timedelta(
                    seconds=settings.quality_evaluation_poll_seconds
                ),
            )
            logger.warning("[QUALITY] evaluation failed id=%s error=%s", evaluation_id, type(exc).__name__)
            return False

    def recover_captures(self) -> int:
        """Recover interrupted evaluator calls without backfilling historical chats."""
        if not self.enabled:
            return 0
        return repository.recover_stale_evaluations()


_quality_service: Optional[ConversationQualityService] = None


def get_conversation_quality_service() -> ConversationQualityService:
    global _quality_service
    if _quality_service is None:
        _quality_service = ConversationQualityService()
    return _quality_service
