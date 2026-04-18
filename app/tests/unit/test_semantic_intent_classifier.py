"""Unit tests for SemanticIntentClassifier.

These tests use monkeypatched embedding vectors so they run without
a real Qwen API key or network access.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.intent.anchor_definitions import INTENT_ANCHORS
from app.services.intent.semantic_intent_classifier import (
    SemanticIntentClassifier,
    get_semantic_intent_classifier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embeddings_service(dim: int = 64):
    """Return a lightweight fake that produces deterministic vectors."""
    import hashlib

    class _FakeService:
        def get_embeddings(self, texts):
            return [self.get_single_embedding(t) for t in texts]

        def get_single_embedding(self, text):
            # Deterministic pseudo-random vector derived from text hash
            h = hashlib.sha256(text.encode()).digest()
            rng = np.random.RandomState(int.from_bytes(h[:4], "little"))
            vec = rng.randn(dim).astype(np.float32)
            vec /= np.linalg.norm(vec)
            return vec.tolist()

    return _FakeService()


def _make_classifier(dim: int = 64, **kwargs) -> SemanticIntentClassifier:
    """Create and initialize a classifier with fake embeddings."""
    clf = SemanticIntentClassifier(**kwargs)
    # Patch the import inside initialize() by pre-populating centroids
    _init_with_fake(clf, dim)
    return clf


def _init_with_fake(clf: SemanticIntentClassifier, dim: int = 64) -> None:
    """Manually initialize the classifier with fake centroid vectors."""
    fake_svc = _fake_embeddings_service(dim)

    all_sentences = []
    intent_ranges = []
    offset = 0
    for intent, sentences in INTENT_ANCHORS.items():
        start = offset
        all_sentences.extend(sentences)
        offset += len(sentences)
        intent_ranges.append((intent, start, offset))

    all_vectors = fake_svc.get_embeddings(all_sentences)

    clf._intent_order = []
    centroid_list = []
    for intent, start, end in intent_ranges:
        vecs = np.array(all_vectors[start:end], dtype=np.float32)
        centroid = vecs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        clf._centroids[intent] = centroid
        clf._intent_order.append(intent)
        centroid_list.append(centroid)

    clf._centroid_matrix = np.stack(centroid_list)
    clf._initialized = True


# ---------------------------------------------------------------------------
# Tests: Initialization
# ---------------------------------------------------------------------------

class TestClassifierInitialization:

    def test_initialization_computes_all_centroids(self) -> None:
        clf = _make_classifier(dim=64)
        assert clf.is_initialized is True
        assert len(clf._centroids) == len(INTENT_ANCHORS)
        for intent in INTENT_ANCHORS:
            assert intent in clf._centroids
            assert clf._centroids[intent].shape == (64,)

    def test_centroid_matrix_shape(self) -> None:
        clf = _make_classifier(dim=64)
        assert clf._centroid_matrix is not None
        assert clf._centroid_matrix.shape == (len(INTENT_ANCHORS), 64)

    def test_centroids_are_unit_vectors(self) -> None:
        clf = _make_classifier(dim=64)
        for intent, centroid in clf._centroids.items():
            norm = np.linalg.norm(centroid)
            assert abs(norm - 1.0) < 1e-5, f"{intent} centroid not normalized: norm={norm}"

    def test_uninitialized_classifier_returns_none(self) -> None:
        clf = SemanticIntentClassifier()
        assert clf.is_initialized is False
        intent, score, reasons = clf.classify("test message")
        assert intent is None
        assert "semantic_not_initialized" in reasons


# ---------------------------------------------------------------------------
# Tests: Classification logic
# ---------------------------------------------------------------------------

class TestClassifyLogic:

    def test_classify_returns_tuple_of_three(self, monkeypatch) -> None:
        dim = 64
        clf = _make_classifier(dim=dim)
        fake_svc = _fake_embeddings_service(dim)
        monkeypatch.setattr(
            "app.services.embeddings.get_embeddings_service",
            lambda: fake_svc,
        )
        result = clf.classify("some random text for testing")
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_classify_score_is_between_minus1_and_1(self, monkeypatch) -> None:
        dim = 64
        clf = _make_classifier(dim=dim)
        fake_svc = _fake_embeddings_service(dim)
        monkeypatch.setattr(
            "app.services.embeddings.get_embeddings_service",
            lambda: fake_svc,
        )
        _, score, _ = clf.classify("implement the feature")
        assert -1.0 <= score <= 1.0

    def test_classify_reasons_contain_score(self, monkeypatch) -> None:
        dim = 64
        clf = _make_classifier(dim=dim)
        fake_svc = _fake_embeddings_service(dim)
        monkeypatch.setattr(
            "app.services.embeddings.get_embeddings_service",
            lambda: fake_svc,
        )
        _, _, reasons = clf.classify("some message")
        assert any("semantic_score=" in r for r in reasons)


# ---------------------------------------------------------------------------
# Tests: Threshold and gap heuristics (with controlled vectors)
# ---------------------------------------------------------------------------

class TestThresholdLogic:

    def _make_controlled_classifier(
        self,
        *,
        high_threshold: float = 0.72,
        low_threshold: float = 0.58,
        min_gap: float = 0.08,
    ) -> SemanticIntentClassifier:
        """Create a classifier with 2 intents and known centroid directions."""
        clf = SemanticIntentClassifier(
            high_threshold=high_threshold,
            low_threshold=low_threshold,
            min_gap=min_gap,
        )
        # Two orthogonal centroids in 4D
        clf._centroids = {
            "execute_task": np.array([1, 0, 0, 0], dtype=np.float32),
            "research": np.array([0, 1, 0, 0], dtype=np.float32),
        }
        clf._intent_order = ["execute_task", "research"]
        clf._centroid_matrix = np.stack([
            clf._centroids["execute_task"],
            clf._centroids["research"],
        ])
        clf._initialized = True
        return clf

    def test_high_confidence_accepted(self, monkeypatch) -> None:
        clf = self._make_controlled_classifier(high_threshold=0.70)

        # Message vector very close to execute_task centroid
        msg_vec = np.array([0.95, 0.1, 0.0, 0.0], dtype=np.float32)
        msg_vec /= np.linalg.norm(msg_vec)

        fake_svc = type("FakeSvc", (), {
            "get_single_embedding": lambda self, text: msg_vec.tolist(),
        })()
        monkeypatch.setattr(
            "app.services.embeddings.get_embeddings_service",
            lambda: fake_svc,
        )

        intent, score, reasons = clf.classify("test")
        assert intent == "execute_task"
        assert score >= 0.70
        assert any("semantic_high_confidence" in r for r in reasons)

    def test_gap_rejects_close_scores(self) -> None:
        clf = self._make_controlled_classifier(
            low_threshold=0.40,
            high_threshold=0.90,
            min_gap=0.10,
        )
        # Vector equidistant from both centroids → gap ≈ 0
        msg_vec = np.array([0.707, 0.707, 0.0, 0.0], dtype=np.float32)
        msg_vec /= np.linalg.norm(msg_vec)

        scores = clf._centroid_matrix @ msg_vec
        sorted_scores = np.sort(scores)[::-1]
        gap = float(sorted_scores[0] - sorted_scores[1])
        assert gap < 0.10, f"Gap should be small for equidistant vector, got {gap}"

    def test_gap_accepts_clear_winner(self) -> None:
        clf = self._make_controlled_classifier(
            low_threshold=0.40,
            high_threshold=0.90,
            min_gap=0.08,
        )
        # Vector strongly aligned with execute_task
        msg_vec = np.array([0.95, 0.05, 0.0, 0.0], dtype=np.float32)
        msg_vec /= np.linalg.norm(msg_vec)

        scores = clf._centroid_matrix @ msg_vec
        sorted_scores = np.sort(scores)[::-1]
        gap = float(sorted_scores[0] - sorted_scores[1])
        assert gap >= 0.08, f"Gap should be large for aligned vector, got {gap}"
        assert clf._intent_order[np.argmax(scores)] == "execute_task"

    def test_below_low_threshold_returns_none(self) -> None:
        clf = self._make_controlled_classifier(low_threshold=0.99)
        # No vector can have cosine sim >= 0.99 with any centroid unless identical
        msg_vec = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
        msg_vec /= np.linalg.norm(msg_vec)

        scores = clf._centroid_matrix @ msg_vec
        best_score = float(np.max(scores))
        assert best_score < 0.99


# ---------------------------------------------------------------------------
# Tests: Feature flag integration
# ---------------------------------------------------------------------------

class TestFeatureFlag:

    def test_fallback_returns_none_when_disabled(self, monkeypatch) -> None:
        """When SEMANTIC_INTENT_ENABLED is False, _try_semantic_intent_fallback returns None."""
        from app.routers.chat.request_routing import _try_semantic_intent_fallback

        # Default settings have semantic_intent_enabled=False
        result = _try_semantic_intent_fallback("put together a comprehensive analysis")
        assert result is None

    def test_existing_rule_matches_unaffected(self) -> None:
        """Verify that messages matching existing rules still work identically."""
        from app.routers.chat.request_routing import resolve_intent_type

        # "执行" is in _EXECUTE_PHRASES → should always return execute_task
        intent, reasons = resolve_intent_type(message="帮我执行这个任务")
        assert intent == "execute_task"
        # No semantic reason codes should be present
        assert not any("semantic" in r for r in reasons)

    def test_greeting_still_routes_to_light(self) -> None:
        """Greetings must still get classified correctly by rules."""
        from app.routers.chat.request_routing import resolve_request_routing

        decision = resolve_request_routing(message="你好呀")
        assert decision.request_tier == "light"
        assert decision.capability_floor == "tools"

    def test_research_cue_still_routes_to_research(self) -> None:
        """Research keywords must still be caught by rules."""
        from app.routers.chat.request_routing import resolve_intent_type

        intent, reasons = resolve_intent_type(message="帮我搜索最新的文献")
        assert intent == "research"
        assert "intent_research" in reasons
