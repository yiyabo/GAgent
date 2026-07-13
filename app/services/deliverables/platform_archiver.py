"""把 session 产出全量同步到平台项目目录下的 agent_results/。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from app.database_pool import get_db
from app.services.session_paths import get_runtime_session_dir
from app.services.sso import get_main_platform_user_id, get_project_context

logger = logging.getLogger(__name__)

_EXCLUDE_ARGS = [
    "--exclude=.cache/",
    "--exclude=__pycache__/",
    "--exclude=*.pyc",
    "--exclude=.env_guard/",
]

_AGENT_RESULTS_DIRNAME = "agent_results"


def archive_session_to_platform(session_id: str) -> bool:
    """Do not materialize platform-owned artifacts through Agent-host paths.

    Platform-origin artifact publishing must use the main platform's versioned
    artifact API.  That API is not available yet, so callers receive a safe
    no-op instead of an implicit filesystem fallback.
    """
    logger.info(
        "[PlatformArchiver] Skipped local filesystem archive for session %s; "
        "platform artifact API is required",
        str(session_id or "").strip() or "<empty>",
    )
    return False


def _legacy_archive_session_to_platform(session_id: str) -> bool:
    """Legacy local filesystem implementation retained only for migration reference."""
    try:
        session_id = str(session_id or "").strip()
        if not session_id:
            return False

        project_id, owner_id, session_name = _lookup_session_project(session_id)
        if project_id is None:
            logger.debug(
                "[PlatformArchiver] Session %s has no project_id; skip", session_id
            )
            return False

        user_id = get_main_platform_user_id(owner_id) if owner_id else None

        project_data = get_project_context(user_id, project_id)
        if not project_data:
            logger.warning(
                "[PlatformArchiver] Project context not found: project_id=%s user_id=%s",
                project_id,
                user_id,
            )
            return False

        data_roots = project_data.get("data_roots") or []
        if not data_roots:
            logger.warning("[PlatformArchiver] No data_roots for project_id=%s", project_id)
            return False

        data_root_path = Path(data_roots[0].get("path", ""))
        if not data_root_path.exists():
            logger.warning(
                "[PlatformArchiver] data_root not accessible: %s", data_root_path
            )
            return False

        agent_results_root = data_root_path / _AGENT_RESULTS_DIRNAME

        session_dir = get_runtime_session_dir(session_id)
        target_dir = agent_results_root / _archive_dirname(session_id, session_name)

        target_dir.mkdir(parents=True, exist_ok=True)

        _rsync_session(session_dir, target_dir)
        _write_meta_json(session_id, target_dir, project_id)
        _fix_permissions(target_dir)

        logger.info(
            "[PlatformArchiver] Archived session %s -> %s (project_id=%s)",
            session_id,
            target_dir,
            project_id,
        )
        return True

    except Exception as e:
        logger.warning(
            "[PlatformArchiver] Failed to archive session %s: %s", session_id, e
        )
        return False


def _lookup_session_project(session_id: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT project_id, owner_id, name FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None, None, None
            return row["project_id"], row["owner_id"], row["name"]
    except Exception as e:
        logger.error("[PlatformArchiver] Failed to lookup session project: %s", e)
        return None, None, None


_UNSAFE_DIRNAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_dirname(name: Optional[str]) -> str:
    if not name:
        return ""
    cleaned = _UNSAFE_DIRNAME_CHARS.sub("_", name).strip().lstrip(".").rstrip(". ")
    return cleaned[:100] if len(cleaned) > 100 else cleaned


def _archive_dirname(session_id: str, session_name: Optional[str]) -> str:
    title = _sanitize_dirname(session_name) or "session"
    stable_id = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"{title[:80]}-{stable_id}"


def _write_meta_json(session_id: str, target_dir: Path, project_id: int) -> None:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, name, created_at, updated_at, plan_title "
                "FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return
        meta = {
            "session_id": row["id"],
            "title": row["name"] or "",
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
            "plan_title": row["plan_title"],
            "project_id": project_id,
        }
        meta_path = target_dir / ".meta.json"
        tmp_path = target_dir / ".meta.json.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, meta_path)
    except Exception as e:
        logger.warning("[PlatformArchiver] Failed to write .meta.json: %s", e)


def _rsync_session(src: Path, dst: Path) -> None:
    src_raw = src / "raw_files"
    if not src_raw.exists():
        return
    dst_raw = dst / "raw_files"
    dst_raw.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-aL",
        "--delete",
        "--exclude=.cache/",
        "--exclude=__pycache__/",
        "--exclude=*.pyc",
        "--chmod=Du+rwx,Dgo+rx,Fu+rw,Fgo+r",
        str(src_raw) + "/",
        str(dst_raw) + "/",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning(
                "[PlatformArchiver] rsync returned %d: %s",
                result.returncode,
                (result.stderr or "")[:500],
            )
            _fallback_copytree(src, dst)
    except FileNotFoundError:
        logger.info("[PlatformArchiver] rsync not found; using copytree fallback")
        _fallback_copytree(src, dst)
    except subprocess.TimeoutExpired:
        logger.warning("[PlatformArchiver] rsync timed out for %s", src)
        _fallback_copytree(src, dst)


def _fallback_copytree(src: Path, dst: Path) -> None:
    src_raw = src / "raw_files"
    if not src_raw.exists():
        return
    dst_raw = dst / "raw_files"
    dst_raw.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(".cache", "__pycache__", "*.pyc", ".env_guard")
    shutil.copytree(src_raw, dst_raw, symlinks=False, ignore=ignore, dirs_exist_ok=True)


def _fix_permissions(target_dir: Path) -> None:
    try:
        for root, dirs, files in os.walk(target_dir):
            for d in dirs:
                (Path(root) / d).chmod(0o755)
            for f in files:
                p = Path(root) / f
                try:
                    p.chmod(0o644)
                except OSError:
                    pass
    except Exception as e:
        logger.warning("[PlatformArchiver] Failed to fix permissions: %s", e)
