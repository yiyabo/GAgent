"""Authorization and path boundaries for the HTTP tool gateway."""

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from app.database import get_db, plan_db_connection
from app.repository.plan_storage import get_system_job_db_path
from app.services.request_principal import ensure_owner_access, require_authenticated_principal
from app.services.session_paths import get_session_storage_candidates, normalize_session_base


def owned_session(request: Request, session_id: Any) -> str:
    require_authenticated_principal(request)
    if not isinstance(session_id, str) or not normalize_session_base(session_id):
        raise HTTPException(400, "session_id is required")
    from app.routers.upload_routes import _ensure_session_access

    session_id = session_id.strip()
    _ensure_session_access(session_id, request)
    return session_id


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def bio_paths(session_id: str, input_file: Any, output_file: Any, params: Any):
    from tool_box.bio_tools.bio_tools_handler import (
        DATABASE_MOUNT_CONFIG, INPUT_PATH_PARAM_KEYS, OUTPUT_PATH_PARAM_KEYS,
    )

    roots = get_session_storage_candidates(session_id)
    shared = [Path(os.getenv("DB_ROOT", "data/databases")) / "bio_tools"]
    shared.extend(Path(os.getenv(key, default)) for key, default in DATABASE_MOUNT_CONFIG.values())
    shared = [path.expanduser().resolve() for path in shared]

    def checked(value: str, *, read: bool) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = roots[0] / path
        path = path.resolve()
        allowed = roots + shared if read else roots
        if not _inside(path, allowed) or (read and not _inside(path.parent, allowed)):
            raise HTTPException(403, "tool path is outside the session")
        return str(path)

    if input_file:
        if not isinstance(input_file, str):
            raise HTTPException(400, "input_file must be a path")
        input_file = checked(input_file, read=True)
    if output_file:
        if not isinstance(output_file, str):
            raise HTTPException(400, "output_file must be a path")
        checked(output_file, read=False)

    def check_params(value, *, read=True, path_value=False):
        if isinstance(value, dict):
            return {
                key: check_params(item, read=key not in OUTPUT_PATH_PARAM_KEYS, path_value=key in INPUT_PATH_PARAM_KEYS)
                for key, item in value.items()
            }
        elif isinstance(value, (tuple, list)):
            return [check_params(item, read=read, path_value=path_value) for item in value]
        elif isinstance(value, str):
            parts = []
            for part in value.split(","):
                part = part.strip()
                if ".." in Path(part).parts:
                    raise HTTPException(403, "Relative tool paths cannot traverse parent directories")
                if part and (path_value or part.startswith(("/", "~"))):
                    resolved = checked(part, read=read)
                    if path_value:
                        part = resolved
                parts.append(part)
            if path_value:
                return ",".join(parts)
        return value

    return input_file, check_params(params)


def owned_bio_job(request: Request, job_id: str) -> None:
    from tool_box.bio_tools.bio_tools_handler import _resolve_bio_tools_job_payload

    require_authenticated_principal(request)
    job = _resolve_bio_tools_job_payload(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    owner = job.get("owner_id")
    if owner and owner != "legacy-local":
        ensure_owner_access(request, owner, detail="job owner mismatch")
        return
    session_id = job.get("session_id")
    if not session_id:
        raise HTTPException(403, "Job ownership is unavailable")
    with get_db() as conn:
        row = conn.execute("SELECT owner_id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(403, "Job ownership is unavailable")
    owned_session(request, session_id)


def bind_http_bio_job(request: Request, session_id: str, result: dict) -> None:
    if not result.get("background") or not result.get("job_id"):
        return
    from app.services.plans.decomposition_jobs import plan_decomposition_jobs

    principal = require_authenticated_principal(request)
    job = plan_decomposition_jobs.get_job(str(result["job_id"]))
    if job is None:
        raise HTTPException(500, "Background job was not registered")
    # Background bio jobs are created without an owner by the internal handler.
    with plan_decomposition_jobs._lock:
        job.owner_id = principal.owner_id
        job.session_id = session_id
        with plan_db_connection(get_system_job_db_path()) as conn:
            conn.execute(
                "UPDATE decomposition_jobs SET owner_id=?, session_id=? WHERE job_id=?",
                (principal.owner_id, session_id, job.job_id),
            )
