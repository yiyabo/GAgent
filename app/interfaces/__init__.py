"""Abstract interfaces for the application layers."""

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
    """Deprecated legacy task repository interface.

    The dialogue pipeline now relies on PlanTree persistence through
    :class:`app.repository.plan_repository.PlanRepository`.  Instantiating this
    class (or subclasses) raises ``RuntimeError`` to ensure legacy code migrates.
    """

    def __new__(cls, *args, **kwargs):  # pragma: no cover - defensive
        raise RuntimeError(
            "TaskRepository is deprecated. Use PlanRepository and PlanTree-based "
            "APIs instead of the legacy tasks table."
        )

    # --- mutations ---
    @abstractmethod
    def create_task(
        self,
        name: str,
        status: str = "pending",
        priority: Optional[int] = None,
        parent_id: Optional[int] = None,
        task_type: str = "atomic",
        session_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        root_id: Optional[int] = None,
        context_refs: Optional[str] = None,
        artifacts: Optional[str] = None,
    ) -> int:
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
    def list_all_tasks(
        self,
        session_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_tasks_by_prefix(
        self, prefix: str, pending_only: bool = False, ordered: bool = True
    ) -> List[Dict[str, Any]]:
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

    # --- execution metadata ---
    def update_task_context(self, task_id: int, *, context_refs: Optional[str] = None, artifacts: Optional[str] = None) -> None:
        raise NotImplementedError

    def append_execution_log(
        self,
        task_id: int,
        *,
        workflow_id: Optional[str] = None,
        step_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        raise NotImplementedError

    def list_execution_logs(self, task_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_workflow_metadata(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # --- links (graph) ---
    def create_link(self, from_id: int, to_id: int, kind: str) -> None:
        """Create a directed link from one task to another.

        Typical kinds: 'requires' (hard dependency), 'refers' (soft reference).
        Default base implementation raises to signal optional support.
        """
        raise NotImplementedError

    def delete_link(self, from_id: int, to_id: int, kind: str) -> None:
        """Delete a directed link between tasks."""
        raise NotImplementedError

    def list_links(
        self,
        from_id: Optional[int] = None,
        to_id: Optional[int] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List links filtered by optional endpoints and kind."""
        raise NotImplementedError

    def list_dependencies(self, task_id: int) -> List[Dict[str, Any]]:
        """List upstream tasks linked into the given task (e.g., requires/refers)."""
        raise NotImplementedError

    # --- context snapshots (Phase 2) ---
    def upsert_task_context(
        self,
        task_id: int,
        combined: str,
        sections: List[Dict[str, Any]],
        meta: Dict[str, Any],
        label: Optional[str] = "latest",
    ) -> None:
        """Persist a context snapshot for a task. Default label 'latest'."""
        raise NotImplementedError

    def get_task_context(self, task_id: int, label: Optional[str] = "latest") -> Optional[Dict[str, Any]]:
        """Retrieve a context snapshot for a task by label."""
        raise NotImplementedError

    def list_task_contexts(self, task_id: int) -> List[Dict[str, Any]]:
        """List available snapshot labels and metadata for a task."""
        raise NotImplementedError

    # --- hierarchy (Phase 5) ---
    # Optional: default implementations raise NotImplementedError so legacy repos still instantiate.
    def get_task_info(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Return full task info including hierarchy fields (parent_id, path, depth)."""
        raise NotImplementedError

    def get_parent(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Return the parent task if any."""
        raise NotImplementedError

    def get_children(self, parent_id: int) -> List[Dict[str, Any]]:
        """Return direct children tasks of the given parent."""
        raise NotImplementedError

    def get_ancestors(self, task_id: int) -> List[Dict[str, Any]]:
        """Return ordered ancestors from root to the parent of the task."""
        raise NotImplementedError

    def get_descendants(self, root_id: int) -> List[Dict[str, Any]]:
        """Return all descendants of a task ordered by path."""
        raise NotImplementedError

    def get_subtree(self, root_id: int) -> List[Dict[str, Any]]:
        """Return root followed by all descendants ordered by path."""
        raise NotImplementedError

    def update_task_parent(self, task_id: int, new_parent_id: Optional[int]) -> None:
        """Move a task to a different parent (or to root level if new_parent_id is None).

        This should update the task's parent_id, path, and depth fields consistently.
        Implementations should handle path/depth recalculation for the moved subtree.
        """
        raise NotImplementedError

    def update_task_type(self, task_id: int, task_type: str) -> None:
        """Update the task type (root/composite/atomic).

        This is used by the recursive decomposition service to mark tasks
        that have been decomposed into subtasks.
        """
        raise NotImplementedError
