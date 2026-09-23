#!/usr/bin/env python3
"""Export the chat-run SSE event contract for the frontend.

Regenerates ``web-ui/src/types/chatRunEvents.schema.json`` (union JSON
Schema) and ``chatRunEvents.canonical.json`` (one valid example per event
type) from the backend registry in ``app/services/chat_run_events.py``.

Run after changing the registry and commit the outputs; the frontend
vitest consumes the canonical examples, so drift breaks a test on the
side that forgot to regenerate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.chat_run_events import (  # noqa: E402
    CANONICAL_EVENT_EXAMPLES,
    chat_run_event_schema,
)

OUT_DIR = REPO_ROOT / "web-ui" / "src" / "types"


def main() -> int:
    schema_path = OUT_DIR / "chatRunEvents.schema.json"
    canonical_path = OUT_DIR / "chatRunEvents.canonical.json"
    schema_path.write_text(
        json.dumps(chat_run_event_schema(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    canonical_path.write_text(
        json.dumps(CANONICAL_EVENT_EXAMPLES, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {schema_path}")
    print(f"wrote {canonical_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
