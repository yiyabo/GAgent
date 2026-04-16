"""Semantic intent classifier using embedding similarity.

This module provides a fallback intent classifier that uses cosine similarity
between message embeddings and pre-computed intent centroid vectors.  It is
only consulted when the deterministic rule engine in ``request_routing.py``
fails to match any phrase list and would otherwise return ``"chat"``.

The classifier is **disabled by default** (``SEMANTIC_INTENT_ENABLED=False``)
and can be enabled via environment variable for gradual rollout.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.routers.chat.request_routing import IntentType

from .anchor_definitions import INTENT_ANCHORS

logger = logging.getLogger(__name__)

class SemanticIntentClassifier:
    """Embedding-based intent classifier with dual-threshold + gap logic.

    Lifecycle
    ---------
    1. ``__init__(settings)`` — stores config, no I/O.
    2. ``initialize()``       — embeds anchor sentences, computes centroids.
    3. ``classify(message)``  — hot path, returns intent or ``None``.
    """

    def __init__(
        self,
        *,
        high_threshold: float = 0.72,
        low_threshold: float = 0.58,
        min_gap: float = 0.08,
    ) -> None:
        self._high_threshold = high_threshold
        self._low_threshold = low_threshold
        self._min_gap = min_gap

        # Populated by initialize()
        self._centroids: Dict[str, np.ndarray] = {}
        self._centroid_matrix: Optional[np.ndarray] = None  # (n_intents, dim)
        self._intent_order: List[str] = []
        self._initialized = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Embed all anchor sentences and compute per-intent centroids.

        Uses the project's existing ``GLMEmbeddingsService`` which routes
        through the Qwen embedding API (with LRU + SQLite cache) so repeated
        startups are essentially free.
        """
        if self._initialized:
            return

        try:
            from app.services.embeddings import get_embeddings_service

            svc = get_embeddings_service()
        except Exception as exc:
            logger.warning("SemanticIntentClassifier: embedding service unavailable, skipping init: %s", exc)
            return

        all_sentences: List[str] = []
        intent_ranges: List[Tuple[str, int, int]] = []  # (intent, start, end)
        offset = 0
        for intent, sentences in INTENT_ANCHORS.items():
            start = offset
            all_sentences.extend(sentences)
            offset += len(sentences)
            intent_ranges.append((intent, start, offset))

        try:
            all_vectors = svc.get_embeddings(all_sentences)
        except Exception as exc:
            logger.warning("SemanticIntentClassifier: failed to embed anchors: %s", exc)
            return

        if not all_vectors or len(all_vectors) != len(all_sentences):
            logger.warning(
                "SemanticIntentClassifier: embedding count mismatch (%d vs %d), skipping",
                len(all_vectors) if all_vectors else 0,
                len(all_sentences),
            )
            return

        self._intent_order = []
        centroid_list: List[np.ndarray] = []
        for intent, start, end in intent_ranges:
            vecs = np.array(all_vectors[start:end], dtype=np.float32)
            centroid = vecs.mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            self._centroids[intent] = centroid
            self._intent_order.append(intent)
            centroid_list.append(centroid)

        self._centroid_matrix = np.stack(centroid_list)  # (n_intents, dim)
        self._initialized = True

        logger.info(
            "SemanticIntentClassifier initialized: %d intents, dim=%d",
            len(self._intent_order),
            self._centroid_matrix.shape[1],
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self, message: str) -> Tuple[Optional[str], float, List[str]]:
        """Classify *message* by cosine similarity to intent centroids.

        Returns
        -------
        (intent_or_none, best_score, reason_codes)
            *intent_or_none* is ``None`` when the classifier cannot decide
            with sufficient confidence — the caller should fall back to
            ``"chat"`` as before.
        """
        if not self._initialized or self._centroid_matrix is None:
            return None, 0.0, ["semantic_not_initialized"]

        try:
            from app.services.embeddings import get_embeddings_service

            svc = get_embeddings_service()
            msg_vec = svc.get_single_embedding(message)
        except Exception as exc:
            logger.debug("SemanticIntentClassifier: embedding failed for message: %s", exc)
            return None, 0.0, ["semantic_embedding_error"]

        if not msg_vec:
            return None, 0.0, ["semantic_empty_embedding"]

        vec = np.array(msg_vec, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        # Cosine similarity with all centroids at once
        scores = self._centroid_matrix @ vec  # (n_intents,)

        sorted_indices = np.argsort(scores)[::-1]
        best_idx = sorted_indices[0]
        best_score = float(scores[best_idx])
        best_intent = self._intent_order[best_idx]

        second_score = float(scores[sorted_indices[1]]) if len(sorted_indices) > 1 else 0.0
        gap = best_score - second_score

        reasons: List[str] = [f"semantic_score={best_score:.3f}"]

        if best_score >= self._high_threshold:
            reasons.append("semantic_high_confidence")
            return best_intent, best_score, reasons

        if best_score >= self._low_threshold and gap >= self._min_gap:
            reasons.append(f"semantic_gap_confidence(gap={gap:.3f})")
            return best_intent, best_score, reasons

        # Not confident enough — abstain
        reasons.append("semantic_below_threshold")
        return None, best_score, reasons

    @property
    def is_initialized(self) -> bool:
        return self._initialized


# ======================================================================
# Module-level singleton
# ======================================================================

_classifier_instance: Optional[SemanticIntentClassifier] = None


def get_semantic_intent_classifier() -> SemanticIntentClassifier:
    """Return the module-level singleton, creating it lazily if needed."""
    global _classifier_instance
    if _classifier_instance is None:
        from app.services.foundation.settings import get_settings

        settings = get_settings()
        _classifier_instance = SemanticIntentClassifier(
            high_threshold=settings.semantic_intent_high_threshold,
            low_threshold=settings.semantic_intent_low_threshold,
            min_gap=settings.semantic_intent_min_gap,
        )
    return _classifier_instance
