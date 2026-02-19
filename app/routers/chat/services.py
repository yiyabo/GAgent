"""Shared chat router singletons.

This module centralizes chat-level singleton services so chat modules can share
state without cross-module import cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Type

from app.config.decomposer_config import get_decomposer_settings
from app.repository.plan_repository import PlanRepository
from app.services.foundation.settings import get_settings
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_executor import PlanExecutor
from app.services.session_title_service import SessionTitleService

if TYPE_CHECKING:
    from .agent import StructuredChatAgent

plan_repository = PlanRepository()
decomposer_settings = get_decomposer_settings()
plan_decomposer_service = PlanDecomposer(
    repo=plan_repository,
    settings=decomposer_settings,
)
plan_executor_service = PlanExecutor(repo=plan_repository)
session_title_service = SessionTitleService()
app_settings = get_settings()


def get_structured_chat_agent_cls() -> Type["StructuredChatAgent"]:
    """Resolve StructuredChatAgent lazily to avoid module import cycles."""
    from .agent import StructuredChatAgent

    return StructuredChatAgent

