"""A durable queue, one auxiliary call at a time; no historical backfill."""
from __future__ import annotations
import asyncio
import logging
from contextlib import suppress
from app.repository import skill_learning as repository
from app.services.foundation.settings import get_settings
from .service import get_skill_learning_service

logger=logging.getLogger(__name__)


class SkillLearningRunner:
    def __init__(self):self.task=None;self.stop_event=asyncio.Event()
    async def start(self):
        self.task=asyncio.create_task(self.run(),name='skill-learning-runner')
    async def stop(self):
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):await self.task
    async def run(self):
        while not self.stop_event.is_set():
            try:
                for usage_run in await asyncio.to_thread(repository.terminal_pending_usage_runs):
                    await asyncio.to_thread(get_skill_learning_service().complete_usages,usage_run)
                for run_id in await asyncio.to_thread(repository.due_jobs,get_settings().skill_learning_enabled):
                    await get_skill_learning_service().process_one(run_id)
            except asyncio.CancelledError:raise
            except Exception as exc:logger.warning('Skill learning queue failed: %s',type(exc).__name__)
            try:await asyncio.wait_for(self.stop_event.wait(),timeout=20)
            except asyncio.TimeoutError:pass
