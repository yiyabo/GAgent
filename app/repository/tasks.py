import re
from typing import Any, Dict, List, Optional

from ..database import get_db
from ..interfaces import TaskRepository


# -------------------------------
# Helpers for plan prefix handling
# -------------------------------

def _plan_prefix(title: str) -> str:
    return f"[{title}] "


def _split_prefix(name: str):
    m = re.match(r"^\[(.*?)\]\s+(.*)$", name)
    if m:
        return m.group(1), m.group(2)
    return None, name


# -------------------------------
# Concrete repository implementation
# -------------------------------


class _SqliteTaskRepositoryBase(TaskRepository):
    """SQLite-backed implementation of TaskRepository using context-managed connections."""

    # --- mutations ---
    def create_task(self, name: str, status: str = "pending", priority: Optional[int] = None) -> int:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tasks (name, status, priority) VALUES (?, ?, ?)",
                (name, status, priority),
            )
            conn.commit()
            return cur.lastrowid

    def upsert_task_input(self, task_id: int, prompt: str) -> None:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_inputs (task_id, prompt) VALUES (?, ?)",
                (task_id, prompt),
            )
            conn.commit()

    def upsert_task_output(self, task_id: int, content: str) -> None:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_outputs (task_id, content) VALUES (?, ?)",
                (task_id, content),
            )
            conn.commit()

    def update_task_status(self, task_id: int, status: str) -> None:
        with get_db() as conn:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
            conn.commit()


# -------------------------------
# Task queries
# -------------------------------

def _row_to_dict(row) -> Dict[str, Any]:
    try:
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "priority": row.get("priority") if hasattr(row, "get") else row[3] if len(row) > 3 else None,
        }
    except Exception:
        # tuple-like
        return {
            "id": row[0],
            "name": row[1],
            "status": row[2] if len(row) > 2 else None,
            "priority": row[3] if len(row) > 3 else None,
        }


    
class SqliteTaskRepository(_SqliteTaskRepositoryBase):
    # queries continued
    def list_all_tasks(self) -> List[Dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute("SELECT id, name, status, priority FROM tasks").fetchall()
        return [_row_to_dict(r) for r in rows]


    def list_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, status, priority FROM tasks WHERE status=?",
                (status,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


    def list_tasks_by_prefix(self, prefix: str, pending_only: bool = False, ordered: bool = True) -> List[Dict[str, Any]]:
        where = "name LIKE ?"
        params = [prefix + "%"]
        if pending_only:
            where += " AND status='pending'"
        order = "ORDER BY priority ASC, id ASC" if ordered else ""
        sql = f"SELECT id, name, status, priority FROM tasks WHERE {where} {order}"
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]


    def get_task_input_prompt(self, task_id: int) -> Optional[str]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT prompt FROM task_inputs WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return row["prompt"]
        except Exception:
            return row[0]


    def get_task_output_content(self, task_id: int) -> Optional[str]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT content FROM task_outputs WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return row["content"]
        except Exception:
            return row[0]


    def list_plan_titles(self) -> List[str]:
        with get_db() as conn:
            rows = conn.execute("SELECT name FROM tasks").fetchall()
        titles = set()
        for r in rows:
            try:
                nm = r["name"]
            except Exception:
                nm = r[0]
            t, _ = _split_prefix(nm)
            if t:
                titles.add(t)
        return sorted(titles)


    def list_plan_tasks(self, title: str) -> List[Dict[str, Any]]:
        prefix = _plan_prefix(title)
        return self.list_tasks_by_prefix(prefix, pending_only=False, ordered=True)


    def list_plan_outputs(self, title: str) -> List[Dict[str, Any]]:
        """Return sections with name (short), full name, and content for a plan."""
        prefix = _plan_prefix(title)
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT t.name, o.content
                FROM tasks t
                JOIN task_outputs o ON o.task_id = t.id
                WHERE t.name LIKE ?
                ORDER BY t.priority ASC, t.id ASC
                """,
                (prefix + "%",),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                name = r["name"]
                content = r["content"]
            except Exception:
                name, content = r[0], r[1]
            _, short = _split_prefix(name)
            out.append({"name": name, "short_name": short, "content": content})
        return out


# -------------------------------
# Backward-compatible module-level API
# -------------------------------

default_repo = SqliteTaskRepository()


def create_task(name: str, status: str = "pending", priority: Optional[int] = None) -> int:
    return default_repo.create_task(name, status, priority)


def upsert_task_input(task_id: int, prompt: str) -> None:
    return default_repo.upsert_task_input(task_id, prompt)


def upsert_task_output(task_id: int, content: str) -> None:
    return default_repo.upsert_task_output(task_id, content)


def update_task_status(task_id: int, status: str) -> None:
    return default_repo.update_task_status(task_id, status)


def list_all_tasks() -> List[Dict[str, Any]]:
    return default_repo.list_all_tasks()


def list_tasks_by_status(status: str) -> List[Dict[str, Any]]:
    return default_repo.list_tasks_by_status(status)


def list_tasks_by_prefix(prefix: str, pending_only: bool = False, ordered: bool = True) -> List[Dict[str, Any]]:
    return default_repo.list_tasks_by_prefix(prefix, pending_only, ordered)


def get_task_input_prompt(task_id: int) -> Optional[str]:
    return default_repo.get_task_input_prompt(task_id)


def get_task_output_content(task_id: int) -> Optional[str]:
    return default_repo.get_task_output_content(task_id)


def list_plan_titles() -> List[str]:
    return default_repo.list_plan_titles()


def list_plan_tasks(title: str) -> List[Dict[str, Any]]:
    return default_repo.list_plan_tasks(title)


def list_plan_outputs(title: str) -> List[Dict[str, Any]]:
    return default_repo.list_plan_outputs(title)
