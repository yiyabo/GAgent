"""load_skill tool: on-demand skill retrieval (progressive disclosure).

Skills are discovered by ``app.services.skills.SkillsLoader`` (dual-root scan:
repo ``skills/`` first, ``~/.claude/skills`` overlay). Three levels reach the
model, each only when it asks for it:

1. the name + description index in the chat system prompt
   (``app.services.deep_think.skills_index``);
2. ``load_skill(name)``: the complete SKILL.md plus a ``linked_files`` list;
3. ``load_skill(name, file_path=...)``: one linked file (reference, template,
   script) the instructions point to.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from tool_box.context import ToolContext

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 30_000
_MAX_LISTED_NAMES = 20

# Linked files: text a skill's instructions can point to. Anything else (images,
# archives, binaries) is neither listed nor readable through this tool.
_LINKED_DIRS = ("references", "templates", "assets", "scripts")
_TEXT_SUFFIXES = frozenset(
    {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".py", ".sh", ".r", ".tex", ".toml"}
)
_NOT_LINKED_STEMS = frozenset({"license", "notice", "readme"})
_MAX_LINKED_PER_GROUP = 40
_MAX_FILE_BYTES = 2_000_000

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


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def _is_linkable(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or path.suffix.lower() not in _TEXT_SUFFIXES:
        return False
    if name.lower() == "config.json" or path.stem.lower() in _NOT_LINKED_STEMS:
        return False
    return path.is_file()


def _linked_files(skill_dir: Path) -> Dict[str, List[str]]:
    """Files a skill's instructions may point to, grouped by where they live."""
    root = skill_dir.resolve()
    groups: Dict[str, List[str]] = {}
    for sub in _LINKED_DIRS:
        base = skill_dir / sub
        if not base.is_dir():
            continue
        found = [
            p.relative_to(skill_dir).as_posix()
            for p in sorted(base.rglob("*"))
            if "__pycache__" not in p.parts and _is_linkable(p) and _inside(p, root)
        ]
        if found:
            groups[sub] = found[:_MAX_LINKED_PER_GROUP]
    try:
        loose = [p.name for p in sorted(skill_dir.iterdir()) if p.name != "SKILL.md" and _is_linkable(p) and _inside(p, root)]
    except OSError:
        loose = []
    if loose:
        groups["files"] = loose[:_MAX_LINKED_PER_GROUP]
    return groups


def _resolve_linked_file(skill_dir: Path, file_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Map a model-supplied relative path onto a readable file inside ``skill_dir``.

    Returns ``(path, None)`` or ``(None, error_code)``. The resolved path must stay
    inside the skill directory after symlinks are followed.
    """
    raw = str(file_path or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or "\x00" in raw:
        return None, "invalid_file_path"
    pure = PurePosixPath(raw)
    if pure.is_absolute() or (len(raw) > 1 and raw[1] == ":") or any(part in ("..", "") or part.startswith(".") for part in pure.parts):
        return None, "path_not_allowed"
    try:
        resolved = (skill_dir / pure).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "file_not_found"
    if not resolved.is_relative_to(skill_dir.resolve()):
        return None, "path_not_allowed"
    if not resolved.is_file():
        return None, "file_not_found"
    if not _is_linkable(resolved):
        return None, "file_type_not_allowed"
    return resolved, None


def _load_linked_file(spec: Any, file_path: str, section: Optional[str]) -> Dict[str, Any]:
    skill_dir = Path(spec.directory)
    linked = _linked_files(skill_dir)
    path, error = _resolve_linked_file(skill_dir, file_path)
    if path is None:
        return {
            "success": False,
            "error": f"{error}: {file_path}",
            "linked_files": linked,
            "summary": (
                f"Skill '{spec.name}' has no readable linked file '{file_path}' ({error}). "
                "Pass a path from linked_files, relative to the skill directory."
            ),
        }
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return {
                "success": False,
                "error": f"file_too_large: {file_path}",
                "summary": f"'{file_path}' is larger than {_MAX_FILE_BYTES:,} bytes and cannot be loaded.",
            }
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"success": False, "error": f"file_unreadable: {file_path}", "summary": str(exc)}

    total_chars = len(content)
    section_query = str(section or "").strip()
    if section_query and path.suffix.lower() in {".md", ".markdown"}:
        section_text, headings = _extract_section(content, section_query)
        if section_text is None:
            return {
                "success": False,
                "error": f"section_not_found: {section_query}",
                "available_sections": headings[:_MAX_LISTED_NAMES],
                "summary": f"'{file_path}' has no section matching '{section_query}'.",
            }
        content = section_text
    truncated = len(content) > MAX_CONTENT_CHARS
    if truncated:
        content = content[:MAX_CONTENT_CHARS] + (
            f"\n\n[... truncated at {MAX_CONTENT_CHARS:,} chars — re-call load_skill with 'section' "
            "to fetch one markdown section ...]"
        )
    result: Dict[str, Any] = {
        "success": True,
        "name": spec.name,
        "file_path": path.relative_to(skill_dir.resolve()).as_posix(),
        "content": content,
        "truncated": truncated,
        "content_chars": len(content),
        "total_chars": total_chars,
    }
    if section_query:
        result["section"] = section_query
    return result


async def load_skill_handler(
    name: str,
    section: Optional[str] = None,
    file_path: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Return one skill's SKILL.md (optionally one section), or one of its linked files."""
    skill_name = str(name or "").strip()
    if not skill_name:
        return {
            "success": False,
            "error": "missing_name",
            "summary": "load_skill requires a non-empty 'name'.",
        }

    if skill_name.startswith("learned:"):
        from app.services.skill_learning.context import load_learned_skill
        try:
            result = load_learned_skill(skill_name, getattr(tool_context, "session_id", None),record=False)
            if not result:
                return {"success": False, "error": "invalid_learned_skill_name"}
            if section:
                selected, headings = _extract_section(result["content"], section)
                if selected is None:
                    return {"success": False, "error": "section_not_found", "available_sections": headings}
                result["content"] = selected
            from app.services.chat_run_state import chat_run_claim
            from app.repository import skill_learning as repository
            claim=chat_run_claim.get()
            if claim:repository.record_loaded(result["skill_id"],result["skill_version"],claim[0])
            return result
        except ValueError as exc:
            return {"success": False, "error": "learned_skill_unavailable", "summary": str(exc)}

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

    linked_target = str(file_path or "").strip().replace("\\", "/")
    while linked_target.startswith("./"):
        linked_target = linked_target[2:]
    if linked_target and linked_target != "SKILL.md":
        return _load_linked_file(spec, linked_target, section)

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
    else:
        linked = _linked_files(Path(spec.directory))
        if linked:
            result["linked_files"] = linked
            result["usage_hint"] = (
                "To read a linked file the instructions point to, call "
                "load_skill(name, file_path='references/<file>.md')."
            )
    return result


load_skill_tool = {
    "name": "load_skill",
    "description": (
        "Load a runtime skill on demand (progressive disclosure): a saved, vetted playbook "
        "for a kind of work. The first call, load_skill(name), returns the complete SKILL.md "
        "plus a 'linked_files' list (references, templates, scripts). Open one of those only "
        "when the instructions point to it: load_skill(name, file_path='references/x.md'). "
        "Unknown names return the available skill list. For long files pass 'section' to "
        "fetch one markdown section instead of the whole text."
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
                    "heading). Use it to page through long text that was truncated."
                ),
            },
            "file_path": {
                "type": "string",
                "description": (
                    "Optional path of a linked file inside the skill, taken from linked_files "
                    "(e.g. 'references/api.md'). Omit to get the main SKILL.md."
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
