"""An unowned memory must never be assigned or removed on the strength of a tag."""

import json
import sqlite3
from contextlib import closing

import pytest

from scripts.memory_owner_backfill import backfill, load_mapping


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE users(id TEXT PRIMARY KEY);
            CREATE TABLE memories(id TEXT PRIMARY KEY, owner_id TEXT, tags TEXT);
            CREATE TABLE memory_embeddings(memory_id TEXT PRIMARY KEY);
            INSERT INTO users VALUES ('alice'), ('bob');
            INSERT INTO memories VALUES ('verified', NULL, '["session:session_A"]'),
                ('unknown', NULL, '["session:session_deleted"]'), ('existing', 'bob', '[]');
            INSERT INTO memory_embeddings SELECT id FROM memories;
        """)
    return path


def snapshot(path):
    with closing(sqlite3.connect(path)) as conn:
        return (
            conn.execute("SELECT id, owner_id FROM memories ORDER BY id").fetchall(),
            conn.execute("SELECT memory_id FROM memory_embeddings ORDER BY memory_id").fetchall(),
        )


def test_backfill_only_assigns_verified_rows(registry):
    original, embeddings = snapshot(registry)
    mapping = {"verified": "alice"}
    assert backfill(registry, {}, apply=False) == (0, 0, 2)
    assert backfill(registry, mapping, apply=False) == (1, 0, 2)
    assert snapshot(registry) == (original, embeddings)
    assert backfill(registry, mapping, apply=True) == (1, 0, 1)
    expected = ([("existing", "bob"), ("unknown", None), ("verified", "alice")], embeddings)
    assert snapshot(registry) == expected
    assert backfill(registry, mapping, apply=True) == (0, 1, 1)
    assert snapshot(registry) == expected


@pytest.mark.parametrize("invalid", [{"existing": "alice"}, {"absent": "alice"}, {"unknown": "missing"}])
def test_invalid_second_assignment_rolls_back(registry, invalid):
    original = snapshot(registry)
    with pytest.raises(ValueError):
        backfill(registry, {"verified": "alice", **invalid}, apply=True)
    assert snapshot(registry) == original


def test_reject_duplicate_mapping_ids(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps([{"memory_id": "x", "owner_id": "alice"}] * 2), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate memory ID"):
        load_mapping(path)
