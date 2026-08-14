"""Content moderation audit hooks (log-only).

Public API used by chat routes and the LLM service:

- ``scan_user_input``  -- audit user-provided chat text
- ``scan_llm_output``  -- audit text produced by the LLM
- ``prewarm_moderation`` -- build the keyword engine during app startup

All functions are fire-and-forget: they never raise and never block content.
Hits are appended as JSON lines to the moderation audit log.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import List, Optional

from .engine import _PROJECT_ROOT, get_engine

logger = logging.getLogger(__name__)

_TEXT_EXCERPT_LEN = 120

_audit_logger: Optional[logging.Logger] = None
_audit_lock = threading.Lock()


def _get_audit_logger() -> logging.Logger:
    """Dedicated JSON-lines audit logger (logs/moderation.log by default)."""
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger
    with _audit_lock:
        if _audit_logger is None:
            audit = logging.getLogger("moderation.audit")
            audit.setLevel(logging.INFO)
            audit.propagate = False
            if not audit.handlers:
                try:
                    from app.services.foundation.settings import get_settings

                    raw = getattr(
                        get_settings(), "moderation_log_file", "logs/moderation.log"
                    )
                except Exception:
                    raw = "logs/moderation.log"
                from pathlib import Path

                path = Path(raw)
                if not path.is_absolute():
                    path = _PROJECT_ROOT / path
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                audit.addHandler(handler)
            _audit_logger = audit
    return _audit_logger


def _audit(source: str, text: str, **context) -> None:
    engine = get_engine()
    if engine is None:
        return
    hits = engine.scan(text)
    if not hits:
        return
    record = {
        "ts": round(time.time(), 3),
        "source": source,
        "hits": hits,
        "hit_count": len(hits),
        "excerpt": text[:_TEXT_EXCERPT_LEN],
    }
    record.update({k: v for k, v in context.items() if v is not None})
    try:
        _get_audit_logger().info(json.dumps(record, ensure_ascii=False))
    except Exception as exc:
        logger.warning("Failed to write moderation audit log: %s", exc)


def scan_user_input(
    text: Optional[str],
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Audit user-provided chat text. Never raises, never blocks."""
    if not text:
        return
    try:
        _audit("input", text, session_id=session_id, user_id=user_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Moderation input scan failed: %s", exc)


def scan_llm_output(
    text: Optional[str],
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """Audit LLM-generated text. Never raises, never blocks."""
    if not text:
        return
    try:
        _audit("output", text, provider=provider, model=model)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Moderation output scan failed: %s", exc)


def prewarm_moderation() -> bool:
    """Build the keyword engine at startup; returns True when ready."""
    try:
        engine = get_engine()
        return engine is not None and engine.ready
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Moderation pre-warm failed: %s", exc)
        return False
