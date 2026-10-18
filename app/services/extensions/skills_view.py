"""Skills side of the extensions page: catalog provenance + scanner findings + on/off state."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .service import ExtensionError
from .state import read_state, write_state

logger = logging.getLogger(__name__)

PROJECT_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"
CATALOG_FILE = "CATALOG.json"

_UNKNOWN_ORIGIN = {
    "kind": "internal",
    "source": "unknown",
    "license": "unknown",
    "commit": None,
    "modified": False,
    "modifications": [],
}
_UNKNOWN_REVIEW = {
    "scanner": "n/a",
    "reviewed_by": "",
    "reviewed_at": "",
    "human_signoff": False,
    "notes": "目录中没有这个技能的审查记录",
}


def _load_catalog(skills_dir: Path) -> Dict[str, Any]:
    try:
        data = json.loads((skills_dir / CATALOG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = data.get("skills") if isinstance(data, dict) else None
    return skills if isinstance(skills, dict) else {}


def _scan(skill_dir: Path) -> Dict[str, Any]:
    """Run the static vetting scanner; a missing or failing scanner must not break the page."""
    try:
        from app.services.skills.vetting import scan_skill_dir
    except ImportError:
        return {"status": None, "findings": [], "stats": {}}
    try:
        return scan_skill_dir(skill_dir)
    except Exception as exc:
        logger.warning("skill scan failed for %s: %s", skill_dir, exc)
        return {"status": None, "findings": [], "stats": {}}


def _describe(name: str, skill_dir: Path, spec_description: str, entry: Optional[Dict[str, Any]], enabled: bool) -> Dict[str, Any]:
    entry = entry or {}
    origin = {**_UNKNOWN_ORIGIN, **(entry.get("origin") or {})}
    origin["modified"] = bool(entry.get("modified", origin.get("modified", False)))
    origin["modifications"] = list(entry.get("modifications") or origin.get("modifications") or [])
    review = {**_UNKNOWN_REVIEW, **(entry.get("review") or {})}
    scan = _scan(skill_dir)
    stats = scan.get("stats") or {}
    status = scan.get("status")
    if status in {"passed", "warnings", "blocked"}:
        review["scanner"] = status  # the live scan beats a stale recorded verdict
    try:
        size = (skill_dir / "SKILL.md").stat().st_size
    except OSError:
        size = 0
    references = sum(1 for p in (skill_dir / "references").rglob("*") if p.is_file()) if (skill_dir / "references").is_dir() else 0
    return {
        "name": name,
        "description": spec_description,
        "origin": origin,
        "review": review,
        "findings": list(scan.get("findings") or []),
        "enabled": enabled,
        "size_chars": int(stats.get("chars") or size),
        "scripts": int(stats.get("scripts") or 0),
        "references": references,
    }


def list_skills(*, can_manage: bool, skills_dir: Path = PROJECT_SKILLS_DIR) -> Dict[str, Any]:
    from app.services.skills.skills_loader import SkillsLoader

    catalog = _load_catalog(skills_dir)
    disabled = {n for n, e in (read_state().get("skills") or {}).items() if isinstance(e, dict) and e.get("enabled") is False}
    # Same directory for both roots, so the loader scans it once and does not warn about a missing overlay.
    loader = SkillsLoader(skills_dir=str(skills_dir), project_skills_dir=str(skills_dir))
    rows: List[Dict[str, Any]] = []
    seen = set()
    for spec in loader.list_skills():
        seen.add(spec["name"])
        rows.append(_describe(spec["name"], Path(spec["directory"]), spec["description"], catalog.get(spec["name"]), spec["name"] not in disabled))
    for name in sorted(disabled - seen):
        directory = skills_dir / name
        skill_md = directory / "SKILL.md"
        if skill_md.exists():
            # The loader hides admin-disabled skills; read their description ourselves.
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            description = loader._extract_description(text, loader._extract_frontmatter(text))
            rows.append(_describe(name, directory, description, catalog.get(name), False))
    rows.sort(key=lambda row: row["name"])
    return {"can_manage": can_manage, "skills": rows}


def set_skill_enabled(name: str, enabled: bool, *, skills_dir: Path = PROJECT_SKILLS_DIR) -> Dict[str, Any]:
    directory = skills_dir / name
    if not name or "/" in name or name.startswith(".") or not (directory / "SKILL.md").is_file():
        raise ExtensionError(404, f"未知的技能: {name}")
    if enabled:
        scan = _scan(directory)
        if scan.get("status") == "blocked":
            raise ExtensionError(409, "扫描器在这个技能里发现了被禁止的内容,不能启用")
    state = read_state()
    state.setdefault("skills", {})[name] = {"enabled": enabled}
    write_state(state)
    try:
        from app.services.skills.skills_loader import get_skills_loader

        get_skills_loader().rescan()
    except Exception as exc:
        logger.warning("skill loader rescan failed after toggling %s: %s", name, exc)
    for row in list_skills(can_manage=True, skills_dir=skills_dir)["skills"]:
        if row["name"] == name:
            return row
    raise ExtensionError(404, f"未知的技能: {name}")
