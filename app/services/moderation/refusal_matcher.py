"""Question-bank refusal matcher.

Matches incoming user messages against a bank of known forbidden questions
(``data/moderation/refused_questions_core.tsv``: id<TAB>question<TAB>risk<TAB>category).
A hit means the platform should refuse the topic outright with a fixed
template and without invoking the LLM.

Two matching layers, strict-to-loose:

1. Exact match after light normalization (casefold, strip punctuation /
   whitespace). Zero false positives, catches verbatim replays.
2. Character bigram similarity (Dice coefficient) >= 0.86 against the
   normalized bank entries. Catches minor rewrites while staying far away
   from normal-domain questions. Bigrams are used instead of a full fuzzy
   library to keep the module dependency-free; the matcher is only invoked
   for reasonably-sized chat messages.

The matcher never raises. When the bank file is missing or parsing fails,
matching is disabled and every check returns no-match.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_REFUSAL_BANK_PATH = "data/moderation/refused_questions_core.tsv"
REFUSAL_REPLY_TEMPLATE = "该话题涉及平台不允许的内容，无法继续探讨，请更换话题。"

_SIMILARITY_THRESHOLD = 0.86
_MAX_QUERY_LEN = 500  # ignore absurdly long inputs for fuzzy matching
_MAX_TEXT_CHARS = 1000

_NON_WORD_RE = re.compile(r"[\s\W_]+", flags=re.UNICODE)
_ROUND_PREFIX_RE = re.compile(r"第[0-9一二三四五六七八九十]+轮[：:]")

_matcher: Optional["RefusalMatcher"] = None
_matcher_lock = threading.Lock()


def _normalize(text: str) -> str:
    """Casefold, NFKC-fold, strip punctuation/whitespace for stable comparison."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _ROUND_PREFIX_RE.sub(" ", folded)
    return _NON_WORD_RE.sub("", folded)


def _bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _dice_similarity(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


class RefusalMatcher:
    """Normalized exact + bigram-similarity matcher over the refusal bank."""

    def __init__(self) -> None:
        self._by_norm: Dict[str, Tuple[str, str, str]] = {}  # norm -> (id, risk, category)
        self._norm_entries: List[Tuple[str, set, str]] = []  # (norm, bigrams, id)
        self._ready = False
        self._count = 0

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def count(self) -> int:
        return self._count

    def load(self, bank_path: Path) -> None:
        """Parse the TSV bank. Single-row bank entries with multi-round
        ``第1轮：...`` payloads are split so each round matches separately."""
        try:
            entries = 0
            with open(bank_path, "r", encoding="utf-8") as f:
                header = f.readline()
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 2 or not parts[1].strip():
                        continue
                    qid = parts[0].strip()
                    question = parts[1].strip()
                    risk = parts[2].strip() if len(parts) > 2 else ""
                    category = parts[3].strip() if len(parts) > 3 else ""
                    for round_text in self._split_rounds(question):
                        norm = _normalize(round_text)
                        if not norm:
                            continue
                        self._by_norm[norm] = (qid, risk, category)
                        self._norm_entries.append((norm, _bigrams(norm), qid))
                        entries += 1
            self._count = entries
            self._ready = entries > 0
            if self._ready:
                logger.info(
                    "Refusal matcher loaded: %d bank entries from %s",
                    entries,
                    bank_path,
                )
            else:
                logger.warning("Refusal bank empty at %s; matching disabled", bank_path)
        except OSError as exc:
            logger.warning("Failed to read refusal bank %s: %s", bank_path, exc)
            self._ready = False

    @staticmethod
    def _split_rounds(question: str) -> List[str]:
        """Split a multi-round bank cell into individual turn texts.

        ``第1轮：xxx第2轮：yyy`` -> ["xxx", "yyy"]; a plain question returns
        itself. Keeping the full cell text as well lets a verbatim replay of
        the whole cell match exactly.
        """
        if "第" not in question or "轮" not in question:
            return [question]
        pieces = _ROUND_PREFIX_RE.split(question)
        rounds = [p.strip(" 。.?？!！") for p in pieces if p.strip(" 。.?？!！")]
        if len(rounds) <= 1:
            return [question]
        return [question] + rounds

    def match(self, text: str) -> Optional[Dict[str, str]]:
        """Return hit info when ``text`` matches the bank, else None."""
        if not self._ready or not text:
            return None
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS]
        norm = _normalize(text)
        if not norm:
            return None
        exact = self._by_norm.get(norm)
        if exact is not None:
            return self._hit(exact, "exact", 1.0)
        if len(text) > _MAX_QUERY_LEN:
            return None
        query_grams = _bigrams(norm)
        if not query_grams:
            return None
        best: Tuple[float, str, Tuple[str, str, str]] = (0.0, "", ("", "", ""))
        for entry_norm, entry_grams, qid in self._norm_entries:
            score = _dice_similarity(query_grams, entry_grams)
            if score > best[0]:
                best = (score, entry_norm, self._by_norm.get(entry_norm, ("", "", "")))
                if score == 1.0:
                    break
        if best[0] >= _SIMILARITY_THRESHOLD and best[2][0]:
            return self._hit(best[2], "fuzzy", round(best[0], 4))
        return None

    @staticmethod
    def _hit(entry: Tuple[str, str, str], layer: str, score: float) -> Dict[str, str]:
        qid, risk, category = entry
        return {
            "bank_id": qid,
            "risk": risk,
            "category": category,
            "layer": layer,
            "score": str(score),
        }


def get_refusal_matcher() -> Optional[RefusalMatcher]:
    """Lazily build and cache the singleton matcher (None when disabled)."""
    global _matcher
    if _matcher is not None:
        return _matcher if _matcher.ready else None
    with _matcher_lock:
        if _matcher is None:
            matcher = RefusalMatcher()
            try:
                from app.services.foundation.settings import get_settings

                raw = getattr(
                    get_settings(),
                    "moderation_refusal_bank_path",
                    DEFAULT_REFUSAL_BANK_PATH,
                )
                path = Path(raw)
                if not path.is_absolute():
                    path = _PROJECT_ROOT / path
                if path.exists():
                    matcher.load(path)
                else:
                    logger.info("Refusal bank not found at %s; matcher disabled", path)
            except Exception as exc:  # never let refusal checks break requests
                logger.warning("Refusal matcher initialization failed: %s", exc)
            _matcher = matcher
    return _matcher if _matcher.ready else None


def reset_refusal_matcher() -> None:
    """Drop the cached matcher (tests / config reload)."""
    global _matcher
    with _matcher_lock:
        _matcher = None


def check_refusal_bank(text: Optional[str]) -> Optional[Dict[str, str]]:
    """Public hook for chat routes: return hit info when the message is refused.

    Never raises; returns None when the matcher is disabled or no entry
    matches, so the chat flow continues normally.
    """
    if not text:
        return None
    try:
        matcher = get_refusal_matcher()
        if matcher is None:
            return None
        return matcher.match(text)
    except Exception as exc:  # never let refusal checks break requests
        logger.warning("Refusal bank check failed: %s", exc)
        return None
