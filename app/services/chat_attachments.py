"""Files a user sent with a chat message.

The composer sends the attachment list in ``context["attachments"]``. It is kept on the saved user
message so a reloaded chat still shows which files went with which message, and so later turns can
tell the model where those files are.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

MAX_ATTACHMENTS_PER_MESSAGE = 50
_MAX_FIELD_CHARS = 1024
_FIELDS = ("name", "path", "extracted_path")
_TYPES = {"file", "image"}

HISTORY_ATTACHMENT_HEADER = "\n\n📎 Files the user attached to this message:"


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:_MAX_FIELD_CHARS]


def sanitize_attachments(raw: Any) -> List[Dict[str, str]]:
    """Keep only well-formed entries with a path, in order, without duplicates."""
    if not isinstance(raw, list):
        return []
    cleaned: List[Dict[str, str]] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        path = _clean(item.get("path"))
        if not path or path in seen:
            continue
        seen.add(path)
        entry: Dict[str, str] = {"type": item.get("type") if item.get("type") in _TYPES else "file"}
        for field in _FIELDS:
            value = _clean(item.get(field))
            if value:
                entry[field] = value
        entry.setdefault("name", path.rsplit("/", 1)[-1])
        cleaned.append(entry)
        if len(cleaned) >= MAX_ATTACHMENTS_PER_MESSAGE:
            break
    return cleaned


def attach_message_attachments(metadata: dict, context: Mapping[str, Any] | None) -> None:
    attachments = sanitize_attachments((context or {}).get("attachments"))
    if attachments:
        metadata["attachments"] = attachments


def history_attachment_note(metadata: Mapping[str, Any] | None) -> str:
    """Text appended to an earlier user message so the model still knows its files."""
    attachments = sanitize_attachments((metadata or {}).get("attachments"))
    if not attachments:
        return ""
    lines = [HISTORY_ATTACHMENT_HEADER]
    for att in attachments:
        line = f"- {att['name']}: {att['path']}"
        if att.get("extracted_path"):
            line += f" (extracted: {att['extracted_path']})"
        lines.append(line)
    return "\n".join(lines)
