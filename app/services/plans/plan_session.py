from __future__ import annotations

from typing import List, Optional

from .plan_models import PlanSummary, PlanTree
from ...repository.plan_repository import PlanRepository


class PlanSession:
    """Helper that binds a dialogue session to an in-memory plan tree."""

    def __init__(self, repo: Optional[PlanRepository] = None, plan_id: Optional[int] = None) -> None:
        self._repo = repo or PlanRepository()
        self.plan_id: Optional[int] = plan_id
        self._plan_tree: Optional[PlanTree] = None
        self._loaded: bool = False

    @property
    def repo(self) -> PlanRepository:
        return self._repo

    def bind(self, plan_id: int) -> PlanTree:
        """Bind to a plan and preload its tree."""
        self.plan_id = plan_id
        return self.refresh()

    def refresh(self) -> Optional[PlanTree]:
        """Reload the plan tree from storage."""
        self._loaded = True
        if self.plan_id is None:
            self._plan_tree = None
            return None
        self._plan_tree = self._repo.get_plan_tree(self.plan_id)
        return self._plan_tree

    def ensure(self) -> PlanTree:
        """Return the bound plan tree, loading it if necessary."""
        if not self._loaded:
            self.refresh()
        if self._plan_tree is None or self.plan_id is None:
            raise RuntimeError("The current session is not bound to a specific plan.")
        return self._plan_tree

    def outline(self, max_depth: int = 3, max_nodes: int = 40) -> str:
        """Return a string outline for prompt inclusion."""
        if self.plan_id is None:
            return "(no plan bound)"
        tree = self.ensure()
        return tree.to_outline(max_depth=max_depth, max_nodes=max_nodes)

    def subgraph_outline(self, node_id: int, max_depth: int = 2) -> str:
        tree = self.ensure()
        return tree.subgraph_outline(node_id, max_depth=max_depth)

    def list_plans(self) -> List[PlanSummary]:
        return self._repo.list_plans()

    def summaries_for_prompt(self, limit: int = 10) -> str:
        """Return brief plan summaries for list_plans."""
        plans = self.list_plans()[:limit]
        if not plans:
            return "(no existing plans)"
        lines = []
        for plan in plans:
            lines.append(f"- #{plan.id} {plan.title} (tasks: {plan.task_count})")
        return "\n".join(lines)

    def current_tree(self) -> Optional[PlanTree]:
        if not self._loaded:
            self.refresh()
        return self._plan_tree

    def detach(self) -> None:
        self.plan_id = None
        self._plan_tree = None
        self._loaded = True

    def persist_current_tree(self, note: Optional[str] = None) -> None:
        if self.plan_id is None:
            return
        tree = self.ensure()
        self._repo.upsert_plan_tree(tree, note=note)
