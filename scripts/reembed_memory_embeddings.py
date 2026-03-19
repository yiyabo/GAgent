#!/usr/bin/env python3
"""
Recompute SQLite memory_embeddings rows using the current embedding client.

Run after changing embedding provider/model/dimension (e.g. QWEN_EMBEDDING_MODEL,
QWEN_EMBEDDING_DIM) so semantic search does not mix incompatible vector lengths.

Usage:
  python scripts/reembed_memory_embeddings.py
  python scripts/reembed_memory_embeddings.py --session session_abc123
  python scripts/reembed_memory_embeddings.py --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def _run(session_id: str | None, limit: int | None) -> int:
    from app.services.memory.memory_service import get_memory_service

    svc = get_memory_service()
    stats = await svc.reembed_memory_embeddings(session_id, limit=limit)
    logger.info("Done: %s", stats)
    return 0 if stats.get("errors", 0) == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed memory vectors for current embedding config.")
    parser.add_argument(
        "--session",
        default=None,
        help="Optional session id for session-scoped memory DB; omit for global DB.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of memories to process (for dry runs).",
    )
    args = parser.parse_args()
    code = asyncio.run(_run(args.session, args.limit))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
