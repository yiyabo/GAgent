"""Semantic intent classification fallback for request routing."""

from .semantic_intent_classifier import (
    SemanticIntentClassifier,
    get_semantic_intent_classifier,
)

__all__ = [
    "SemanticIntentClassifier",
    "get_semantic_intent_classifier",
]
