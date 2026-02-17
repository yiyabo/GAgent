"""Helpers for managing uploaded session files."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.services.session_paths import (
    get_runtime_session_dir,
    get_session_storage_candidates,
)


def get_session_root_dir(session_id: str) -> Path:
    return get_runtime_session_dir(session_id, create=False)


def ensure_session_dir(session_id: str) -> Path:
    return get_runtime_session_dir(session_id, create=True)


def delete_session_storage(session_id: str) -> bool:
    deleted = False
    for session_dir in get_session_storage_candidates(session_id, include_legacy=True):
        if not session_dir.exists():
            continue
        shutil.rmtree(session_dir)
        deleted = True
    return deleted
