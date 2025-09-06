#!/usr/bin/env python3
"""
Similarity Calculator Module.

Specialized in vector similarity computation, finding most similar items, and batch similarity comparison.
Extracted from GLMEmbeddingsService to follow the single responsibility principle.
"""

import logging
from typing import Any, Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class SimilarityCalculator:
    """Similarity calculator class specialized in vector similarity computation"""

    def __init__(self):
        """Initialize similarity calculator"""
        logger.info("Similarity calculator initialized")

    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Compute cosine similarity between two vectors

        Args:
            embedding1: First vector
            embedding2: Second vector

        Returns:
            Cosine similarity value (-1 to 1)
        """
        if not embedding1 or not embedding2:
            return 0.0

        if len(embedding1) != len(embedding2):
            logger.warning(f"Embedding dimensions mismatch: {len(embedding1)} vs {len(embedding2)}")
            return 0.0

        try:
            vec1 = np.array(embedding1, dtype=np.float32)
            vec2 = np.array(embedding2, dtype=np.float32)

            # Compute cosine similarity
            dot_product = np.dot(vec1, vec2)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = dot_product / (norm1 * norm2)
            return float(similarity)

        except Exception as e:
            logger.error(f"Similarity computation failed: {e}")
            return 0.0

    def compute_similarities(self, query_embedding: List[float], target_embeddings: List[List[float]]) -> List[float]:
        """
        Compute similarities between query vector and multiple target vectors

        Args:
            query_embedding: Query vector
            target_embeddings: List of target vectors

        Returns:
            List of similarities
        """
        if not query_embedding or not target_embeddings:
            return []

        similarities = []
        for target_embedding in target_embeddings:
            similarity = self.compute_similarity(query_embedding, target_embedding)
            similarities.append(similarity)

        return similarities

    def compute_similarities_batch(
        self, query_embedding: List[float], target_embeddings: List[List[float]]
    ) -> List[float]:
        """
        Batch compute similarities (optimized version)

        Args:
            query_embedding: Query vector
            target_embeddings: List of target vectors

        Returns:
            List of similarities
        """
        if not query_embedding or not target_embeddings:
            return []

        try:
            query_vec = np.array(query_embedding, dtype=np.float32)
            target_matrix = np.array(target_embeddings, dtype=np.float32)

            # Batch compute cosine similarities
            dot_products = np.dot(target_matrix, query_vec)
            query_norm = np.linalg.norm(query_vec)
            target_norms = np.linalg.norm(target_matrix, axis=1)

            # Avoid division by zero
            valid_mask = (target_norms != 0) & (query_norm != 0)
            similarities = np.zeros(len(target_embeddings))

            if query_norm != 0:
                similarities[valid_mask] = dot_products[valid_mask] / (target_norms[valid_mask] * query_norm)

            return similarities.tolist()

        except Exception as e:
            logger.error(f"Batch similarity computation failed: {e}")
            return self.compute_similarities(query_embedding, target_embeddings)

    def find_most_similar(
        self, query_embedding: List[float], candidates: List[Dict[str, Any]], k: int = 5, min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Find most similar candidates

        Args:
            query_embedding: Query vector
            candidates: List of candidates, each containing 'embedding' field
            k: Return top k most similar
            min_similarity: Minimum similarity threshold

        Returns:
            List of candidates sorted by similarity
        """
        if not query_embedding or not candidates:
            return []

        # Extract embeddings
        candidate_embeddings = []
        valid_candidates = []

        for candidate in candidates:
            if "embedding" in candidate and candidate["embedding"]:
                candidate_embeddings.append(candidate["embedding"])
                valid_candidates.append(candidate)

        if not candidate_embeddings:
            logger.warning("No valid embeddings found in candidates")
            return []

        # Compute similarities
        similarities = self.compute_similarities_batch(query_embedding, candidate_embeddings)

        # Add similarity to candidates
        for i, candidate in enumerate(valid_candidates):
            candidate["similarity"] = similarities[i]

        # Filter candidates below threshold
        filtered_candidates = [c for c in valid_candidates if c["similarity"] >= min_similarity]

        # Sort by similarity
        sorted_candidates = sorted(filtered_candidates, key=lambda x: x["similarity"], reverse=True)

        # Return top k
        result = sorted_candidates[:k]

        logger.debug(f"Found {len(result)} most similar items from {len(candidates)} candidates")
        return result

    def find_similar_pairs(self, embeddings: List[List[float]], threshold: float = 0.8) -> List[Tuple[int, int, float]]:
        """
        Find vector pairs with similarity above threshold

        Args:
            embeddings: List of vectors
            threshold: Similarity threshold

        Returns:
            List of similar vector pairs (index1, index2, similarity)
        """
        if not embeddings or len(embeddings) < 2:
            return []

        similar_pairs = []

        try:
            embedding_matrix = np.array(embeddings, dtype=np.float32)

            # Compute similarity matrix for all vector pairs
            norms = np.linalg.norm(embedding_matrix, axis=1)
            normalized_embeddings = embedding_matrix / norms[:, np.newaxis]
            similarity_matrix = np.dot(normalized_embeddings, normalized_embeddings.T)

            # Find similar pairs above threshold
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = similarity_matrix[i, j]
                    if similarity >= threshold:
                        similar_pairs.append((i, j, float(similarity)))

        except Exception as e:
            logger.error(f"Similar pairs computation failed: {e}")
            # Fallback to pairwise computation
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = self.compute_similarity(embeddings[i], embeddings[j])
                    if similarity >= threshold:
                        similar_pairs.append((i, j, similarity))

        # Sort by similarity
        similar_pairs.sort(key=lambda x: x[2], reverse=True)

        logger.debug(f"Found {len(similar_pairs)} similar pairs above threshold {threshold}")
        return similar_pairs

    def compute_centroid(self, embeddings: List[List[float]]) -> List[float]:
        """
        Compute centroid of vector list

        Args:
            embeddings: List of vectors

        Returns:
            Centroid vector
        """
        if not embeddings:
            return []

        try:
            embedding_matrix = np.array(embeddings, dtype=np.float32)
            centroid = np.mean(embedding_matrix, axis=0)
            return centroid.tolist()

        except Exception as e:
            logger.error(f"Centroid computation failed: {e}")
            return []

    def compute_diversity_score(self, embeddings: List[List[float]]) -> float:
        """
        Compute diversity score of vector set

        Args:
            embeddings: List of vectors

        Returns:
            Diversity score (0-1, higher means more diverse)
        """
        if not embeddings or len(embeddings) < 2:
            return 0.0

        try:
            # Compute average similarity of all vector pairs
            total_similarity = 0.0
            pair_count = 0

            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = self.compute_similarity(embeddings[i], embeddings[j])
                    total_similarity += similarity
                    pair_count += 1

            if pair_count == 0:
                return 0.0

            avg_similarity = total_similarity / pair_count
            diversity_score = 1.0 - avg_similarity  # Lower similarity means higher diversity

            return max(0.0, min(1.0, diversity_score))  # Clamp to 0-1 range

        except Exception as e:
            logger.error(f"Diversity score computation failed: {e}")
            return 0.0
