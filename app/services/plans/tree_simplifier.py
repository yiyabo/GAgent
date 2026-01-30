"""
树结构简化器 - 将计划树转换为DAG

通过识别相似节点并合并，减少冗余任务，形成有向无环图(DAG)结构。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from .dag_models import DAG, DAGNode
from .plan_models import PlanNode, PlanTree
from .similarity_matcher import (
    CachedSimilarityMatcher,
    LLMSimilarityMatcher,
    SimilarityMatcher,
    SimpleSimilarityMatcher,
)

logger = logging.getLogger(__name__)


class TreeSimplifier:
    """
    树结构简化器

    将PlanTree转换为DAG，通过合并相似节点减少冗余

    用法:
        simplifier = TreeSimplifier(matcher=MySimilarityMatcher())
        dag = simplifier.simplify(plan_tree)
    """

    def __init__(
        self,
        matcher: Optional[SimilarityMatcher] = None,
        use_llm: bool = True,
        use_cache: bool = True,
    ):
        """
        初始化简化器

        Args:
            matcher: 相似度匹配器，用于识别和判断节点是否应合并
            use_llm: 是否使用 LLM 进行相似度判断
            use_cache: 是否使用缓存
        """
        if matcher is not None:
            self.matcher = matcher
        elif use_llm:
            base_matcher = LLMSimilarityMatcher()
            self.matcher = (
                CachedSimilarityMatcher(base_matcher) if use_cache else base_matcher
            )
        else:
            self.matcher = SimpleSimilarityMatcher()

    def is_reachable(self, dag: DAG, from_id: int, to_id: int) -> bool:
        """
        BFS检查从from_id是否可达to_id

        Args:
            dag: DAG结构
            from_id: 起始节点ID
            to_id: 目标节点ID

        Returns:
            是否可达
        """
        if from_id == to_id:
            return True

        visited = set()
        queue = [from_id]

        while queue:
            current = queue.pop(0)
            if current == to_id:
                return True
            if current in visited:
                continue
            visited.add(current)

            node = dag.nodes.get(current)
            if node:
                queue.extend(node.child_ids - visited)

        return False

    def can_merge(
        self, dag: DAG, node1_id: int, node2_id: int
    ) -> Tuple[bool, str]:
        """
        检查两个节点是否可以安全合并（不产生环）

        合并条件：
        1. 两节点都存在
        2. 不存在直接父子关系
        3. 不存在祖先-后代关系（互不可达）
        4. 不存在直接依赖关系

        Args:
            dag: DAG结构
            node1_id: 第一个节点ID
            node2_id: 第二个节点ID

        Returns:
            (可否合并, 原因)
        """
        node1 = dag.nodes.get(node1_id)
        node2 = dag.nodes.get(node2_id)

        if not node1 or not node2:
            return False, "节点不存在"

        if node1_id == node2_id:
            return False, "同一节点"

        # 检查1: 直接父子关系
        if node2_id in node1.child_ids or node1_id in node2.child_ids:
            return False, "存在直接父子关系（子节点）"

        if node2_id in node1.parent_ids or node1_id in node2.parent_ids:
            return False, "存在直接父子关系（父节点）"

        # 检查2: 祖先-后代关系（路径可达性）
        if self.is_reachable(dag, node1_id, node2_id):
            return False, f"[{node1_id}]是[{node2_id}]的祖先，存在路径"

        if self.is_reachable(dag, node2_id, node1_id):
            return False, f"[{node2_id}]是[{node1_id}]的祖先，存在路径"

        # 检查3: 依赖关系
        if node2_id in node1.dependencies:
            return False, f"[{node1_id}]依赖[{node2_id}]"

        if node1_id in node2.dependencies:
            return False, f"[{node2_id}]依赖[{node1_id}]"

        return True, "可以合并（互不可达的并行节点）"

    def find_mergeable_groups(self, dag: DAG) -> List[List[int]]:
        """
        找出所有可合并的节点组（互不可达的相似节点）

        Returns:
            可合并节点组列表，每组可以合并为一个节点
        """
        node_ids = list(dag.nodes.keys())
        n = len(node_ids)

        # 构建可达性矩阵
        reachable = {}
        for i in range(n):
            for j in range(i + 1, n):
                id1, id2 = node_ids[i], node_ids[j]
                can, _ = self.can_merge(dag, id1, id2)
                reachable[(id1, id2)] = can
                reachable[(id2, id1)] = can

        # 使用简单的贪心：按节点名称分组，然后检查组内是否可合并
        name_groups: Dict[str, List[int]] = {}
        for node_id, node in dag.nodes.items():
            # 使用名称的简化版本作为key
            name_key = node.name.strip().lower()
            name_groups.setdefault(name_key, []).append(node_id)

        mergeable_groups = []
        for name, ids in name_groups.items():
            if len(ids) < 2:
                continue

            # 检查组内所有节点两两可合并
            group_valid = True
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if not reachable.get((ids[i], ids[j]), False):
                        group_valid = False
                        break
                if not group_valid:
                    break

            if group_valid:
                mergeable_groups.append(ids)

        return mergeable_groups

    def tree_to_dag(self, tree: PlanTree) -> DAG:
        """
        将PlanTree转换为DAG（不做合并）

        Args:
            tree: 输入的计划树

        Returns:
            对应的DAG结构
        """
        dag = DAG(
            plan_id=tree.id,
            title=tree.title,
            description=tree.description,
        )

        # 转换所有节点
        for node_id, plan_node in tree.nodes.items():
            dag_node = DAGNode(
                id=node_id,
                name=plan_node.name,
                instruction=plan_node.instruction,
                source_node_ids=[node_id],
                dependencies=set(plan_node.dependencies),
                metadata=deepcopy(plan_node.metadata),
            )
            dag.nodes[node_id] = dag_node

        # 根据 adjacency (边) 设置父子关系
        for parent_id, children_ids in tree.adjacency.items():
            if parent_id is None:
                # 根节点，没有父节点
                continue
            if parent_id not in dag.nodes:
                continue
            for child_id in children_ids:
                if child_id in dag.nodes:
                    dag.nodes[parent_id].child_ids.add(child_id)
                    dag.nodes[child_id].parent_ids.add(parent_id)

        return dag

    def merge_nodes(
        self,
        dag: DAG,
        keep_id: int,
        remove_id: int,
        force: bool = False,
    ) -> bool:
        """
        合并两个节点，保留keep_id，删除remove_id

        Args:
            dag: DAG结构
            keep_id: 保留的节点ID
            remove_id: 要删除的节点ID
            force: 是否跳过安全检查

        Returns:
            是否成功合并
        """
        if keep_id not in dag.nodes or remove_id not in dag.nodes:
            return False

        # 安全检查
        if not force:
            can, reason = self.can_merge(dag, keep_id, remove_id)
            if not can:
                logger.warning(f"无法合并 [{keep_id}] 和 [{remove_id}]: {reason}")
                return False

        keep_node = dag.nodes[keep_id]
        remove_node = dag.nodes[remove_id]

        # 合并信息
        keep_node.merge_from(remove_node)

        # 移除自引用
        keep_node.parent_ids.discard(keep_id)
        keep_node.child_ids.discard(keep_id)
        keep_node.parent_ids.discard(remove_id)
        keep_node.child_ids.discard(remove_id)

        # 更新其他节点的引用
        for node_id, node in dag.nodes.items():
            if node_id == keep_id or node_id == remove_id:
                continue

            # 将指向remove_id的引用改为指向keep_id
            if remove_id in node.parent_ids:
                node.parent_ids.discard(remove_id)
                node.parent_ids.add(keep_id)

            if remove_id in node.child_ids:
                node.child_ids.discard(remove_id)
                node.child_ids.add(keep_id)

            if remove_id in node.dependencies:
                node.dependencies.discard(remove_id)
                node.dependencies.add(keep_id)

        # 删除节点并记录映射
        del dag.nodes[remove_id]
        dag.merge_map[remove_id] = keep_id

        return True

    def merge_group(self, dag: DAG, node_ids: List[int]) -> Optional[int]:
        """
        合并一组节点为一个

        Args:
            dag: DAG结构
            node_ids: 要合并的节点ID列表

        Returns:
            合并后保留的节点ID，失败返回None
        """
        if len(node_ids) < 2:
            return node_ids[0] if node_ids else None

        # 检查组内所有节点两两可合并
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                can, reason = self.can_merge(dag, node_ids[i], node_ids[j])
                if not can:
                    logger.warning(
                        f"组内节点不可合并: [{node_ids[i]}] 和 [{node_ids[j]}]: {reason}"
                    )
                    return None

        # 保留ID最小的节点
        keep_id = min(node_ids)
        merged_names = []

        for remove_id in sorted(node_ids):
            if remove_id == keep_id:
                continue
            node_name = (
                dag.nodes[remove_id].name if remove_id in dag.nodes else "?"
            )
            if self.merge_nodes(dag, keep_id, remove_id):
                merged_names.append(f"[{remove_id}]{node_name[:20]}")

        if merged_names:
            keep_name = dag.nodes[keep_id].name
            logger.info(
                f"合并完成: [{keep_id}]{keep_name[:20]} <- {', '.join(merged_names)}"
            )

        return keep_id

    def simplify(
        self,
        tree: PlanTree,
        max_iterations: int = 100,
    ) -> DAG:
        """
        简化树结构，合并相似节点，生成DAG

        Args:
            tree: 输入的计划树
            max_iterations: 最大迭代次数（防止无限循环）

        Returns:
            简化后的DAG结构
        """
        # 1. 转换为DAG
        dag = self.tree_to_dag(tree)

        original_count = dag.node_count()
        logger.info(f"开始简化 Plan #{tree.id}，原始节点数: {original_count}")

        # 2. 迭代合并相似节点
        for iteration in range(max_iterations):
            nodes = list(dag.nodes.values())
            similar_pairs = self.matcher.find_similar_pairs(nodes)

            if not similar_pairs:
                break

            # 按相似度降序排序
            similar_pairs.sort(key=lambda x: x[2], reverse=True)

            # 合并最相似的一对
            merged = False
            for node_id_1, node_id_2, score in similar_pairs:
                if node_id_1 not in dag.nodes or node_id_2 not in dag.nodes:
                    continue

                node1 = dag.nodes[node_id_1]
                node2 = dag.nodes[node_id_2]

                if self.matcher.should_merge(node1, node2):
                    # 保留ID较小的节点
                    keep_id = min(node_id_1, node_id_2)
                    remove_id = max(node_id_1, node_id_2)
                    if self.merge_nodes(dag, keep_id, remove_id):
                        merged = True
                        break

            if not merged:
                break

        final_count = dag.node_count()
        merged_count = len(dag.merge_map)
        logger.info(
            f"简化完成: {original_count} -> {final_count} 节点，合并 {merged_count} 个"
        )

        return dag

    def simplify_fast(self, tree: PlanTree) -> DAG:
        """
        快速简化（仅基于名称匹配，不使用 LLM）

        Args:
            tree: 输入的计划树

        Returns:
            简化后的DAG结构
        """
        # 1. 转换为DAG
        dag = self.tree_to_dag(tree)

        # 2. 找出可合并的组
        mergeable_groups = self.find_mergeable_groups(dag)

        # 3. 合并每个组
        for group in mergeable_groups:
            self.merge_group(dag, group)

        return dag

    def simplify_from_db(self, plan_id: int, repo=None) -> DAG:
        """
        从数据库加载计划并执行简化。

        Args:
            plan_id: 计划ID
            repo: PlanRepository实例（可选）

        Returns:
            简化后的DAG
        """
        if repo is None:
            from ...repository.plan_repository import PlanRepository

            repo = PlanRepository()

        tree = repo.get_plan_tree(plan_id)
        return self.simplify(tree)

    def save_dag_to_db(
        self,
        dag: DAG,
        repo=None,
        title_suffix: str = " (Simplified)",
    ) -> int:
        """
        将DAG保存为新的计划（Plan）。

        说明：
        - 由于Plan结构是树，这里选择一个父节点作为结构父节点，
          其余父节点与显式依赖统一映射为 dependencies。
        """
        if repo is None:
            from ...repository.plan_repository import PlanRepository

            repo = PlanRepository()

        new_plan = repo.create_plan(
            title=f"{dag.title}{title_suffix}",
            description=dag.description,
        )
        new_plan_id = new_plan.id

        id_map: Dict[int, int] = {}
        parent_map: Dict[int, Optional[int]] = {}
        metadata_map: Dict[int, Dict[str, Any]] = {}

        # 先创建所有节点，确保父节点先创建
        for node_id in dag.topological_sort():
            node = dag.nodes[node_id]

            parent_id = None
            if node.parent_ids:
                for pid in sorted(node.parent_ids):
                    if pid in id_map:
                        parent_id = id_map[pid]
                        break

            metadata = {
                "source_node_ids": node.source_node_ids,
                "original_parent_ids": list(node.parent_ids),
                "original_child_ids": list(node.child_ids),
                **node.metadata,
            }

            new_node = repo.create_task(
                new_plan_id,
                name=node.name,
                instruction=node.instruction,
                parent_id=parent_id,
                dependencies=None,
                metadata=metadata,
            )
            id_map[node_id] = new_node.id
            parent_map[node_id] = parent_id
            metadata_map[node_id] = metadata

        # 再统一补齐依赖映射（确保跨分支依赖不会丢失）
        for node_id, node in dag.nodes.items():
            mapped_deps: List[int] = []

            for dep_id in node.dependencies:
                mapped = id_map.get(dep_id)
                if mapped is not None and mapped not in mapped_deps:
                    mapped_deps.append(mapped)

            parent_id = parent_map.get(node_id)
            for pid in sorted(node.parent_ids):
                mapped = id_map.get(pid)
                if mapped is not None and mapped != parent_id and mapped not in mapped_deps:
                    mapped_deps.append(mapped)

            repo.update_task(
                new_plan_id,
                id_map[node_id],
                metadata=metadata_map[node_id],
                dependencies=mapped_deps or None,
            )

        return new_plan_id

    def simplify_and_save(
        self,
        plan_id: int,
        repo=None,
        title_suffix: str = " (Simplified)",
    ) -> Tuple[DAG, int]:
        """
        从数据库加载、简化、并在需要时保存为新计划。

        Returns:
            (DAG, 新计划ID或原计划ID)
        """
        dag = self.simplify_from_db(plan_id, repo)
        if not dag.merge_map:
            return dag, plan_id
        new_plan_id = self.save_dag_to_db(dag, repo, title_suffix)
        return dag, new_plan_id


# 便捷函数
def simplify_plan(plan_id: int, use_llm: bool = False) -> DAG:
    """
    便捷函数：简化指定计划

    Args:
        plan_id: 计划ID
        use_llm: 是否使用 LLM 进行相似度判断

    Returns:
        简化后的 DAG
    """
    from ...repository.plan_repository import PlanRepository

    repo = PlanRepository()
    tree = repo.get_plan_tree(plan_id)

    simplifier = TreeSimplifier(use_llm=use_llm)
    return simplifier.simplify(tree)


def visualize_plan(plan_id: int) -> str:
    """
    便捷函数：可视化计划的 DAG 结构

    Args:
        plan_id: 计划ID

    Returns:
        可视化字符串
    """
    from ...repository.plan_repository import PlanRepository

    repo = PlanRepository()
    tree = repo.get_plan_tree(plan_id)

    simplifier = TreeSimplifier(use_llm=False)
    dag = simplifier.tree_to_dag(tree)

    return dag.visualize()
