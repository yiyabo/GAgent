"""Sensitive-keyword moderation engine (log-only audit).

Loads a TC260-taxonomy keyword library into an Aho-Corasick automaton and
scans text for hits. This module NEVER blocks content: it only reports hits
so callers can write audit logs. Any failure (missing library, missing
pyahocorasick, I/O errors) degrades to a no-op so chat traffic is unaffected.

Single-character keywords are skipped entirely: Chinese has no word
boundaries, so substring matching floods the log with false positives
("鸡" in "鸡蛋"), while boundary-checked matching almost never fires.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_MAX_DISTINCT_HITS = 20
_MAX_SCAN_CHARS = 200_000


class ModerationEngine:
    """Aho-Corasick keyword matcher over the sensitive-word library."""

    def __init__(self) -> None:
        self._automaton = None
        self._keyword_count = 0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def keyword_count(self) -> int:
        return self._keyword_count

    def load(self, keywords_path: Path, whitelist_path: Optional[Path] = None) -> None:
        """Build the automaton from a TSV of ``keyword<TAB>risk<TAB>category``."""
        try:
            import ahocorasick  # pyahocorasick, optional dependency
        except ImportError:
            logger.warning(
                "pyahocorasick is not installed; moderation scanning disabled"
            )
            return

        whitelist = self._load_whitelist(whitelist_path)

        automaton = ahocorasick.Automaton()
        count = 0
        skipped_single_char = 0
        with open(keywords_path, "r", encoding="utf-8") as f:
            f.readline()  # header
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if not parts or not parts[0]:
                    continue
                keyword = parts[0]
                risk = parts[1] if len(parts) > 1 else ""
                category = parts[2] if len(parts) > 2 else ""
                if keyword in whitelist:
                    continue
                if len(keyword) == 1:
                    skipped_single_char += 1
                    continue
                automaton.add_word(keyword, (keyword, risk, category))
                count += 1
        automaton.make_automaton()

        self._automaton = automaton
        self._keyword_count = count
        self._ready = True
        logger.info(
            "Moderation engine loaded: %d keywords (%d whitelisted, %d single-char skipped)",
            self._keyword_count,
            len(whitelist),
            skipped_single_char,
        )

    @staticmethod
    def _load_whitelist(whitelist_path: Optional[Path]) -> set:
        if not whitelist_path or not whitelist_path.exists():
            return set()
        words = set()
        try:
            with open(whitelist_path, "r", encoding="utf-8") as f:
                for line in f:
                    word = line.strip()
                    if word and not word.startswith("#"):
                        words.add(word)
        except OSError as exc:
            logger.warning("Failed to read moderation whitelist: %s", exc)
        return words

    def scan(self, text: str) -> List[Dict[str, str]]:
        """Return distinct keyword hits in ``text`` (capped), or an empty list."""
        if not self._ready or not text:
            return []
        if len(text) > _MAX_SCAN_CHARS:
            text = text[:_MAX_SCAN_CHARS]

        hits: Dict[str, Dict[str, str]] = {}
        for _end, (keyword, risk, category) in self._automaton.iter(text):
            if keyword not in hits:
                hits[keyword] = {
                    "keyword": keyword,
                    "risk": risk,
                    "category": category,
                }
                if len(hits) >= _MAX_DISTINCT_HITS:
                    break
        return list(hits.values())


_engine: Optional[ModerationEngine] = None
_engine_lock = threading.Lock()


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else _PROJECT_ROOT / path


def get_engine() -> Optional[ModerationEngine]:
    """Return the loaded engine, building it lazily on first use.

    Returns None when moderation is disabled or the library cannot be loaded.
    """
    global _engine
    if _engine is not None:
        return _engine if _engine.ready else None
    with _engine_lock:
        if _engine is None:
            _engine = ModerationEngine()
            try:
                from app.services.foundation.settings import get_settings

                settings = get_settings()
                if not getattr(settings, "moderation_enabled", False):
                    logger.info("Moderation scanning disabled via MODERATION_ENABLED")
                else:
                    keywords_path = _resolve_path(
                        getattr(
                            settings,
                            "moderation_keywords_path",
                            "data/moderation/keywords.tsv",
                        )
                    )
                    whitelist_path = _resolve_path(
                        getattr(
                            settings,
                            "moderation_whitelist_path",
                            "data/moderation/whitelist.txt",
                        )
                    )
                    if not keywords_path.exists():
                        logger.warning(
                            "Moderation keyword library not found at %s; scanning disabled",
                            keywords_path,
                        )
                    else:
                        _engine.load(keywords_path, whitelist_path)
            except Exception as exc:  # never let moderation break startup/requests
                logger.warning("Moderation engine initialization failed: %s", exc)
    return _engine if _engine.ready else None


def reset_engine() -> None:
    """Drop the cached engine (used by tests and config reloads)."""
    global _engine
    with _engine_lock:
        _engine = None
