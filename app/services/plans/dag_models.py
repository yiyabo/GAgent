"""
DAG (Directed Acyclic Graph) Data Models

定义用于表示简化后计划结构的 DAG 数据模型。
DAG 允许一个节点有多个父节点，用于表示合并后的任务依赖关系。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class DAGNode:
    """DAG 中的节点"""

    id: int
    name: str
    instruction: Optional[str] = None

    # 原始节点ID列表（合并后可能包含多个）
    source_node_ids: List[int] = field(default_factory=list)

    # DAG结构：多个父节点，多个子节点
    parent_ids: Set[int] = field(default_factory=set)
    child_ids: Set[int] = field(default_factory=set)

    # 依赖关系
    dependencies: Set[int] = field(default_factory=set)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_parent(self, parent_id: int) -> None:
        self.parent_ids.add(parent_id)

    def add_child(self, child_id: int) -> None:
        self.child_ids.add(child_id)

    def merge_from(self, other: "DAGNode") -> None:
        """合并另一个节点的信息"""
        self.source_node_ids.extend(other.source_node_ids)
        self.parent_ids.update(other.parent_ids)
        self.child_ids.update(other.child_ids)
        self.dependencies.update(other.dependencies)
        # 合并 instruction（追加或保留更长的）
        if other.instruction:
            if self.instruction:
                self.instruction += f"\n---\n{other.instruction}"
            else:
                self.instruction = other.instruction
        # 合并 metadata
        if other.metadata:
            self.metadata.update(other.metadata)


@dataclass
class DAG:
    """有向无环图结构"""

    plan_id: int
    title: str
    description: Optional[str] = None

    nodes: Dict[int, DAGNode] = field(default_factory=dict)

    # 合并记录: 被合并节点ID -> 目标节点ID
    merge_map: Dict[int, int] = field(default_factory=dict)

    def node_count(self) -> int:
        return len(self.nodes)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可 JSON 序列化的字典"""
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
        """获取所有根节点（无父节点）"""
        return [n for n in self.nodes.values() if not n.parent_ids]

    def get_leaves(self) -> List[DAGNode]:
        """获取所有叶节点（无子节点）"""
        return [n for n in self.nodes.values() if not n.child_ids]

    def topological_sort(self, reverse: bool = False) -> List[int]:
        """拓扑排序，返回节点ID列表"""
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
            raise ValueError("图中存在环，无法拓扑排序")

        return list(reversed(result)) if reverse else result

    def to_outline(self) -> str:
        """生成DAG结构的文本描述"""
        lines = [
            f"DAG: {self.title} (Plan #{self.plan_id})",
            f"节点数: {self.node_count()}",
            f"合并数: {len(self.merge_map)}",
            "",
        ]

        try:
            sorted_ids = self.topological_sort()
        except ValueError:
            sorted_ids = list(self.nodes.keys())

        for node_id in sorted_ids:
            node = self.nodes[node_id]
            parents = ",".join(map(str, sorted(node.parent_ids))) or "无"
            children = ",".join(map(str, sorted(node.child_ids))) or "无"
            sources = ",".join(map(str, node.source_node_ids))

            lines.append(f"[{node_id}] {node.name}")
            lines.append(f"    来源: {sources}")
            lines.append(f"    父节点: {parents} | 子节点: {children}")
            if node.instruction:
                instr = (
                    node.instruction[:80] + "..."
                    if len(node.instruction) > 80
                    else node.instruction
                )
                lines.append(f"    指令: {instr}")

        return "\n".join(lines)

    def visualize(self, show_instruction: bool = False) -> str:
        """
        可视化DAG结构（ASCII图形）

        Args:
            show_instruction: 是否显示节点指令

        Returns:
            ASCII格式的DAG可视化字符串
        """
        lines = []
        lines.append(f"╔{'═'*58}╗")
        lines.append(f"║ DAG: {self.title[:50]:<52}║")
        lines.append(
            f"║ Plan #{self.plan_id} | 节点: {self.node_count()} | 合并: {len(self.merge_map):<15}║"
        )
        lines.append(f"╠{'═'*58}╣")

        # 获取根节点
        roots = [n for n in self.nodes.values() if not n.parent_ids]

        if not roots:
            lines.append("║ (空图)                                                   ║")
            lines.append(f"╚{'═'*58}╝")
            return "\n".join(lines)

        # BFS遍历并记录层级
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

        # 打印每层
        for level in sorted(levels.keys()):
            node_ids = levels[level]
            indent = "  " * level

            for node_id in node_ids:
                node = self.nodes[node_id]

                # 节点符号
                if not node.parent_ids:
                    prefix = "◉"  # 根节点
                elif not node.child_ids:
                    prefix = "◎"  # 叶节点
                else:
                    prefix = "○"  # 中间节点

                # 多父节点标记
                multi_parent = (
                    f" ←[{','.join(map(str, sorted(node.parent_ids)))}]"
                    if len(node.parent_ids) > 1
                    else ""
                )

                name = node.name[:40] + "..." if len(node.name) > 40 else node.name
                lines.append(f"║ {indent}{prefix} [{node_id}] {name}{multi_parent}")

                # 显示指令
                if show_instruction and node.instruction:
                    instr = (
                        node.instruction[:50] + "..."
                        if len(node.instruction) > 50
                        else node.instruction
                    )
                    lines.append(f"║ {indent}   └─ {instr}")

                # 显示子节点连接
                if node.child_ids:
                    children_str = ",".join(map(str, sorted(node.child_ids)))
                    lines.append(f"║ {indent}   ↓ [{children_str}]")

        lines.append(f"╠{'═'*58}╣")
        lines.append("║ 图例: ◉根节点  ○中间节点  ◎叶节点  ←多父节点           ║")
        lines.append(f"╚{'═'*58}╝")

        return "\n".join(lines)

    def print_adjacency(self) -> str:
        """
        打印邻接表

        Returns:
            邻接表的文本表示
        """
        lines = [
            f"邻接表 - {self.title} (Plan #{self.plan_id})",
            f"{'─'*50}",
            "节点 → 子节点列表",
            f"{'─'*50}",
        ]

        for node_id in sorted(self.nodes.keys()):
            node = self.nodes[node_id]
            children = sorted(node.child_ids) if node.child_ids else []
            children_str = ", ".join(map(str, children)) if children else "(无)"
            lines.append(f"[{node_id:3}] {node.name[:30]:<30} → {children_str}")

        lines.append(f"{'─'*50}")

        # 反向邻接表
        lines.append("")
        lines.append("反向邻接表 (入边)")
        lines.append(f"{'─'*50}")
        lines.append("节点 ← 父节点列表")
        lines.append(f"{'─'*50}")

        for node_id in sorted(self.nodes.keys()):
            node = self.nodes[node_id]
            parents = sorted(node.parent_ids) if node.parent_ids else []
            parents_str = ", ".join(map(str, parents)) if parents else "(根节点)"
            lines.append(f"[{node_id:3}] {node.name[:30]:<30} ← {parents_str}")

        return "\n".join(lines)
