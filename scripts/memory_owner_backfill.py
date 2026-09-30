#!/usr/bin/env python3
"""Backfill memories.owner_id and purge orphan memories (one-off migration).

Context: recall from the global memory store is owner-isolated and fail-closed.
Rows written before owner isolation existed have owner_id NULL and are invisible
to recall until backfilled. Memories whose source session was deleted are purged
(session-delete cascade did not exist before).

Owner resolution order:
  1. ``session:<id>`` tag -> chat_sessions.owner_id
  2. related_task_id -> tasks.session_id -> chat_sessions.owner_id
  3. otherwise left NULL (stays invisible to recall)

Usage:
  python scripts/memory_owner_backfill.py --dry-run
  python scripts/memory_owner_backfill.py
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_SESSION_TAG_RE = re.compile(r"session:(session_[0-9A-Za-z_]+)")


def _session_tag_of(tags_raw: str | None) -> str | None:
    if not tags_raw:
        return None
    match = _SESSION_TAG_RE.search(tags_raw)
    return match.group(1) if match else None


def run(dry_run: bool) -> int:
    from app.database import get_db
    from app.services.memory.memory_service import _ensure_owner_column

    with get_db() as conn:
        _ensure_owner_column(conn)

        session_owner: dict[str, str | None] = {}

        def owner_of_session(session_id: str) -> str | None:
            if session_id not in session_owner:
                row = conn.execute(
                    "SELECT owner_id FROM chat_sessions WHERE id = ?", (session_id,)
                ).fetchone()
                session_owner[session_id] = str(row[0]) if row and row[0] else None
            return session_owner[session_id]

        rows = conn.execute(
            "SELECT id, tags, related_task_id FROM memories WHERE owner_id IS NULL"
        ).fetchall()
        logger.info("memories with NULL owner_id: %s", len(rows))

        backfilled = 0
        orphan_ids: list[str] = []
        unresolved = 0
        for row in rows:
            memory_id = row[0]
            session_id = _session_tag_of(row[1])
            owner_id: str | None = None
            session_missing = False
            if session_id:
                owner_id = owner_of_session(session_id)
                session_missing = owner_id is None
            if owner_id is None and row[2] is not None:
                task_row = conn.execute(
                    "SELECT s.owner_id FROM tasks t JOIN chat_sessions s ON s.id = t.session_id WHERE t.id = ?",
                    (int(row[2]),),
                ).fetchone()
                if task_row and task_row[0]:
                    owner_id = str(task_row[0])
                    session_missing = False
            if owner_id is not None:
                if not dry_run:
                    conn.execute(
                        "UPDATE memories SET owner_id = ? WHERE id = ?",
                        (owner_id, memory_id),
                    )
                backfilled += 1
            elif session_missing:
                orphan_ids.append(memory_id)
            else:
                unresolved += 1

        logger.info(
            "backfill candidates: %s, orphans (session deleted): %s, unresolved: %s",
            backfilled,
            len(orphan_ids),
            unresolved,
        )

        if orphan_ids and not dry_run:
            placeholders = ",".join("?" for _ in orphan_ids)
            conn.execute(
                f"DELETE FROM memory_embeddings WHERE memory_id IN ({placeholders})",
                tuple(orphan_ids),
            )
            conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",
                tuple(orphan_ids),
            )

        if not dry_run:
            conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE owner_id IS NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        logger.info(
            "%stotal memories now: %s, still NULL owner: %s",
            "[DRY-RUN] " if dry_run else "",
            total,
            remaining,
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing anything.",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.dry_run))


if __name__ == "__main__":
    main()
