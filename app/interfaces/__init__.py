"""Abstract interfaces for the application layers.

- LLMProvider: abstraction for LLM clients (chat, ping, config)
- TaskRepository: abstraction for task persistence and queries
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LLMProvider(ABC):
    """Abstract interface for an LLM provider/client."""

    @abstractmethod
    def chat(self, prompt: str) -> str:  # pragma: no cover - interface only
        """Return completion text for a given prompt."""
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> bool:  # pragma: no cover - interface only
        """Connectivity check; return True if provider is reachable."""
        raise NotImplementedError

    @abstractmethod
    def config(self) -> Dict[str, Any]:  # pragma: no cover - interface only
        """Return provider configuration info for health/debug endpoints."""
        raise NotImplementedError


class TaskRepository(ABC):
    """Abstract interface for task persistence and queries."""

    # --- mutations ---
    @abstractmethod
    def create_task(self, name: str, status: str = "pending", priority: Optional[int] = None) -> int:
        raise NotImplementedError

    @abstractmethod
    def upsert_task_input(self, task_id: int, prompt: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_task_output(self, task_id: int, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_task_status(self, task_id: int, status: str) -> None:
        raise NotImplementedError

    # --- queries ---
    @abstractmethod
    def list_all_tasks(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_tasks_by_prefix(self, prefix: str, pending_only: bool = False, ordered: bool = True) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_task_input_prompt(self, task_id: int) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def get_task_output_content(self, task_id: int) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def list_plan_titles(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def list_plan_tasks(self, title: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_plan_outputs(self, title: str) -> List[Dict[str, Any]]:
        raise NotImplementedError
