"""Persisted on/off choices made on the extensions page.

Kept free of heavy imports on purpose: the skills loader reads it while scanning.
The file is written atomically so a crash mid-write cannot leave half a file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "extensions_state.json"
STATE_ENV = "EXTENSIONS_STATE_FILE"


def state_path() -> Path:
    explicit = str(os.getenv(STATE_ENV) or "").strip()
    if explicit:
        return Path(explicit)
    return Path(os.getenv("DB_ROOT", "data/databases")) / STATE_FILE_NAME


def _empty() -> Dict[str, Any]:
    return {"mcp": {}, "skills": {}}


def read_state(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        logger.warning("extensions state unreadable (%s); treating everything as default", exc)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    mcp, skills = data.get("mcp"), data.get("skills")
    return {"mcp": dict(mcp) if isinstance(mcp, dict) else {}, "skills": dict(skills) if isinstance(skills, dict) else {}}


def write_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".extensions-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def disabled_skill_names() -> Set[str]:
    return {name for name, entry in (read_state().get("skills") or {}).items() if isinstance(entry, dict) and entry.get("enabled") is False}
