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
    """Remove decorative emoji prefixes while preserving the main text content."""
    raw = str(text or "")
    if not raw.strip():
        return raw

    cleaned_lines = []
    for line in raw.splitlines():
        match = _MARKDOWN_PREFIX_RE.match(line)
        if not match:
            cleaned_lines.append(line)
            continue
        prefix, remainder = match.groups()
        stripped_remainder = _LEADING_EMOJI_RE.sub("", remainder).lstrip()
        cleaned_lines.append(f"{prefix}{stripped_remainder}".rstrip())

    cleaned = "\n".join(cleaned_lines)
    cleaned = _EMOJI_ONLY_LINE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
