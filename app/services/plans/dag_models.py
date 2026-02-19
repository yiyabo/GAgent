"""
DAG (Directed Acyclic Graph) Data Models

plan DAG model. 
DAG , task. 
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class DAGNode:
    """DAG medium"""

    id: int
    name: str
    instruction: Optional[str] = None

    source_node_ids: List[int] = field(default_factory=list)

    parent_ids: Set[int] = field(default_factory=set)
    child_ids: Set[int] = field(default_factory=set)

    dependencies: Set[int] = field(default_factory=set)

    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_parent(self, parent_id: int) -> None:
        self.parent_ids.add(parent_id)

    def add_child(self, child_id: int) -> None:
        self.child_ids.add(child_id)

    def merge_from(self, other: "DAGNode") -> None:
        """"""
        self.source_node_ids.extend(other.source_node_ids)
        self.parent_ids.update(other.parent_ids)
        self.child_ids.update(other.child_ids)
        self.dependencies.update(other.dependencies)
        if other.instruction:
            if self.instruction:
                self.instruction += f"\n---\n{other.instruction}"
            else:
                self.instruction = other.instruction
        if other.metadata:
            self.metadata.update(other.metadata)


@dataclass
class DAG:
    """"""

    plan_id: int
    title: str
    description: Optional[str] = None

    nodes: Dict[int, DAGNode] = field(default_factory=dict)

    merge_map: Dict[int, int] = field(default_factory=dict)

    def node_count(self) -> int:
        return len(self.nodes)

    def to_dict(self) -> Dict[str, Any]:
        """ JSON """
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "description": self.description,
            "node_count": self.node_count(),
            "merge_count": len(self.merge_map),
            "merge_map": self.merge_map,
            "nodes": {
                nid: {
                    "id": node.id,
                    "name": node.name,
                    "instruction": node.instruction,
                    "source_node_ids": node.source_node_ids,
                    "parent_ids": sorted(node.parent_ids),
                    "child_ids": sorted(node.child_ids),
                    "dependencies": sorted(node.dependencies),
                    "metadata": node.metadata,
                }
                for nid, node in sorted(self.nodes.items())
            },
        }

    def get_roots(self) -> List[DAGNode]:
        """get()"""
        return [n for n in self.nodes.values() if not n.parent_ids]

    def get_leaves(self) -> List[DAGNode]:
        """get()"""
        return [n for n in self.nodes.values() if not n.child_ids]

    def topological_sort(self, reverse: bool = False) -> List[int]:
        """, ID"""
        in_degree = {nid: len(n.parent_ids) for nid, n in self.nodes.items()}
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            node_id = queue.pop(0)
            result.append(node_id)

            for child_id in self.nodes[node_id].child_ids:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    queue.append(child_id)

        if len(result) != len(self.nodes):
            raise ValueError("medium, ")

        return list(reversed(result)) if reverse else result

    def to_outline(self) -> str:
        """DAG"""
        lines = [
            f"DAG: {self.title} (Plan #{self.plan_id})",
            f": {self.node_count()}",
            f": {len(self.merge_map)}",
            "",
        ]

        try:
            sorted_ids = self.topological_sort()
        except ValueError:
            sorted_ids = list(self.nodes.keys())

        for node_id in sorted_ids:
            node = self.nodes[node_id]
            parents = ",".join(map(str, sorted(node.parent_ids))) or ""
            children = ",".join(map(str, sorted(node.child_ids))) or ""
            sources = ",".join(map(str, node.source_node_ids))

            lines.append(f"[{node_id}] {node.name}")
            lines.append(f"  : {sources}")
            lines.append(f"  : {parents} | : {children}")
            if node.instruction:
                instr = (
                    node.instruction[:80] + "..."
                    if len(node.instruction) > 80
                    else node.instruction
                )
                lines.append(f"  : {instr}")

        return "\n".join(lines)

    def visualize(self, show_instruction: bool = False) -> str:
        """
        DAG(ASCII)

        Args:
            show_instruction: 

        Returns:
            ASCIIDAG
        """
        lines = []
        lines.append(f"╔{'═'*58}╗")
        lines.append(f"║ DAG: {self.title[:50]:<52}║")
        lines.append(
            f"║ Plan #{self.plan_id} | : {self.node_count()} | : {len(self.merge_map):<15}║"
        )
        lines.append(f"╠{'═'*58}╣")

        roots = [n for n in self.nodes.values() if not n.parent_ids]

        if not roots:
            lines.append("║ ()  ║")
            lines.append(f"╚{'═'*58}╝")
            return "\n".join(lines)

        visited = set()
        levels: Dict[int, List[int]] = {}  # level -> [node_ids]
        node_level: Dict[int, int] = {}  # node_id -> level

        queue = [(r.id, 0) for r in roots]
        while queue:
            node_id, level = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)

            levels.setdefault(level, []).append(node_id)
            node_level[node_id] = level

            node = self.nodes[node_id]
            for child_id in sorted(node.child_ids):
                if child_id not in visited:
                    queue.append((child_id, level + 1))

        for level in sorted(levels.keys()):
            node_ids = levels[level]
            indent = "  " * level

            for node_id in node_ids:
                node = self.nodes[node_id]

                if not node.parent_ids:
                    prefix = "◉"  # 
                elif not node.child_ids:
                    prefix = "◎"  # 
                else:
                    prefix = "○"  # medium

                multi_parent = (
                    f" ←[{','.join(map(str, sorted(node.parent_ids)))}]"
                    if len(node.parent_ids) > 1
                    else ""
                )

                name = node.name[:40] + "..." if len(node.name) > 40 else node.name
                lines.append(f"║ {indent}{prefix} [{node_id}] {name}{multi_parent}")

                if show_instruction and node.instruction:
                    instr = (
                        node.instruction[:50] + "..."
                        if len(node.instruction) > 50
                        else node.instruction
                    )
                    lines.append(f"║ {indent}  └─ {instr}")

                if node.child_ids:
                    children_str = ",".join(map(str, sorted(node.child_ids)))
                    lines.append(f"║ {indent}  ↓ [{children_str}]")

        lines.append(f"╠{'═'*58}╣")
        lines.append("║ : ◉  ○medium  ◎  ←  ║")
        lines.append(f"╚{'═'*58}╝")

        return "\n".join(lines)

    def print_adjacency(self) -> str:
        """


        Returns:

        """
        lines = [
            f" - {self.title} (Plan #{self.plan_id})",
            f"{'─'*50}",
            " → ",
            f"{'─'*50}",
        ]

        for node_id in sorted(self.nodes.keys()):
            node = self.nodes[node_id]
            children = sorted(node.child_ids) if node.child_ids else []
            children_str = ", ".join(map(str, children)) if children else "()"
            lines.append(f"[{node_id:3}] {node.name[:30]:<30} → {children_str}")

        lines.append(f"{'─'*50}")

        lines.append("")
        lines.append(" ()")
        lines.append(f"{'─'*50}")
        lines.append(" ← ")
        lines.append(f"{'─'*50}")

        for node_id in sorted(self.nodes.keys()):
            node = self.nodes[node_id]
            parents = sorted(node.parent_ids) if node.parent_ids else []
            parents_str = ", ".join(map(str, parents)) if parents else "()"
            lines.append(f"[{node_id:3}] {node.name[:30]:<30} ← {parents_str}")

        return "\n".join(lines)
