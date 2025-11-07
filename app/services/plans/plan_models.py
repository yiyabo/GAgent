from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field


class PlanSummary(BaseModel):
    """Lightweight plan metadata used for list operations."""

    id: int
    title: str
    description: Optional[str] = None
    task_count: int = 0
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PlanNode(BaseModel):
    """Single task node within a plan tree."""

    id: int
    plan_id: int
    name: str
    status: str = "pending"
    instruction: Optional[str] = None
    parent_id: Optional[int] = None
    position: int = 0
    depth: int = 0
    path: str = Field(default="/")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[int] = Field(default_factory=list)
    context_combined: Optional[str] = None
    context_sections: List[Dict[str, Any]] = Field(default_factory=list)
    context_meta: Dict[str, Any] = Field(default_factory=dict)
    context_updated_at: Optional[str] = None
    execution_result: Optional[str] = None

    def display_name(self) -> str:
        """Short helper for prompt rendering."""
        return self.name.strip() or f"Task {self.id}"


class PlanTree(BaseModel):
    """In-memory representation of a plan and its tasks."""

    id: int
    title: str
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    nodes: Dict[int, PlanNode] = Field(default_factory=dict)
    adjacency: Dict[Optional[int], List[int]] = Field(default_factory=dict)

    def node_count(self) -> int:
        return len(self.nodes)

    def is_empty(self) -> bool:
        return self.node_count() == 0

    def has_node(self, node_id: int) -> bool:
        return node_id in self.nodes

    def root_node_ids(self) -> List[int]:
        """Return ordered list of root nodes (parent_id is NULL)."""
        return list(self.adjacency.get(None, []))

    def children_ids(self, node_id: Optional[int]) -> List[int]:
        """Return ordered children IDs for given node."""
        return list(self.adjacency.get(node_id, []))

    def iter_nodes(self) -> Iterable[PlanNode]:
        return self.nodes.values()

    def get_node(self, node_id: int) -> PlanNode:
        return self.nodes[node_id]

    def rebuild_adjacency(self) -> None:
        """Rebuild adjacency map based on current node parent/position."""
        adjacency: Dict[Optional[int], List[int]] = {}
        for node in sorted(self.nodes.values(), key=lambda n: (n.parent_id or -1, n.position, n.id)):
            adjacency.setdefault(node.parent_id, []).append(node.id)
        self.adjacency = adjacency

    def to_outline(self, max_depth: int = 3, max_nodes: int = 40) -> str:
        """Render the plan as a compact outline for prompts."""

        def _render(node_id: int, depth: int, lines: List[str], counter: List[int]) -> None:
            if depth > max_depth or counter[0] >= max_nodes:
                return
            node = self.nodes[node_id]
            indent = "  " * depth
            instruction = node.instruction.strip() if node.instruction else ""
            snippet = instruction[:90] + ("..." if instruction and len(instruction) > 90 else "")
            lines.append(f"{indent}- [{node.id}] {node.display_name()}{f' :: {snippet}' if snippet else ''}")
            counter[0] += 1
            for child_id in self.children_ids(node_id):
                _render(child_id, depth + 1, lines, counter)

        if self.is_empty():
            return "(plan has no tasks yet)"

        lines: List[str] = [f"Plan #{self.id}: {self.title}"]
        if self.description:
            lines.append(f"Description: {self.description}")
        counter = [0]
        for root_id in self.root_node_ids():
            _render(root_id, depth=0, lines=lines, counter=counter)
            if counter[0] >= max_nodes:
                break
        if counter[0] >= max_nodes:
            lines.append(f"... truncated after {counter[0]} nodes ...")
        return "\n".join(lines)

    def subgraph_outline(self, node_id: int, max_depth: int = 2) -> str:
        """Return a textual outline for a subgraph rooted at node_id."""
        if node_id not in self.nodes:
            return f"(node {node_id} not found in plan {self.id})"

        def _render(node_id: int, depth: int, lines: List[str]) -> None:
            node = self.nodes[node_id]
            indent = "  " * depth
            snippet = (node.instruction or "").strip()
            snippet = snippet[:90] + ("..." if snippet and len(snippet) > 90 else "")
            lines.append(f"{indent}- [{node.id}] {node.display_name()}{f' :: {snippet}' if snippet else ''}")
            if depth >= max_depth:
                return
            for child_id in self.children_ids(node_id):
                _render(child_id, depth + 1, lines)

        lines: List[str] = [f"Subgraph rooted at node {node_id} (depth ≤ {max_depth})"]
        _render(node_id, depth=0, lines=lines)
        return "\n".join(lines)

    def subgraph_nodes(self, node_id: int, max_depth: int = 2) -> List[PlanNode]:
        """Return PlanNode objects within max_depth of node_id (depth 0 included)."""
        if node_id not in self.nodes:
            raise ValueError(f"node {node_id} not found in plan {self.id}")

        collected: List[PlanNode] = []

        def _visit(current_id: int, depth: int) -> None:
            if depth > max_depth:
                return
            node = self.nodes.get(current_id)
            if not node:
                return
            collected.append(node)
            if depth == max_depth:
                return
            for child_id in self.children_ids(current_id):
                _visit(child_id, depth + 1)

        _visit(node_id, 0)
        return collected

    def ordered_nodes(self) -> List[PlanNode]:
        """Return all nodes in parent-before-child order for persistence."""
        ordered: List[PlanNode] = []
        visited: set[int] = set()

        def _visit(node_id: int) -> None:
            if node_id in visited or node_id not in self.nodes:
                return
            visited.add(node_id)
            ordered.append(self.nodes[node_id])
            for child_id in self.children_ids(node_id):
                _visit(child_id)

        for root_id in self.root_node_ids():
            _visit(root_id)

        # Include any orphan nodes that might exist
        for node_id in sorted(self.nodes):
            if node_id not in visited:
                _visit(node_id)

        return ordered
