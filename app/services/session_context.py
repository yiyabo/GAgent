"""Utilities to manage chat session context."""
from typing import Optional

from ..database_pool import get_db
from ..repository.tasks import default_repo


def update_session_context(
    session_id: str,
    plan_title: Optional[str] = None,
    task_id: Optional[int] = None,
    task_name: Optional[str] = None,
) -> None:
    """Update chat session context fields in the database."""
    if not session_id:
        return

    updates = []
    params = []

    if plan_title is not None:
        updates.append("current_plan_title = ?")
        params.append(plan_title)
    if task_id is not None:
        updates.append("current_task_id = ?")
        params.append(task_id)
    if task_name is not None:
        updates.append("current_task_name = ?")
        params.append(task_name)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")

    with get_db() as conn:
        conn.execute(
            f"UPDATE chat_sessions SET {', '.join(updates)} WHERE id = ?",
            (*params, session_id),
        )
        conn.commit()

    # Ensure workflow ownership stays sync'd with session context
    try:
        if task_id is not None:
            task_info = default_repo.get_task_info(task_id)
            if task_info:
                workflow_id = task_info.get("workflow_id")
                if workflow_id:
                    # assign workflow to session if not already set
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE workflows SET session_id=? WHERE workflow_id=?",
                            (session_id, workflow_id),
                        )
                        conn.commit()
    except Exception:
        pass
