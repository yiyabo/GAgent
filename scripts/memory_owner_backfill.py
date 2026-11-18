#!/usr/bin/env python3
"""Inspect or backfill legacy memory owners using an operator-reviewed mapping.

The default command reports counts. Supply --mapping reviewed.json to preview
an assignment and add --apply to commit it. The UTF-8 JSON file is an array of
objects with memory_id and owner_id, verified against independent provenance.
Session and plan tags can be user supplied. Keep rows with unknown provenance
unassigned. Back up the registry before applying a reviewed mapping.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def load_mapping(path: Path) -> dict[str, str]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid assignment JSON at line {exc.lineno}, column {exc.colno}") from exc
    if not isinstance(entries, list):
        raise ValueError("Expected a JSON array of reviewed assignments")
    mapping: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.keys() != {"memory_id", "owner_id"}:
            raise ValueError("Each entry needs exactly memory_id and owner_id")
        memory_id, owner_id = entry["memory_id"], entry["owner_id"]
        if not all(isinstance(value, str) and value.strip() == value and value for value in (memory_id, owner_id)):
            raise ValueError("Expected non-empty string IDs without surrounding whitespace")
        if memory_id in mapping:
            raise ValueError(f"Duplicate memory ID: {memory_id}")
        mapping[memory_id] = owner_id
    return mapping


def backfill(database: Path, mapping: dict[str, str], *, apply: bool) -> tuple[int, int, int]:
    """Return (pending assignments, already assigned, remaining NULL owners)."""
    uri = database.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    pending = already = 0
    with closing(sqlite3.connect(uri, uri=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        for memory_id, owner_id in mapping.items():
            if not conn.execute("SELECT 1 FROM users WHERE id = ?", (owner_id,)).fetchone():
                raise ValueError(f"Unknown user for memory {memory_id}")
            row = conn.execute("SELECT owner_id FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown memory: {memory_id}")
            if row[0] == owner_id:
                already += 1
                continue
            if row[0] is not None:
                raise ValueError(f"Memory already has a different owner: {memory_id}")
            pending += 1
            if apply:
                conn.execute("UPDATE memories SET owner_id = ? WHERE id = ? AND owner_id IS NULL", (owner_id, memory_id))
        remaining = conn.execute("SELECT COUNT(*) FROM memories WHERE owner_id IS NULL").fetchone()[0]
    return pending, already, remaining


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DB_ROOT", "data/databases")) / "main" / "plan_registry.db")
    parser.add_argument("--mapping", type=Path, help="JSON file containing verified memory and user IDs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect only (default)")
    mode.add_argument("--apply", action="store_true", help="Commit all reviewed assignments in one transaction")
    args = parser.parse_args()
    if args.apply and not args.mapping:
        parser.error("--apply requires --mapping")
    try:
        mapping = load_mapping(args.mapping) if args.mapping else {}
        changed, already, remaining = backfill(args.database, mapping, apply=args.apply)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Backfill failed: {exc}\n")
    label = "assigned" if args.apply else "would assign"
    print(f"{label}={changed}, already assigned={already}, currently unowned={remaining}")


if __name__ == "__main__":
    main()
