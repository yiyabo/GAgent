"""Lifespan-managed polling runner for delayed quality evaluations."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional

from app.services.foundation.settings import get_settings

from .service import ConversationQualityService

logger = logging.getLogger(__name__)


class ConversationQualityRunner:
    def __init__(self, service: ConversationQualityService) -> None:
        self.service = service
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if not self.service.enabled or self._task is not None:
            return
        recovered = self.service.recover_captures()
        if recovered:
            logger.info("[QUALITY] recovered %d interrupted evaluation(s)", recovered)
        self._task = asyncio.create_task(self._run(), name="conversation-quality-runner")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.service.evaluate_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[QUALITY] due-evaluation scan failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=get_settings().quality_evaluation_poll_seconds,
                )
            except asyncio.TimeoutError:
                continue
