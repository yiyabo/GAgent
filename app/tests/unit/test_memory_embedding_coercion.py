"""Tests for memory semantic-search embedding dimension guard."""

import json

import pytest

from app.services.memory.memory_service import _coerce_memory_embedding_for_query


def test_coerce_accepts_matching_length() -> None:
    q = [0.1, 0.2, 0.3]
    stored = [0.4, 0.5, 0.6]
    assert _coerce_memory_embedding_for_query(q, stored) == stored
    assert _coerce_memory_embedding_for_query(q, json.dumps(stored)) == stored


@pytest.mark.parametrize(
    "raw",
    [
        [0.1, 0.2],
        [0.1, 0.2, 0.3, 0.4],
        "[]",
        "[0.1]",
        "not-json",
        None,
        {},
    ],
)
def test_coerce_rejects_mismatch_or_invalid(raw) -> None:
    q = [0.0, 0.0, 0.0]
    assert _coerce_memory_embedding_for_query(q, raw) is None


def test_coerce_empty_query() -> None:
    assert _coerce_memory_embedding_for_query([], [1.0, 2.0]) is None
