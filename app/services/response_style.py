from __future__ import annotations

import re

_EMOJI_CHAR_CLASS = r"[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]"
_EMOJI_ONLY_LINE_RE = re.compile(rf"(?m)^\s*{_EMOJI_CHAR_CLASS}(?:\s*{_EMOJI_CHAR_CLASS})*\s*$")
_MARKDOWN_PREFIX_RE = re.compile(r"^(\s*(?:#{1,6}\s*|[-*]\s+|\d+\.\s+)?)(.*)$")
_LEADING_EMOJI_RE = re.compile(rf"^(?:{_EMOJI_CHAR_CLASS}\s*)+")

PROFESSIONAL_STYLE_INSTRUCTION = (
    "Use a professional, calm, and credible tone. "
    "Do not use celebratory, decorative, or playful emojis by default. "
    "Avoid emojis in headings, bullets, tables, labels, and summaries unless the user explicitly asks for a casual style."
)


def sanitize_professional_response_text(text: str | None) -> str:
    """Pass-through: emoji filtering is now handled at the prompt level only.

    Previously this function stripped decorative emoji prefixes and
    emoji-only lines.  That was overly aggressive — the LLM prompt
    already instructs a professional tone, so post-processing removal
    is no longer needed.
    """
    return str(text or "").strip()
