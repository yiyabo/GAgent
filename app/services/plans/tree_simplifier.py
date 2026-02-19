"""
- planDAG

, task, (DAG). 
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

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


    PlanTreeDAG, 

    :
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


        Args:
            matcher: , 
            use_llm:  LLM 
            use_cache: 
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
        BFSfrom_idto_id

        Args:
            dag: DAG
            from_id: ID
            to_id: ID

        Returns:

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
        ()

        : 
        1. 
        2. does not exist
        3. does not exist-()
        4. does not exist

        Args:
            dag: DAG
            node1_id: ID
            node2_id: ID

        Returns:
            (, reason)
        """
        node1 = dag.nodes.get(node1_id)
        node2 = dag.nodes.get(node2_id)

        if not node1 or not node2:
            return False, "does not exist"

        if node1_id == node2_id:
            return False, ""

        if node2_id in node1.child_ids or node1_id in node2.child_ids:
            return False, "()"

        if node2_id in node1.parent_ids or node1_id in node2.parent_ids:
            return False, "()"

        if self.is_reachable(dag, node1_id, node2_id):
            return False, f"[{node1_id}][{node2_id}], path"

        if self.is_reachable(dag, node2_id, node1_id):
            return False, f"[{node2_id}][{node1_id}], path"

        if node2_id in node1.dependencies:
            return False, f"[{node1_id}][{node2_id}]"

        if node1_id in node2.dependencies:
            return False, f"[{node2_id}][{node1_id}]"

        return True, "()"

    def find_mergeable_groups(self, dag: DAG) -> List[List[int]]:
        """
        ()

        Returns:
            , 
        """
        node_ids = list(dag.nodes.keys())
        n = len(node_ids)

        reachable = {}
        for i in range(n):
            for j in range(i + 1, n):
                id1, id2 = node_ids[i], node_ids[j]
                can, _ = self.can_merge(dag, id1, id2)
                reachable[(id1, id2)] = can
                reachable[(id2, id1)] = can

        name_groups: Dict[str, List[int]] = {}
        for node_id, node in dag.nodes.items():
            name_key = node.name.strip().lower()
            name_groups.setdefault(name_key, []).append(node_id)

        mergeable_groups = []
        for name, ids in name_groups.items():
            if len(ids) < 2:
                continue

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
        PlanTreeDAG()

        Args:
            tree: inputplan

        Returns:
            DAG
        """
        dag = DAG(
            plan_id=tree.id,
            title=tree.title,
            description=tree.description,
        )

        if not tree.nodes:
            logger.info(f"tree_to_dag: Plan #{tree.id} has no nodes, returning empty DAG")
            return dag

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

        for parent_id, children_ids in tree.adjacency.items():
            if parent_id is None:
                continue
            if parent_id not in dag.nodes:
                continue
            for child_id in children_ids:
                if child_id in dag.nodes:
                    dag.nodes[parent_id].child_ids.add(child_id)
                    dag.nodes[child_id].parent_ids.add(parent_id)

        if len(dag.nodes) == 1:
            logger.info(f"tree_to_dag: Plan #{tree.id} has single node, returning DAG with 1 node")

        return dag

    def merge_nodes(
        self,
        dag: DAG,
        keep_id: int,
        remove_id: int,
        force: bool = False,
    ) -> bool:
        """
        , keep_id, deleteremove_id

        Args:
            dag: DAG
            keep_id: ID
            remove_id: deleteID
            force: 

        Returns:
            success
        """
        if keep_id not in dag.nodes or remove_id not in dag.nodes:
            return False

        if not force:
            can, reason = self.can_merge(dag, keep_id, remove_id)
            if not can:
                logger.warning(f" [{keep_id}]  [{remove_id}]: {reason}")
                return False

        keep_node = dag.nodes[keep_id]
        remove_node = dag.nodes[remove_id]

        keep_node.merge_from(remove_node)

        keep_node.parent_ids.discard(keep_id)
        keep_node.child_ids.discard(keep_id)
        keep_node.parent_ids.discard(remove_id)
        keep_node.child_ids.discard(remove_id)

        for node_id, node in dag.nodes.items():
            if node_id == keep_id or node_id == remove_id:
                continue

            if remove_id in node.parent_ids:
                node.parent_ids.discard(remove_id)
                node.parent_ids.add(keep_id)

            if remove_id in node.child_ids:
                node.child_ids.discard(remove_id)
                node.child_ids.add(keep_id)

            if remove_id in node.dependencies:
                node.dependencies.discard(remove_id)
                node.dependencies.add(keep_id)

        del dag.nodes[remove_id]
        dag.merge_map[remove_id] = keep_id

        return True

    def merge_group(self, dag: DAG, node_ids: List[int]) -> Optional[int]:
        """


        Args:
            dag: DAG
            node_ids: ID

        Returns:
            ID, failedNone
        """
        if len(node_ids) < 2:
            return node_ids[0] if node_ids else None

        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                can, reason = self.can_merge(dag, node_ids[i], node_ids[j])
                if not can:
                    logger.warning(
                        f": [{node_ids[i]}]  [{node_ids[j]}]: {reason}"
                    )
                    return None

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
                f"completed: [{keep_id}]{keep_name[:20]} <- {', '.join(merged_names)}"
            )

        return keep_id

    def simplify(
        self,
        tree: PlanTree,
        max_iterations: int = 100,
    ) -> DAG:
        """
        , , DAG

        Args:
            tree: inputplan
            max_iterations: ()

        Returns:
            DAG
        """
        if not tree.nodes:
            logger.info(f"simplify: Plan #{tree.id} has no nodes, returning empty DAG")
            return self.tree_to_dag(tree)

        if len(tree.nodes) == 1:
            logger.info(f"simplify: Plan #{tree.id} has single node, returning as-is")
            return self.tree_to_dag(tree)

        dag = self.tree_to_dag(tree)

        original_count = dag.node_count()
        logger.info(f" Plan #{tree.id}, : {original_count}")

        for iteration in range(max_iterations):
            nodes = list(dag.nodes.values())
            similar_pairs = self.matcher.find_similar_pairs(nodes)

            if not similar_pairs:
                break

            similar_pairs.sort(key=lambda x: x[2], reverse=True)

            merged = False
            for node_id_1, node_id_2, score in similar_pairs:
                if node_id_1 not in dag.nodes or node_id_2 not in dag.nodes:
                    continue

                node1 = dag.nodes[node_id_1]
                node2 = dag.nodes[node_id_2]

                if self.matcher.should_merge(node1, node2):
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
            f"completed: {original_count} -> {final_count} ,  {merged_count} "
        )

        return dag

    def simplify_fast(self, tree: PlanTree) -> DAG:
        """
        (name,  LLM)

        Args:
            tree: inputplan

        Returns:
            DAG
        """
        dag = self.tree_to_dag(tree)

        mergeable_groups = self.find_mergeable_groups(dag)

        for group in mergeable_groups:
            self.merge_group(dag, group)

        return dag

    def simplify_from_db(self, plan_id: int, repo=None) -> DAG:
        """
        databaseloadplanexecute. 

        Args:
            plan_id: planID
            repo: PlanRepository()

        Returns:
            DAG
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
        DAGsaveplan(Plan). 

        description: 
        - Plan, , 
          dependencies. 
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
        databaseload, , saveplan. 

        Returns:
            (DAG, planIDplanID)
        """
        dag = self.simplify_from_db(plan_id, repo)
        if not dag.merge_map:
            return dag, plan_id
        new_plan_id = self.save_dag_to_db(dag, repo, title_suffix)
        return dag, new_plan_id


def simplify_plan(plan_id: int, use_llm: bool = False) -> DAG:
    """
    : plan

    Args:
        plan_id: planID
        use_llm:  LLM 

    Returns:
        DAG
    """
    from ...repository.plan_repository import PlanRepository

    repo = PlanRepository()
    tree = repo.get_plan_tree(plan_id)

    simplifier = TreeSimplifier(use_llm=use_llm)
    return simplifier.simplify(tree)


def visualize_plan(plan_id: int) -> str:
    """
    : plan DAG 

    Args:
        plan_id: planID

    Returns:

    """
    from ...repository.plan_repository import PlanRepository

    repo = PlanRepository()
    tree = repo.get_plan_tree(plan_id)

    simplifier = TreeSimplifier(use_llm=False)
    dag = simplifier.tree_to_dag(tree)

    return dag.visualize()
