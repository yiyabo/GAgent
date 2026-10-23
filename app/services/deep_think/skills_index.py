"""Level 1 of skill progressive disclosure: the skill index in the chat system prompt.

Same idea as Hermes' ``## Skills`` block. The model always sees one line per
skill (name and description, grouped by category) and decides itself which to
open:

1. this index (always in the prompt, small, byte-stable between turns so the
   provider's prompt cache keeps hitting);
2. ``load_skill(name)``: the full SKILL.md plus the ``linked_files`` it can open;
3. ``load_skill(name, file_path=...)``: one reference, template or script.

Skills an admin switched off never appear: the loader already hides them.
Plan-task runs keep their own pre-selected "SKILL GUIDANCE" and do not get the
index (see ``prompts._build_native_system_prompt``).

Env: ``CHAT_SKILLS_INDEX_ENABLED`` (default ``"1"``; ``"0"`` removes the index
and the always-offered ``load_skill`` without a code change).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_FLAG = "CHAT_SKILLS_INDEX_ENABLED"
MAX_DESCRIPTION_CHARS = 280
# Beyond this the descriptions are dropped (names only); entries are never dropped.
MAX_INDEX_CHARS = 9_000

_HEADER = (
    "=== SKILLS ===\n"
    "Skills are saved, vetted playbooks for particular kinds of work: the right tool operations, workflows, "
    "pitfalls, and report or figure conventions. Before replying, scan the list below. If a skill matches or is "
    "even partially relevant to the task, you MUST load it with load_skill(name) and follow it. Err on the side "
    "of loading: a skill holds steps and pitfalls you would otherwise have to rediscover, and it records how "
    "the work should be done here. Load it even when you think basic tools would do. Skip skills only for small "
    "talk and for questions you can answer directly without any tool.\n"
    "- load_skill(name) returns the instructions and a linked_files list. Open a linked file only when the "
    "instructions point to it: load_skill(name, file_path='references/x.md'). Read long text by heading with "
    "section='...'.\n"
    "- A skill is guidance for the tools you already have. A skill that mentions a tool does not make that tool "
    "available.\n"
    "- If a skill you loaded has dropped out of your context (summarized away), load it again before relying on it.\n"
)
_FOOTER = "Only proceed without loading a skill if none is relevant to the task.\n\n"

_SPACES = re.compile(r"\s+")


def index_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "1").strip().lower() not in {"0", "false", "no", "off"}


def _enabled_skills() -> List[Dict[str, Any]]:
    from app.services.skills import get_skills_loader

    return list(get_skills_loader().list_skills())


def _one_line(text: Any, limit: int) -> str:
    flat = _SPACES.sub(" ", str(text or "")).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _group(skills: Iterable[Dict[str, Any]]) -> Dict[str, List[Tuple[str, str]]]:
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    seen = set()
    for row in skills:
        name = str(row.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        category = str(row.get("category") or "").strip() or "general"
        grouped.setdefault(category, []).append((name, _one_line(row.get("description"), MAX_DESCRIPTION_CHARS)))
    return grouped


def _render(grouped: Dict[str, List[Tuple[str, str]]], *, descriptions: bool) -> str:
    lines: List[str] = []
    for category in sorted(grouped):
        lines.append(f"  {category}:")
        for name, description in sorted(grouped[category]):
            lines.append(f"    - {name}: {description}" if descriptions and description else f"    - {name}")
    return "\n".join(lines)


def build_skills_index_block(skills: Optional[Iterable[Dict[str, Any]]] = None) -> str:
    """The ``=== SKILLS ===`` prompt block, or ``""`` when there is nothing to offer."""
    if not index_enabled():
        return ""
    try:
        rows = list(skills) if skills is not None else _enabled_skills()
    except Exception as exc:  # never let a skills problem break the prompt
        logger.warning("Skill index unavailable: %s", exc)
        return ""
    grouped = _group(rows)
    if not grouped:
        return ""
    body = _render(grouped, descriptions=True)
    if len(body) > MAX_INDEX_CHARS:
        body = _render(grouped, descriptions=False)
    return _HEADER + "<available_skills>\n" + body + "\n</available_skills>\n" + _FOOTER


def skills_index_active() -> bool:
    """True when the chat prompt carries an index, so ``load_skill`` must stay offered."""
    if not index_enabled():
        return False
    try:
        return bool(_enabled_skills())
    except Exception:
        return False
