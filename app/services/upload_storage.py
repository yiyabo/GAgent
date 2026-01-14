"""Helpers for managing uploaded session files."""

from __future__ import annotations

import shutil
from pathlib import Path

# Use absolute path to ensure correct resolution on server
UPLOAD_BASE_DIR = (Path(__file__).parent.parent.parent / "data" / "information_sessions").resolve()


def get_session_root_dir(session_id: str) -> Path:
    return UPLOAD_BASE_DIR / f"session-{session_id}"


def ensure_session_dir(session_id: str) -> Path:
    session_dir = get_session_root_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def delete_session_storage(session_id: str) -> bool:
    session_dir = get_session_root_dir(session_id)
    if not session_dir.exists():
        return False
    shutil.rmtree(session_dir)
    return True
