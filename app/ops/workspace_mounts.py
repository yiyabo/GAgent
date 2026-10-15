"""One filesystem view for session-scoped execution tools."""
from pathlib import Path

from app.services.session_paths import get_runtime_session_dir
from .sandbox import require_translatable


def session_mounts(workdir: str, session_id: str) -> list[tuple[str, str]]:
    """Read existing session materials; write only to this tool's workspace.

    Parent and child bind mounts deliberately overlap. Docker mounts the child
    workspace read/write while keeping upstream task files read-only.
    """
    workspace = Path(workdir).resolve()
    require_translatable(str(workspace))
    session = get_runtime_session_dir(session_id).resolve() if session_id else None
    mounts = []
    if session is not None and session != workspace and session.is_dir():
        require_translatable(str(session))
        mounts.append((str(session), "ro"))
    mounts.append((str(workspace), "rw"))
    return mounts
