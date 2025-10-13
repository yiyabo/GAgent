#!/usr/bin/env python3
"""
Structure Prior Weight Calculation Module.

Calculate structure prior weights based on task graph relationships to enhance semantic retrieval accuracy.
By analyzing dependency relationships between tasks (requires, refers, etc.), provides structured
weight adjustments for retrieval results, allowing tasks with higher relevance to get higher weights.
"""

import logging
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from ...repository.tasks import SqliteTaskRepository

logger = logging.getLogger(__name__)


class StructurePriorCalculator:
    """Structure prior weight calculator"""

    def __init__(self, repo: Optional[SqliteTaskRepository] = None):
        self.repo = repo or SqliteTaskRepository()

        # Weight configuration
        self.weights = {
            "requires": 0.8,  # Strong dependency relationship weight
            "refers": 0.4,  # Weak reference relationship weight
            "sibling": 0.3,  # Sibling node weight
            "parent": 0.5,  # Parent node weight
            "child": 0.6,  # Child node weight
            "distance_decay": 0.1,  # Distance decay factor
        }

        # Cache
        self._graph_cache = {}
        self._weights_cache = {}

    def compute_structure_weights(self, query_task_id: int, candidate_task_ids: List[int]) -> Dict[int, float]:
        """
        Compute structure prior weights between query task and candidate tasks

        Args:
            query_task_id: Query task ID
            candidate_task_ids: List of candidate task IDs

        Returns:
            Dictionary mapping task ID to weight
        """
        if not candidate_task_ids:
            return {}

        # Build task graph
        task_graph = self._build_task_graph([query_task_id] + candidate_task_ids)

        # Calculate various relationship weights
        weights = {}

        for candidate_id in candidate_task_ids:
            if candidate_id == query_task_id:
                weights[candidate_id] = 1.0  # Self weight is highest
                continue

            # Calculate composite weight
            total_weight = self._calculate_relationship_weight(query_task_id, candidate_id, task_graph)

            weights[candidate_id] = max(0.0, min(1.0, total_weight))

        logger.debug(f"Computed structure weights for query {query_task_id}: {weights}")
        return weights

    def _build_task_graph(self, task_ids: List[int]) -> Dict[str, Any]:
        """Build task graph data structure"""
        cache_key = tuple(sorted(task_ids))
        if cache_key in self._graph_cache:
            return self._graph_cache[cache_key]

        # Get basic task information
        tasks = {}
        for task_id in task_ids:
            try:
                task = self.repo.get_task_info(task_id)
                if task:
                    tasks[task_id] = task
            except Exception as e:
                logger.warning(f"Failed to get task {task_id}: {e}")

        # Build dependency relationship graph
        dependencies = defaultdict(list)  # from_id -> [(to_id, kind)]
        reverse_deps = defaultdict(list)  # to_id -> [(from_id, kind)]

        for task_id in task_ids:
            try:
                # Get dependency relationships for this task
                deps = self.repo.list_dependencies(task_id)
                for dep in deps:
                    dep_id = dep["id"]
                    kind = dep["kind"]
                    if dep_id in task_ids:  # Only consider dependencies within candidate task scope
                        dependencies[dep_id].append((task_id, kind))
                        reverse_deps[task_id].append((dep_id, kind))
            except Exception as e:
                logger.warning(f"Failed to get dependencies for task {task_id}: {e}")

        # Build hierarchical relationships
        hierarchy = self._build_hierarchy_relations(tasks)

        graph = {
            "tasks": tasks,
            "dependencies": dict(dependencies),
            "reverse_deps": dict(reverse_deps),
            "hierarchy": hierarchy,
        }

        self._graph_cache[cache_key] = graph
        return graph

    def _build_hierarchy_relations(self, tasks: Dict[int, Dict]) -> Dict[str, Dict[int, List[int]]]:
        """Build hierarchical relationships (parent-child, sibling)"""
        parents = defaultdict(list)  # parent_id -> [child_ids]
        children = defaultdict(list)  # child_id -> [parent_id]
        siblings = defaultdict(list)  # task_id -> [sibling_ids]

        # Group by parent_id
        by_parent = defaultdict(list)
        for task_id, task in tasks.items():
            parent_id = task.get("parent_id")
            if parent_id:
                by_parent[parent_id].append(task_id)
                parents[parent_id].append(task_id)
                children[task_id].append(parent_id)

        # Build sibling relationships
        for parent_id, child_ids in by_parent.items():
            if len(child_ids) > 1:
                for child_id in child_ids:
                    siblings[child_id] = [cid for cid in child_ids if cid != child_id]

        return {"parents": dict(parents), "children": dict(children), "siblings": dict(siblings)}

    def _calculate_relationship_weight(self, query_id: int, candidate_id: int, graph: Dict[str, Any]) -> float:
        """Calculate relationship weight between two tasks"""
        total_weight = 0.0

        # 1. Direct dependency relationship weight
        dep_weight = self._calculate_dependency_weight(query_id, candidate_id, graph)
        total_weight += dep_weight

        # 2. Hierarchical relationship weight
        hierarchy_weight = self._calculate_hierarchy_weight(query_id, candidate_id, graph)
        total_weight += hierarchy_weight

        # 3. Path distance weight
        distance_weight = self._calculate_distance_weight(query_id, candidate_id, graph)
        total_weight += distance_weight

        # 4. Common neighbor weight
        neighbor_weight = self._calculate_neighbor_weight(query_id, candidate_id, graph)
        total_weight += neighbor_weight

        return total_weight

    def _calculate_dependency_weight(self, query_id: int, candidate_id: int, graph: Dict[str, Any]) -> float:
        """Calculate direct dependency relationship weight"""
        weight = 0.0

        dependencies = graph["dependencies"]
        reverse_deps = graph["reverse_deps"]

        # Check query -> candidate dependency
        if query_id in dependencies:
            for dep_id, kind in dependencies[query_id]:
                if dep_id == candidate_id:
                    weight += self.weights.get(kind, 0.0)

        # Check candidate -> query dependency
        if candidate_id in dependencies:
            for dep_id, kind in dependencies[candidate_id]:
                if dep_id == query_id:
                    weight += self.weights.get(kind, 0.0) * 0.8  # Reverse dependency weight slightly lower

        return weight

    def _calculate_hierarchy_weight(self, query_id: int, candidate_id: int, graph: Dict[str, Any]) -> float:
        """Calculate hierarchical relationship weight"""
        weight = 0.0
        hierarchy = graph["hierarchy"]

        # Parent-child relationship
        if query_id in hierarchy["parents"] and candidate_id in hierarchy["parents"][query_id]:
            weight += self.weights["child"]
        elif candidate_id in hierarchy["parents"] and query_id in hierarchy["parents"][candidate_id]:
            weight += self.weights["parent"]

        # Sibling relationship
        if query_id in hierarchy["siblings"] and candidate_id in hierarchy["siblings"][query_id]:
            weight += self.weights["sibling"]

        return weight

    def _calculate_distance_weight(self, query_id: int, candidate_id: int, graph: Dict[str, Any]) -> float:
        """Calculate path distance weight (using BFS)"""
        # Use BFS to calculate shortest path distance
        distance = self._bfs_shortest_path(query_id, candidate_id, graph)

        if distance is None or distance == 0:
            return 0.0

        # Closer distance means higher weight, using exponential decay
        return max(0.0, 1.0 - distance * self.weights["distance_decay"])

    def _bfs_shortest_path(self, start_id: int, target_id: int, graph: Dict[str, Any]) -> Optional[int]:
        """Use BFS to calculate shortest path distance"""
        if start_id == target_id:
            return 0

        visited = set()
        queue = deque([(start_id, 0)])
        visited.add(start_id)

        dependencies = graph["dependencies"]
        reverse_deps = graph["reverse_deps"]
        hierarchy = graph["hierarchy"]

        while queue:
            current_id, distance = queue.popleft()

            # Check all neighbor nodes
            neighbors = set()

            # Dependency relationship neighbors
            if current_id in dependencies:
                neighbors.update(dep_id for dep_id, _ in dependencies[current_id])
            if current_id in reverse_deps:
                neighbors.update(dep_id for dep_id, _ in reverse_deps[current_id])

            # Hierarchical relationship neighbors
            if current_id in hierarchy["parents"]:
                neighbors.update(hierarchy["parents"][current_id])
            if current_id in hierarchy["children"]:
                neighbors.update(hierarchy["children"][current_id])
            if current_id in hierarchy["siblings"]:
                neighbors.update(hierarchy["siblings"][current_id])

            for neighbor_id in neighbors:
                if neighbor_id == target_id:
                    return distance + 1

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, distance + 1))

        return None  # No path connection

    def _calculate_neighbor_weight(self, query_id: int, candidate_id: int, graph: Dict[str, Any]) -> float:
        """Calculate common neighbor weight"""
        # Get neighbor sets of both tasks
        query_neighbors = self._get_neighbors(query_id, graph)
        candidate_neighbors = self._get_neighbors(candidate_id, graph)

        # Calculate common neighbors
        common_neighbors = query_neighbors.intersection(candidate_neighbors)

        if not common_neighbors:
            return 0.0

        # More common neighbors means higher weight
        neighbor_weight = len(common_neighbors) / max(len(query_neighbors), len(candidate_neighbors))
        return neighbor_weight * 0.2  # Common neighbor weight is relatively low

    def _get_neighbors(self, task_id: int, graph: Dict[str, Any]) -> Set[int]:
        """Get all neighbor nodes of a task"""
        neighbors = set()

        dependencies = graph["dependencies"]
        reverse_deps = graph["reverse_deps"]
        hierarchy = graph["hierarchy"]

        # Dependency relationship neighbors
        if task_id in dependencies:
            neighbors.update(dep_id for dep_id, _ in dependencies[task_id])
        if task_id in reverse_deps:
            neighbors.update(dep_id for dep_id, _ in reverse_deps[task_id])

        # Hierarchical relationship neighbors
        if task_id in hierarchy["parents"]:
            neighbors.update(hierarchy["parents"][task_id])
        if task_id in hierarchy["children"]:
            neighbors.update(hierarchy["children"][task_id])
        if task_id in hierarchy["siblings"]:
            neighbors.update(hierarchy["siblings"][task_id])

        return neighbors

    def apply_structure_weights(
        self, semantic_scores: Dict[int, float], structure_weights: Dict[int, float], alpha: float = 0.3
    ) -> Dict[int, float]:
        """
        Apply structure prior weights to semantic similarity scores

        Args:
            semantic_scores: Semantic similarity scores
            structure_weights: Structure prior weights
            alpha: Impact factor of structure weights (0-1)

        Returns:
            Adjusted composite scores
        """
        combined_scores = {}

        for task_id, semantic_score in semantic_scores.items():
            structure_weight = structure_weights.get(task_id, 0.0)

            # Linear combination of semantic scores and structure weights
            combined_score = (1 - alpha) * semantic_score + alpha * structure_weight
            combined_scores[task_id] = combined_score

        return combined_scores

    def get_structure_explanation(self, query_id: int, candidate_id: int) -> Dict[str, Any]:
        """
        Get explanation information for structure weights

        Args:
            query_id: Query task ID
            candidate_id: Candidate task ID

        Returns:
            Dictionary containing weight explanations
        """
        graph = self._build_task_graph([query_id, candidate_id])

        explanation = {"query_task": query_id, "candidate_task": candidate_id, "relationships": [], "total_weight": 0.0}

        # Analyze various relationships
        dep_weight = self._calculate_dependency_weight(query_id, candidate_id, graph)
        if dep_weight > 0:
            explanation["relationships"].append(
                {"type": "dependency", "weight": dep_weight, "description": "Direct dependency relationship"}
            )

        hierarchy_weight = self._calculate_hierarchy_weight(query_id, candidate_id, graph)
        if hierarchy_weight > 0:
            explanation["relationships"].append(
                {
                    "type": "hierarchy",
                    "weight": hierarchy_weight,
                    "description": "Hierarchical relationship (parent-child/sibling)",
                }
            )

        distance_weight = self._calculate_distance_weight(query_id, candidate_id, graph)
        if distance_weight > 0:
            explanation["relationships"].append(
                {"type": "distance", "weight": distance_weight, "description": "Path distance weight"}
            )

        neighbor_weight = self._calculate_neighbor_weight(query_id, candidate_id, graph)
        if neighbor_weight > 0:
            explanation["relationships"].append(
                {"type": "neighbor", "weight": neighbor_weight, "description": "Common neighbor weight"}
            )

        explanation["total_weight"] = sum(r["weight"] for r in explanation["relationships"])

        return explanation

    def clear_cache(self):
        """Clear cache"""
        self._graph_cache.clear()
        self._weights_cache.clear()
        logger.debug("Structure prior cache cleared")


# Global instance
_structure_prior_calculator: Optional[StructurePriorCalculator] = None


def get_structure_prior_calculator() -> StructurePriorCalculator:
    """Get structure prior calculator singleton"""
    global _structure_prior_calculator
    if _structure_prior_calculator is None:
        _structure_prior_calculator = StructurePriorCalculator()
    return _structure_prior_calculator
