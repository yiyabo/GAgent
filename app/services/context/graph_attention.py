#!/usr/bin/env python3
"""
Graph Attention Mechanism Reranking Module.

Uses Graph Attention Network (GAT) to rerank semantic retrieval results,
optimizing ranking by analyzing structural information and node features of the task graph.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ...repository.tasks import SqliteTaskRepository

logger = logging.getLogger(__name__)


class GraphAttentionReranker:
    """Graph attention reranker"""

    def __init__(self, repo: Optional[SqliteTaskRepository] = None):
        self.repo = repo or SqliteTaskRepository()

        # Attention mechanism parameters
        self.attention_dim = 64  # Attention vector dimension
        self.num_heads = 4  # Number of multi-head attention
        self.dropout_rate = 0.1  # Dropout rate

        # Weight parameters
        self.relation_weights = {"requires": 1.0, "refers": 0.6, "sibling": 0.4, "parent": 0.7, "child": 0.8}

        # Cache
        self._attention_cache = {}

    def rerank_with_attention(
        self,
        query_task_id: int,
        candidates: List[Dict[str, Any]],
        embeddings: Dict[int, List[float]],
        alpha: float = 0.4,
    ) -> List[Dict[str, Any]]:
        """
        Rerank candidate results using graph attention mechanism

        Args:
            query_task_id: Query task ID
            candidates: List of candidate results, including similarity scores
            embeddings: Mapping from task ID to embedding
            alpha: Impact factor of attention weights

        Returns:
            Reranked list of candidate results
        """
        if not candidates or len(candidates) <= 1:
            return candidates

        try:
            # Build subgraph
            task_ids = [query_task_id] + [c["id"] for c in candidates]
            subgraph = self._build_attention_subgraph(task_ids, embeddings)

            # Calculate attention scores
            attention_scores = self._compute_attention_scores(query_task_id, subgraph)

            # Apply attention weight reranking
            reranked_candidates = self._apply_attention_reranking(candidates, attention_scores, alpha)

            logger.debug(f"Graph attention reranking completed for {len(candidates)} candidates")
            return reranked_candidates

        except Exception as e:
            logger.warning(f"Graph attention reranking failed: {e}, returning original order")
            return candidates

    def _build_attention_subgraph(self, task_ids: List[int], embeddings: Dict[int, List[float]]) -> Dict[str, Any]:
        """Build subgraph for attention computation"""
        # Get task information
        tasks = {}
        for task_id in task_ids:
            try:
                task_info = self.repo.get_task_info(task_id)
                if task_info:
                    tasks[task_id] = task_info
            except Exception as e:
                logger.warning(f"Failed to get task info for {task_id}: {e}")

        # Build adjacency matrix and edge features
        adjacency = self._build_adjacency_matrix(task_ids, tasks)
        edge_features = self._extract_edge_features(task_ids, tasks)

        # Build node feature matrix
        node_features = self._build_node_features(task_ids, tasks, embeddings)

        return {
            "task_ids": task_ids,
            "tasks": tasks,
            "adjacency": adjacency,
            "edge_features": edge_features,
            "node_features": node_features,
        }

    def _build_adjacency_matrix(self, task_ids: List[int], tasks: Dict[int, Dict]) -> np.ndarray:
        """Build adjacency matrix"""
        n = len(task_ids)
        adjacency = np.zeros((n, n), dtype=np.float32)
        id_to_idx = {task_id: i for i, task_id in enumerate(task_ids)}

        # Add dependency relationship edges
        for i, task_id in enumerate(task_ids):
            try:
                dependencies = self.repo.list_dependencies(task_id)
                for dep in dependencies:
                    dep_id = dep["id"]
                    if dep_id in id_to_idx:
                        j = id_to_idx[dep_id]
                        kind = dep["kind"]
                        weight = self.relation_weights.get(kind, 0.5)
                        adjacency[i, j] = weight
                        adjacency[j, i] = weight * 0.8  # Reverse edge weight slightly lower
            except Exception as e:
                logger.warning(f"Failed to get dependencies for task {task_id}: {e}")

        # Add hierarchical relationship edges
        for i, task_id in enumerate(task_ids):
            task = tasks.get(task_id, {})
            parent_id = task.get("parent_id")

            if parent_id and parent_id in id_to_idx:
                j = id_to_idx[parent_id]
                adjacency[i, j] = self.relation_weights["parent"]
                adjacency[j, i] = self.relation_weights["child"]

        # Add sibling relationship edges
        parent_groups = defaultdict(list)
        for task_id in task_ids:
            task = tasks.get(task_id, {})
            parent_id = task.get("parent_id")
            if parent_id:
                parent_groups[parent_id].append(task_id)

        for siblings in parent_groups.values():
            if len(siblings) > 1:
                for i, task_id1 in enumerate(siblings):
                    for j, task_id2 in enumerate(siblings):
                        if i != j and task_id1 in id_to_idx and task_id2 in id_to_idx:
                            idx1, idx2 = id_to_idx[task_id1], id_to_idx[task_id2]
                            adjacency[idx1, idx2] = self.relation_weights["sibling"]

        return adjacency

    def _extract_edge_features(self, task_ids: List[int], tasks: Dict[int, Dict]) -> Dict[Tuple[int, int], Dict]:
        """Extract edge features"""
        edge_features = {}
        id_to_idx = {task_id: i for i, task_id in enumerate(task_ids)}

        for i, task_id in enumerate(task_ids):
            try:
                dependencies = self.repo.list_dependencies(task_id)
                for dep in dependencies:
                    dep_id = dep["id"]
                    if dep_id in id_to_idx:
                        j = id_to_idx[dep_id]
                        edge_features[(i, j)] = {
                            "relation_type": dep["kind"],
                            "weight": self.relation_weights.get(dep["kind"], 0.5),
                            "direction": "dependency",
                        }
            except Exception:
                continue

        return edge_features

    def _build_node_features(
        self, task_ids: List[int], tasks: Dict[int, Dict], embeddings: Dict[int, List[float]]
    ) -> np.ndarray:
        """Build node feature matrix"""
        n = len(task_ids)

        # Get embedding dimension
        embedding_dim = 0
        for task_id in task_ids:
            if task_id in embeddings and embeddings[task_id]:
                embedding_dim = len(embeddings[task_id])
                break

        if embedding_dim == 0:
            embedding_dim = 1024  # Default dimension

        # Build feature matrix
        feature_dim = embedding_dim + 5  # embedding + 5 structural features
        node_features = np.zeros((n, feature_dim), dtype=np.float32)

        for i, task_id in enumerate(task_ids):
            # Embedding features
            if task_id in embeddings and embeddings[task_id]:
                embedding = embeddings[task_id]
                node_features[i, : len(embedding)] = embedding

            # Structural features
            task = tasks.get(task_id, {})

            # Feature 1: Priority (normalized)
            priority = task.get("priority", 100)
            node_features[i, embedding_dim] = min(priority / 100.0, 1.0)

            # Feature 2: Depth (normalized)
            depth = task.get("depth", 0)
            node_features[i, embedding_dim + 1] = min(depth / 10.0, 1.0)

            # Feature 3: Status encoding
            status = task.get("status", "pending")
            status_encoding = {"pending": 0.0, "in_progress": 0.5, "done": 1.0}
            node_features[i, embedding_dim + 2] = status_encoding.get(status, 0.0)

            # Feature 4: Has parent node
            node_features[i, embedding_dim + 3] = 1.0 if task.get("parent_id") else 0.0

            # Feature 5: Task type encoding
            task_type = task.get("task_type", "atomic")
            type_encoding = {"atomic": 0.0, "composite": 1.0}
            node_features[i, embedding_dim + 4] = type_encoding.get(task_type, 0.0)

        return node_features

    def _compute_attention_scores(self, query_task_id: int, subgraph: Dict[str, Any]) -> Dict[int, float]:
        """Compute graph attention scores"""
        task_ids = subgraph["task_ids"]
        adjacency = subgraph["adjacency"]
        node_features = subgraph["node_features"]

        if query_task_id not in task_ids:
            return {task_id: 0.0 for task_id in task_ids}

        query_idx = task_ids.index(query_task_id)
        n = len(task_ids)

        # Simplified multi-head attention mechanism
        attention_scores = {}

        for i, task_id in enumerate(task_ids):
            if i == query_idx:
                attention_scores[task_id] = 1.0  # Query node itself
                continue

            # Calculate attention weight
            attention_weight = self._compute_pairwise_attention(query_idx, i, node_features, adjacency)

            attention_scores[task_id] = attention_weight

        # Normalize attention scores
        max_score = max(attention_scores.values()) if attention_scores else 1.0
        if max_score > 0:
            for task_id in attention_scores:
                attention_scores[task_id] /= max_score

        return attention_scores

    def _compute_pairwise_attention(
        self, query_idx: int, candidate_idx: int, node_features: np.ndarray, adjacency: np.ndarray
    ) -> float:
        """Compute attention weight between two nodes"""
        # Feature similarity
        query_features = node_features[query_idx]
        candidate_features = node_features[candidate_idx]

        # Cosine similarity
        feature_similarity = self._cosine_similarity(query_features, candidate_features)

        # Structural connection strength
        structural_weight = adjacency[query_idx, candidate_idx]

        # Combine attention weight
        attention_weight = 0.7 * feature_similarity + 0.3 * structural_weight

        return max(0.0, attention_weight)

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity"""
        try:
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = np.dot(vec1, vec2) / (norm1 * norm2)
            return float(np.clip(similarity, -1.0, 1.0))
        except Exception:
            return 0.0

    def _apply_attention_reranking(
        self, candidates: List[Dict[str, Any]], attention_scores: Dict[int, float], alpha: float
    ) -> List[Dict[str, Any]]:
        """Apply attention weight reranking to candidate results"""
        reranked_candidates = []

        for candidate in candidates:
            task_id = candidate["id"]
            original_score = candidate.get("similarity", 0.0)
            attention_score = attention_scores.get(task_id, 0.0)

            # Combine original score and attention score
            combined_score = (1 - alpha) * original_score + alpha * attention_score

            reranked_candidate = candidate.copy()
            reranked_candidate["attention_score"] = attention_score
            reranked_candidate["combined_score"] = combined_score
            reranked_candidates.append(reranked_candidate)

        # Sort by combined score
        reranked_candidates.sort(key=lambda x: x["combined_score"], reverse=True)

        return reranked_candidates

    def get_attention_explanation(
        self, query_task_id: int, candidate_task_id: int, embeddings: Dict[int, List[float]]
    ) -> Dict[str, Any]:
        """Get explanation of attention weights"""
        try:
            task_ids = [query_task_id, candidate_task_id]
            subgraph = self._build_attention_subgraph(task_ids, embeddings)

            query_idx = 0
            candidate_idx = 1

            node_features = subgraph["node_features"]
            adjacency = subgraph["adjacency"]

            # Calculate contribution of each component
            feature_similarity = self._cosine_similarity(node_features[query_idx], node_features[candidate_idx])
            structural_weight = adjacency[query_idx, candidate_idx]

            attention_weight = self._compute_pairwise_attention(query_idx, candidate_idx, node_features, adjacency)

            return {
                "query_task_id": query_task_id,
                "candidate_task_id": candidate_task_id,
                "feature_similarity": float(feature_similarity),
                "structural_weight": float(structural_weight),
                "attention_weight": float(attention_weight),
                "explanation": {
                    "feature_contribution": 0.7 * feature_similarity,
                    "structure_contribution": 0.3 * structural_weight,
                },
            }

        except Exception as e:
            logger.error(f"Failed to explain attention for {query_task_id}->{candidate_task_id}: {e}")
            return {"error": str(e)}

    def clear_cache(self):
        """Clear cache"""
        self._attention_cache.clear()
        logger.debug("Graph attention cache cleared")


# Global instance
_graph_attention_reranker: Optional[GraphAttentionReranker] = None


def get_graph_attention_reranker() -> GraphAttentionReranker:
    """Get graph attention reranker singleton"""
    global _graph_attention_reranker
    if _graph_attention_reranker is None:
        _graph_attention_reranker = GraphAttentionReranker()
    return _graph_attention_reranker
