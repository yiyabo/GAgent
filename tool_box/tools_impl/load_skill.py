"""load_skill tool: on-demand full SKILL.md retrieval (progressive disclosure).

Skills are discovered by ``app.services.skills.SkillsLoader`` (dual-root scan:
repo ``skills/`` first, ``~/.claude/skills`` overlay) and normally reach the
context as name+description summaries or budget-trimmed injections. This tool
lets the model pull the COMPLETE body of one named skill when the summary says
it is relevant — large skills no longer need full pre-injection.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from tool_box.context import ToolContext

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 30_000
_MAX_LISTED_NAMES = 20

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$", re.MULTILINE)


def _resolve_loader():
    """Global skills loader (hot-reload aware). Indirection for tests."""
    from app.services.skills import get_skills_loader

    return get_skills_loader()


def _available_names(loader) -> List[str]:
    return sorted(str(info["name"]) for info in loader.list_skills())[:_MAX_LISTED_NAMES]


def _extract_section(content: str, section: str) -> Tuple[Optional[str], List[str]]:
    """Return (section_text, available_headings) for a markdown section query.

    Matches the first heading whose text contains *section* (case-insensitive);
    the section body runs to the next heading of the same or higher level.
    """
    headings = list(_HEADING_RE.finditer(content))
    query = section.strip().lower()
    for index, match in enumerate(headings):
        title = match.group(2).strip()
        if query not in title.lower():
            continue
        level = len(match.group(1))
        body_start = match.end()
        body_end = len(content)
        for following in headings[index + 1:]:
            if len(following.group(1)) <= level:
                body_end = following.start()
                break
        section_text = (match.group(0) + content[body_start:body_end]).strip()
        return section_text, [h.group(2).strip() for h in headings]
    return None, [h.group(2).strip() for h in headings]


async def load_skill_handler(
    name: str,
    section: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Return the full SKILL.md body of one named skill (optionally one section)."""
    skill_name = str(name or "").strip()
    if not skill_name:
        return {
            "success": False,
            "error": "missing_name",
            "summary": "load_skill requires a non-empty 'name'.",
        }

    loader = _resolve_loader()
    spec = loader.get_skill(skill_name)
    if spec is None:
        available = _available_names(loader)
        return {
            "success": False,
            "error": f"skill_not_found: {skill_name}",
            "available_skills": available,
            "summary": (
                f"Skill '{skill_name}' not found. Available skills "
                f"(first {len(available)}): {', '.join(available) or '(none)'}"
            ),
        }

    content = loader.get_skill_body(spec)
    if not content.strip():
        return {
            "success": False,
            "error": f"skill_unreadable: {skill_name}",
            "summary": f"Skill '{skill_name}' exists but its SKILL.md could not be read.",
        }

    total_chars = len(content)
    section_query = str(section or "").strip()
    if section_query:
        section_text, headings = _extract_section(content, section_query)
        if section_text is None:
            return {
                "success": False,
                "error": f"section_not_found: {section_query}",
                "available_sections": headings[:_MAX_LISTED_NAMES],
                "summary": (
                    f"Skill '{skill_name}' has no section matching '{section_query}'. "
                    f"Sections: {', '.join(headings[:_MAX_LISTED_NAMES]) or '(none)'}"
                ),
            }
        content = section_text

    truncated = len(content) > MAX_CONTENT_CHARS
    if truncated:
        content = content[:MAX_CONTENT_CHARS] + (
            f"\n\n[... truncated at {MAX_CONTENT_CHARS:,} chars — re-call load_skill "
            "with the 'section' parameter to fetch a specific markdown section ...]"
        )

    result: Dict[str, Any] = {
        "success": True,
        "name": spec.name,
        "description": spec.description,
        "content": content,
        "truncated": truncated,
        "content_chars": len(content),
        "total_chars": total_chars,
    }
    if section_query:
        result["section"] = section_query
    return result


load_skill_tool = {
    "name": "load_skill",
    "description": (
        "Load the complete SKILL.md of a named runtime skill on demand "
        "(progressive disclosure). Use when the available-skills summary names a "
        "skill relevant to the current task and you need its full instructions "
        "before proceeding. Unknown names return the available skill list. For "
        "large skills, pass 'section' to fetch one markdown section instead of "
        "the whole body."
    ),
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact skill name (as listed in the available-skills summary).",
            },
            "section": {
                "type": "string",
                "description": (
                    "Optional markdown section filter (case-insensitive substring of a "
                    "heading). Use it to page through large skills whose body was truncated."
                ),
            },
        },
        "required": ["name"],
    },
    "handler": load_skill_handler,
    "tags": ["skill", "load", "instructions", "playbook", "context"],
    "examples": [
        "Load the full instructions of the xlsx skill before editing a spreadsheet",
        "Fetch one section of a large skill playbook",
    ],
}
