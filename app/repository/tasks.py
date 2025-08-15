from typing import Any, Dict, List, Optional

from ..database import get_db
from ..interfaces import TaskRepository
from ..utils import plan_prefix, split_prefix


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
            t, _ = split_prefix(nm)
            if t:
                titles.add(t)
        return sorted(titles)


    def list_plan_tasks(self, title: str) -> List[Dict[str, Any]]:
        prefix = plan_prefix(title)
        return self.list_tasks_by_prefix(prefix, pending_only=False, ordered=True)


    def list_plan_outputs(self, title: str) -> List[Dict[str, Any]]:
        """Return sections with name (short), full name, and content for a plan."""
        prefix = plan_prefix(title)
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
            _, short = split_prefix(name)
            out.append({"name": name, "short_name": short, "content": content})
        return out

    # -------------------------------
    # Links (graph) - Phase 1
    # -------------------------------

    def _ensure_task_links_table(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_links (
                from_id INTEGER,
                to_id INTEGER,
                kind TEXT,
                PRIMARY KEY (from_id, to_id, kind)
            )
            """
        )

    def create_link(self, from_id: int, to_id: int, kind: str) -> None:
        with get_db() as conn:
            self._ensure_task_links_table(conn)
            conn.execute(
                "INSERT OR IGNORE INTO task_links (from_id, to_id, kind) VALUES (?, ?, ?)",
                (from_id, to_id, kind),
            )
            conn.commit()

    def delete_link(self, from_id: int, to_id: int, kind: str) -> None:
        with get_db() as conn:
            self._ensure_task_links_table(conn)
            conn.execute(
                "DELETE FROM task_links WHERE from_id=? AND to_id=? AND kind=?",
                (from_id, to_id, kind),
            )
            conn.commit()

    def list_links(
        self,
        from_id: Optional[int] = None,
        to_id: Optional[int] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where = []
        params: List[Any] = []
        if from_id is not None:
            where.append("from_id=?")
            params.append(from_id)
        if to_id is not None:
            where.append("to_id=?")
            params.append(to_id)
        if kind is not None:
            where.append("kind=?")
            params.append(kind)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT from_id, to_id, kind FROM task_links {where_sql} ORDER BY from_id ASC, to_id ASC, kind ASC"
        with get_db() as conn:
            self._ensure_task_links_table(conn)
            rows = conn.execute(sql, params).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append({
                    "from_id": r["from_id"],
                    "to_id": r["to_id"],
                    "kind": r["kind"],
                })
            except Exception:
                out.append({
                    "from_id": r[0],
                    "to_id": r[1],
                    "kind": r[2],
                })
        return out

    def list_dependencies(self, task_id: int) -> List[Dict[str, Any]]:
        """Return upstream dependency/reference tasks for the given task.

        Semantics: incoming edges (to_id == task_id) with kind in ('requires','refers').
        Order: requires first, then refers; each group by (priority, id).
        """
        with get_db() as conn:
            self._ensure_task_links_table(conn)
            rows = conn.execute(
                """
                SELECT t.id, t.name, t.status, t.priority, l.kind
                FROM task_links l
                JOIN tasks t ON t.id = l.from_id
                WHERE l.to_id = ? AND l.kind IN ('requires','refers')
                ORDER BY CASE l.kind WHEN 'requires' THEN 0 ELSE 1 END,
                        t.priority ASC, t.id ASC
                """,
                (task_id,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                item = _row_to_dict(r)
                # Row may be sqlite3.Row; add link kind explicitly
                item["kind"] = r["kind"]
            except Exception:
                item = _row_to_dict(r)
                item["kind"] = r[4]
            out.append(item)
        return out


default_repo = SqliteTaskRepository()
