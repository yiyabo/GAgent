import json
from typing import Any, Dict, List, Optional

from ..database import get_db
from ..interfaces import TaskRepository

# -------------------------------
# Concrete repository implementation
# -------------------------------


class _SqliteTaskRepositoryBase(TaskRepository):
    """SQLite-backed implementation of TaskRepository using context-managed connections."""

    # --- mutations ---
    def create_task(
        self,
        name: str,
        status: str = "pending",
        priority: Optional[int] = None,
        parent_id: Optional[int] = None,
        task_type: str = "atomic",
    ) -> int:
        """Create a task. Optionally set parent_id to place it in the hierarchy.

        Backward compatible signature extension: existing callers need not pass parent_id.
        """
        # Compute path/depth first
        if parent_id is None:
            path = None  # Will be set after getting task_id
            depth = 0
        else:
            with get_db() as conn:
                prow = conn.execute(
                    "SELECT path, depth FROM tasks WHERE id=?",
                    (parent_id,),
                ).fetchone()
                if not prow:
                    raise ValueError(f"Parent task {parent_id} not found")
                try:
                    p_path = prow[0]
                    p_depth = prow[1]
                except Exception:
                    p_path = prow["path"]
                    p_depth = prow["depth"]
                p_path = p_path or f"/{parent_id}"
                path = None  # Will be computed after getting task_id
                depth = (p_depth or 0) + 1

        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (name, status, priority, parent_id, depth, task_type) VALUES (?, ?, ?, ?, ?, ?)",
                (name, status, priority or 100, parent_id, depth, task_type),
            )
            task_id = cursor.lastrowid

            # Compute and set path
            if parent_id is None:
                new_path = f"/{task_id}"
            else:
                prow = conn.execute(
                    "SELECT path FROM tasks WHERE id=?",
                    (parent_id,),
                ).fetchone()
                p_path = prow[0] if prow else f"/{parent_id}"
                new_path = f"{p_path}/{task_id}"

            # Update the path
            conn.execute(
                "UPDATE tasks SET path=? WHERE id=?",
                (new_path, task_id),
            )
            conn.commit()
            return task_id

    def upsert_task_input(self, task_id: int, prompt: str) -> None:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_inputs (task_id, prompt) VALUES (?, ?)",
                (task_id, prompt),
            )
            conn.commit()

    def get_task_input(self, task_id: int) -> Optional[str]:
        """Get task input prompt."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT prompt FROM task_inputs WHERE task_id=?",
                (task_id,),
            ).fetchone()
            return row[0] if row else None

    def delete_task(self, task_id: int) -> bool:
        """Delete a task and all its descendants and associated data."""
        with get_db() as conn:
            # Get the task and all its descendants
            tasks_to_delete = self.get_subtree(task_id)
            if not tasks_to_delete:
                return False

            task_ids_to_delete = [task['id'] for task in tasks_to_delete]
            placeholders = ",".join("?" * len(task_ids_to_delete))

            # Perform deletions in a single transaction
            cursor = conn.cursor()
            
            # Tables to delete from
            tables_to_clean = [
                "task_inputs", "task_outputs", "task_contexts", 
                "task_embeddings", "evaluation_history", "evaluation_configs",
                "plan_tasks"
            ]

            for table in tables_to_clean:
                cursor.execute(f"DELETE FROM {table} WHERE task_id IN ({placeholders})", task_ids_to_delete)

            # Delete from task_links (as from_id or to_id)
            cursor.execute(f"DELETE FROM task_links WHERE from_id IN ({placeholders})", task_ids_to_delete)
            cursor.execute(f"DELETE FROM task_links WHERE to_id IN ({placeholders})", task_ids_to_delete)

            # Finally, delete the tasks themselves
            cursor.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids_to_delete)
            
            conn.commit()
            return cursor.rowcount > 0

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

    def update_task(
        self,
        task_id: int,
        name: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[int] = None,
        task_type: Optional[str] = None,
    ) -> bool:
        """Update task fields. Returns True if task was found and updated."""
        with get_db() as conn:
            # Build dynamic update query
            fields = []
            values = []

            if name is not None:
                fields.append("name=?")
                values.append(name)
            if status is not None:
                fields.append("status=?")
                values.append(status)
            if priority is not None:
                fields.append("priority=?")
                values.append(priority)
            if task_type is not None:
                fields.append("task_type=?")
                values.append(task_type)

            if not fields:
                return False

            values.append(task_id)
            query = f"UPDATE tasks SET {', '.join(fields)} WHERE id=?"

            cursor = conn.execute(query, values)
            conn.commit()
            return cursor.rowcount > 0

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get task by ID."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            return _row_to_full(row) if row else None


# -------------------------------
# Task queries
# -------------------------------


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "status": row[2],
        "priority": row[3],
    }


def _row_to_full(row) -> Dict[str, Any]:
    """Convert a sqlite row to a full task dict including hierarchy fields."""
    return {
        "id": row[0],
        "name": row[1],
        "status": row[2],
        "priority": row[3],
        "parent_id": row[4],
        "path": row[5],
        "depth": row[6],
        "task_type": row[7],
    }


class SqliteTaskRepository(_SqliteTaskRepositoryBase):
    # queries continued
    def list_all_tasks(self) -> List[Dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, status, priority FROM tasks"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, status, priority FROM tasks WHERE status=?",
                (status,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_pending_full(self) -> List[Dict[str, Any]]:
        """Return all pending tasks with hierarchy fields in one query.

        This batch method reduces database round-trips for schedulers.
        Returns tasks with: id, name, status, priority, parent_id, path, depth, task_type
        """
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE status='pending' ORDER BY priority ASC, id ASC"
            ).fetchall()
        return [_row_to_full(r) for r in rows]

    def list_tasks_by_prefix(
        self, prefix: str, pending_only: bool = False, ordered: bool = True
    ) -> List[Dict[str, Any]]:
        # Prefix system removed - return empty list for backward compatibility
        return []

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
            # Prefix system removed - just use the name as is
            t = nm
            if t:
                titles.add(t)
        return sorted(titles)

    def list_plan_tasks(self, title: str) -> List[Dict[str, Any]]:
        # Prefix system removed - use plan system instead
        return []

    def list_plan_outputs(self, title: str) -> List[Dict[str, Any]]:
        """Return sections with name (short), full name, and content for a plan."""
        # Prefix system removed - use plan system instead
        return []

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

    # -------------------------------
    # Context snapshots (Phase 2)
    # -------------------------------

    def _ensure_task_contexts_table(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_contexts (
                task_id INTEGER,
                label TEXT,
                combined TEXT,
                sections TEXT,
                meta TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, label)
            )
            """
        )

    def upsert_task_context(
        self,
        task_id: int,
        combined: str,
        sections: List[Dict[str, Any]],
        meta: Dict[str, Any],
        label: Optional[str] = "latest",
    ) -> None:
        with get_db() as conn:
            self._ensure_task_contexts_table(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO task_contexts (task_id, label, combined, sections, meta)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    label or "latest",
                    combined or "",
                    json.dumps(sections or []),
                    json.dumps(meta or {}),
                ),
            )
            conn.commit()

    def get_task_context(
        self, task_id: int, label: Optional[str] = "latest"
    ) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            self._ensure_task_contexts_table(conn)
            row = None
            if label is not None:
                row = conn.execute(
                    "SELECT task_id, label, combined, sections, meta, created_at FROM task_contexts WHERE task_id=? AND label=?",
                    (task_id, label),
                ).fetchone()
                # If a specific label was requested and not found, return immediately.
                if not row:
                    return None

            # Fallback to the latest if no label was specified and nothing was found yet.
            if not row:
                row = conn.execute(
                    "SELECT task_id, label, combined, sections, meta, created_at FROM task_contexts WHERE task_id=? ORDER BY datetime(created_at) DESC LIMIT 1",
                    (task_id,),
                ).fetchone()

        if not row:
            return None

        try:
            sections_obj = json.loads(row["sections"]) if isinstance(row["sections"], str) else row["sections"]
        except Exception:
            sections_obj = []
        try:
            meta_obj = json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]
        except Exception:
            meta_obj = {}

        return {
            "task_id": row["task_id"],
            "label": row["label"],
            "combined": row["combined"],
            "sections": sections_obj,
            "meta": meta_obj,
            "created_at": row["created_at"],
        }

    def list_task_contexts(self, task_id: int) -> List[Dict[str, Any]]:
        with get_db() as conn:
            self._ensure_task_contexts_table(conn)
            rows = conn.execute(
                "SELECT label, created_at, meta, combined, sections FROM task_contexts WHERE task_id=? ORDER BY datetime(created_at) DESC",
                (task_id,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                lbl = r[0]
                created_at = r[1]
                meta = r[2]
                combined = r[3]
                sections = r[4]
            except Exception:
                lbl = r["label"]
                created_at = r["created_at"]
                meta = r["meta"]
                combined = r["combined"]
                sections = r["sections"]
            try:
                meta_obj = json.loads(meta) if isinstance(meta, str) else meta
            except Exception:
                meta_obj = {}
            try:
                sections_obj = json.loads(sections) if isinstance(sections, str) else sections
            except Exception:
                sections_obj = []
            out.append({
                "label": lbl, 
                "created_at": created_at, 
                "meta": meta_obj,
                "combined": combined,
                "sections": sections_obj
            })
        return out

    # -------------------------------
    # Hierarchy CRUD & queries (Phase 5)
    # -------------------------------

    def get_task_info(self, task_id: int) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return _row_to_full(row)

    def get_parent(self, task_id: int) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT parent_id FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                return None
            try:
                pid = row[0]
            except Exception:
                pid = row["parent_id"]
            if pid is None:
                return None
            prow = conn.execute(
                "SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE id=?",
                (pid,),
            ).fetchone()
        if not prow:
            return None
        return _row_to_full(prow)

    def get_children(self, parent_id: int) -> List[Dict[str, Any]]:
        """Return direct children of a task, ordered by priority and id."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE parent_id=? ORDER BY priority ASC, id ASC",
                (parent_id,),
            ).fetchall()
        return [_row_to_full(r) for r in rows]

    def get_ancestors(self, task_id: int) -> List[Dict[str, Any]]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT path FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                return []
            try:
                path = row[0]
            except Exception:
                path = row["path"]
            if not path:
                return []
            # Parse ids from path like '/12/45/79'
            parts = [p for p in (path.split("/") if path else []) if p]
            if not parts:
                return []
            ancestor_ids = [int(p) for p in parts[:-1]]  # exclude self
            if not ancestor_ids:
                return []
            placeholders = ",".join(["?"] * len(ancestor_ids))
            rows = conn.execute(
                f"SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE id IN ({placeholders}) ORDER BY depth ASC",
                ancestor_ids,
            ).fetchall()
        return [_row_to_full(r) for r in rows]

    def get_descendants(self, root_id: int) -> List[Dict[str, Any]]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT path FROM tasks WHERE id=?", (root_id,)
            ).fetchone()
            if not row:
                return []
            try:
                root_path = row[0]
            except Exception:
                root_path = row["path"]
            if not root_path:
                root_path = f"/{root_id}"
            like_prefix = root_path + "/%"
            rows = conn.execute(
                "SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE path LIKE ? ORDER BY path ASC",
                (like_prefix,),
            ).fetchall()
        return [_row_to_full(r) for r in rows]

    def get_subtree(self, root_id: int) -> List[Dict[str, Any]]:
        """Return root task followed by all descendants ordered by path."""
        root = self.get_task_info(root_id)
        if not root:
            return []
        desc = self.get_descendants(root_id)
        return [root] + desc

    def update_task_parent(self, task_id: int, new_parent_id: Optional[int]) -> None:
        """Move a task under a new parent (or to root if None). Updates path/depth for the subtree.

        Prevents cycles: cannot move under its own subtree.
        """
        with get_db() as conn:
            cur = conn.cursor()
            # Fetch current node info
            row = cur.execute(
                "SELECT id, parent_id, path, depth FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Task {task_id} not found")
            try:
                old_parent_id = row[1]
                old_path = row[2]
                old_depth = row[3] or 0
            except Exception:
                old_parent_id = row["parent_id"]
                old_path = row["path"]
                old_depth = row["depth"] or 0

            if new_parent_id == old_parent_id:
                return

            # Determine new parent info
            if new_parent_id is None:
                new_parent_path = None
                new_parent_depth = -1
                new_root_path = f"/{task_id}"
                new_depth = 0
            else:
                prow = cur.execute(
                    "SELECT id, path, depth FROM tasks WHERE id=?",
                    (new_parent_id,),
                ).fetchone()
                if not prow:
                    raise ValueError(f"Parent task {new_parent_id} not found")
                try:
                    p_path = prow[1]
                    p_depth = prow[2]
                except Exception:
                    p_path = prow["path"]
                    p_depth = prow["depth"]
                p_path = p_path or f"/{new_parent_id}"

                # Prevent cycles: cannot move under own subtree
                if old_path and (
                    p_path == old_path or p_path.startswith(old_path + "/")
                ):
                    raise ValueError("Cannot move a task under its own subtree")

                new_parent_path = p_path
                new_parent_depth = p_depth or 0
                new_root_path = f"{new_parent_path}/{task_id}"
                new_depth = new_parent_depth + 1

            # Update root node
            conn.execute(
                "UPDATE tasks SET parent_id=?, path=?, depth=? WHERE id=?",
                (new_parent_id, new_root_path, new_depth, task_id),
            )

            # Update all descendants' paths and depths
            if old_path:
                # Find all descendants that need path/depth updates
                descendants = conn.execute(
                    "SELECT id, path FROM tasks WHERE path LIKE ? AND id != ?",
                    (old_path + "/%", task_id),
                ).fetchall()

                for desc_row in descendants:
                    desc_id = desc_row[0]
                    desc_old_path = desc_row[1]

                    # Calculate new path by replacing the old prefix
                    if desc_old_path and desc_old_path.startswith(old_path + "/"):
                        desc_new_path = new_root_path + desc_old_path[len(old_path) :]
                        desc_new_depth = desc_new_path.count("/") - 1

                        conn.execute(
                            "UPDATE tasks SET path=?, depth=? WHERE id=?",
                            (desc_new_path, desc_new_depth, desc_id),
                        )

            conn.commit()

    def update_task_type(self, task_id: int, task_type: str) -> None:
        """Update the task type (root/composite/atomic)."""
        with get_db() as conn:
            conn.execute(
                "UPDATE tasks SET task_type=? WHERE id=?",
                (task_type, task_id),
            )
            conn.commit()

    # -------------------------------
    # GLM Embeddings operations
    # -------------------------------

    def store_task_embedding(
        self, task_id: int, embedding_vector: str, model: str = "embedding-2"
    ) -> None:
        """Store embedding vector for a task."""
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO task_embeddings 
                (task_id, embedding_vector, embedding_model, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (task_id, embedding_vector, model),
            )
            conn.commit()

    def get_task_embedding(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get embedding for a specific task."""
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT task_id, embedding_vector, embedding_model, created_at, updated_at
                FROM task_embeddings 
                WHERE task_id = ?
            """,
                (task_id,),
            ).fetchone()

            if row:
                return {
                    "task_id": row["task_id"],
                    "embedding_vector": row["embedding_vector"],
                    "embedding_model": row["embedding_model"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

    def get_tasks_with_embeddings(
        self, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get all tasks that have embeddings with their content."""
        with get_db() as conn:
            query = """
                SELECT 
                    t.id, t.name, t.status, t.priority,
                    toutput.content,
                    te.embedding_vector, te.embedding_model, te.updated_at
                FROM tasks t
                LEFT JOIN task_outputs toutput ON t.id = toutput.task_id
                INNER JOIN task_embeddings te ON t.id = te.task_id
                ORDER BY te.updated_at DESC
            """

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query).fetchall()

            # Convert rows to dict with embedding fields
            result = []
            for row in rows:
                try:
                    task_dict = {
                        "id": row[0],
                        "name": row[1],
                        "status": row[2],
                        "priority": row[3],
                        "content": row[4],
                        "embedding_vector": row[5],
                        "embedding_model": row[6],
                        "updated_at": row[7],
                    }
                except Exception:
                    # Fallback for sqlite3.Row objects
                    task_dict = {
                        "id": row["id"],
                        "name": row["name"],
                        "status": row["status"],
                        "priority": row["priority"],
                        "content": row["content"],
                        "embedding_vector": row["embedding_vector"],
                        "embedding_model": row["embedding_model"],
                        "updated_at": row["updated_at"],
                    }
                result.append(task_dict)
            return result

    def get_tasks_without_embeddings(
        self, status: Optional[str] = "done"
    ) -> List[Dict[str, Any]]:
        """Get tasks that don't have embeddings yet."""
        with get_db() as conn:
            where_clause = ""
            params = []

            if status:
                where_clause = "WHERE t.status = ?"
                params.append(status)

            query = f"""
                SELECT t.id, t.name, t.status, t.priority, to.content
                FROM tasks t
                LEFT JOIN task_outputs to ON t.id = to.task_id
                LEFT JOIN task_embeddings te ON t.id = te.task_id
                {where_clause}
                AND te.task_id IS NULL
                AND to.content IS NOT NULL
                AND TRIM(to.content) != ""
                ORDER BY t.id DESC
            """

            rows = conn.execute(query, params).fetchall()
            return [_row_to_dict(row) for row in rows]

    def delete_task_embedding(self, task_id: int) -> None:
        """Delete embedding for a task."""
        with get_db() as conn:
            conn.execute("DELETE FROM task_embeddings WHERE task_id = ?", (task_id,))
            conn.commit()

    def count_tasks_with_embeddings(self) -> int:
        """Count total number of tasks with embeddings."""
        with get_db() as conn:
            row = conn.execute("SELECT COUNT(*) FROM task_embeddings").fetchone()
            return row[0] if row else 0

    def get_embedding_stats(self) -> Dict[str, Any]:
        """Get statistics about embeddings."""
        with get_db() as conn:
            total_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            total_embeddings = conn.execute(
                "SELECT COUNT(*) FROM task_embeddings"
            ).fetchone()[0]

            model_stats = conn.execute("""
                SELECT embedding_model, COUNT(*) as count
                FROM task_embeddings
                GROUP BY embedding_model
            """).fetchall()

            return {
                "total_tasks": total_tasks,
                "total_embeddings": total_embeddings,
                "coverage_percent": (total_embeddings / total_tasks * 100)
                if total_tasks > 0
                else 0,
                "model_distribution": {row[0]: row[1] for row in model_stats},
            }

    # -------------------------------
    # Evaluation System operations
    # -------------------------------

    def store_evaluation_history(
        self,
        task_id: int,
        iteration: int,
        content: str,
        overall_score: float,
        dimension_scores: Dict[str, float],
        suggestions: List[str],
        needs_revision: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Store evaluation history for a task iteration."""
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO evaluation_history 
                (task_id, iteration, content, overall_score, dimension_scores, suggestions, needs_revision, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    task_id,
                    iteration,
                    content,
                    overall_score,
                    json.dumps(dimension_scores),
                    json.dumps(suggestions),
                    needs_revision,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_evaluation_history(self, task_id: int) -> List[Dict[str, Any]]:
        """Get evaluation history for a task."""
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, iteration, content, overall_score, dimension_scores, 
                       suggestions, needs_revision, timestamp, metadata
                FROM evaluation_history
                WHERE task_id = ?
                ORDER BY iteration ASC
            """,
                (task_id,),
            ).fetchall()

            result = []
            for row in rows:
                try:
                    # Handle both tuple and Row objects
                    row_dict = {
                        "id": row[0],
                        "task_id": row[1],
                        "iteration": row[2],
                        "content": row[3],
                        "overall_score": row[4],
                        "dimension_scores": json.loads(row[5]) if row[5] else {},
                        "suggestions": json.loads(row[6]) if row[6] else [],
                        "needs_revision": bool(row[7]),
                        "timestamp": row[8],
                        "metadata": json.loads(row[9]) if row[9] else None,
                    }
                except Exception:
                    # Fallback for sqlite3.Row objects
                    row_dict = {
                        "id": row["id"],
                        "task_id": row["task_id"],
                        "iteration": row["iteration"],
                        "content": row["content"],
                        "overall_score": row["overall_score"],
                        "dimension_scores": json.loads(row["dimension_scores"])
                        if row["dimension_scores"]
                        else {},
                        "suggestions": json.loads(row["suggestions"])
                        if row["suggestions"]
                        else [],
                        "needs_revision": bool(row["needs_revision"]),
                        "timestamp": row["timestamp"],
                        "metadata": json.loads(row["metadata"])
                        if row["metadata"]
                        else None,
                    }
                result.append(row_dict)
            return result

    def get_latest_evaluation(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get the latest evaluation for a task."""
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT id, task_id, iteration, content, overall_score, dimension_scores, 
                       suggestions, needs_revision, timestamp, metadata
                FROM evaluation_history
                WHERE task_id = ?
                ORDER BY iteration DESC
                LIMIT 1
            """,
                (task_id,),
            ).fetchone()

            if not row:
                return None

            try:
                return {
                    "id": row[0],
                    "task_id": row[1],
                    "iteration": row[2],
                    "content": row[3],
                    "overall_score": row[4],
                    "dimension_scores": json.loads(row[5]) if row[5] else {},
                    "suggestions": json.loads(row[6]) if row[6] else [],
                    "needs_revision": bool(row[7]),
                    "timestamp": row[8],
                    "metadata": json.loads(row[9]) if row[9] else None,
                }
            except Exception:
                # Fallback for sqlite3.Row objects
                return {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "iteration": row["iteration"],
                    "content": row["content"],
                    "overall_score": row["overall_score"],
                    "dimension_scores": json.loads(row["dimension_scores"])
                    if row["dimension_scores"]
                    else {},
                    "suggestions": json.loads(row["suggestions"])
                    if row["suggestions"]
                    else [],
                    "needs_revision": bool(row["needs_revision"]),
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"])
                    if row["metadata"]
                    else None,
                }

    def store_evaluation_config(
        self,
        task_id: int,
        quality_threshold: float = 0.8,
        max_iterations: int = 3,
        evaluation_dimensions: Optional[List[str]] = None,
        domain_specific: bool = False,
        strict_mode: bool = False,
        custom_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        """Store evaluation configuration for a task."""
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evaluation_configs
                (task_id, quality_threshold, max_iterations, evaluation_dimensions, 
                 domain_specific, strict_mode, custom_weights, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (
                    task_id,
                    quality_threshold,
                    max_iterations,
                    json.dumps(evaluation_dimensions)
                    if evaluation_dimensions
                    else None,
                    domain_specific,
                    strict_mode,
                    json.dumps(custom_weights) if custom_weights else None,
                ),
            )
            conn.commit()

    def get_evaluation_config(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get evaluation configuration for a task."""
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT task_id, quality_threshold, max_iterations, evaluation_dimensions,
                       domain_specific, strict_mode, custom_weights, created_at, updated_at
                FROM evaluation_configs
                WHERE task_id = ?
            """,
                (task_id,),
            ).fetchone()

            if not row:
                return None

            try:
                return {
                    "task_id": row[0],
                    "quality_threshold": row[1],
                    "max_iterations": row[2],
                    "evaluation_dimensions": json.loads(row[3]) if row[3] else None,
                    "domain_specific": bool(row[4]),
                    "strict_mode": bool(row[5]),
                    "custom_weights": json.loads(row[6]) if row[6] else None,
                    "created_at": row[7],
                    "updated_at": row[8],
                }
            except Exception:
                # Fallback for sqlite3.Row objects
                return {
                    "task_id": row["task_id"],
                    "quality_threshold": row["quality_threshold"],
                    "max_iterations": row["max_iterations"],
                    "evaluation_dimensions": json.loads(row["evaluation_dimensions"])
                    if row["evaluation_dimensions"]
                    else None,
                    "domain_specific": bool(row["domain_specific"]),
                    "strict_mode": bool(row["strict_mode"]),
                    "custom_weights": json.loads(row["custom_weights"])
                    if row["custom_weights"]
                    else None,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }

    def delete_evaluation_history(self, task_id: int) -> None:
        """Delete all evaluation history for a task."""
        with get_db() as conn:
            conn.execute("DELETE FROM evaluation_history WHERE task_id = ?", (task_id,))
            conn.commit()

    def get_evaluation_stats(self) -> Dict[str, Any]:
        """Get overall evaluation statistics."""
        with get_db() as conn:
            total_evaluations = conn.execute(
                "SELECT COUNT(*) FROM evaluation_history"
            ).fetchone()[0]

            avg_score = (
                conn.execute("""
                SELECT AVG(overall_score) FROM evaluation_history
            """).fetchone()[0]
                or 0.0
            )

            iteration_stats = conn.execute("""
                SELECT AVG(iteration) as avg_iterations, MAX(iteration) as max_iterations
                FROM (
                    SELECT task_id, MAX(iteration) as iteration
                    FROM evaluation_history
                    GROUP BY task_id
                )
            """).fetchone()

            quality_distribution = conn.execute("""
                SELECT 
                    CASE 
                        WHEN overall_score >= 0.9 THEN 'excellent'
                        WHEN overall_score >= 0.8 THEN 'good'
                        WHEN overall_score >= 0.7 THEN 'acceptable'
                        ELSE 'needs_improvement'
                    END as quality_tier,
                    COUNT(*) as count
                FROM evaluation_history
                GROUP BY quality_tier
            """).fetchall()

            return {
                "total_evaluations": total_evaluations,
                "average_score": round(avg_score, 3),
                "average_iterations": round(iteration_stats[0] or 0, 2),
                "max_iterations_used": iteration_stats[1] or 0,
                "quality_distribution": {
                    row[0]: row[1] for row in quality_distribution
                },
            }

    # -----------------------------
    # Plan Management System
    # -----------------------------

    def _ensure_plans_table(self, conn) -> None:
        """确保plan相关表存在"""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL UNIQUE,
                description TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                config_json TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS plan_tasks (
                plan_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                task_category TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (plan_id, task_id),
                FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)

    def create_plan(
        self,
        title: str,
        description: Optional[str] = None,
        config_json: Optional[Dict[str, Any]] = None,
    ) -> int:
        """创建新的研究计划"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            
            # 检查标题是否已存在，如果存在则添加序号
            original_title = title
            counter = 1
            while True:
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO plans (title, description, config_json)
                        VALUES (?, ?, ?)
                    """,
                        (title, description, json.dumps(config_json) if config_json else None),
                    )
                    conn.commit()
                    return cursor.lastrowid
                except Exception as e:
                    if "UNIQUE constraint failed" in str(e):
                        # 标题重复，添加序号
                        counter += 1
                        title = f"{original_title} ({counter})"
                        continue
                    else:
                        # 其他错误，重新抛出
                        raise e

    def link_task_to_plan(
        self, plan_id: int, task_id: int, task_category: str = "general"
    ) -> bool:
        """将任务与计划关联"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            try:
                conn.execute(
                    """
                    INSERT INTO plan_tasks (plan_id, task_id, task_category)
                    VALUES (?, ?, ?)
                """,
                    (plan_id, task_id, task_category),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        """获取计划详情"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            row = conn.execute(
                """
                SELECT id, title, description, status, created_at, updated_at, config_json
                FROM plans WHERE id = ?
            """,
                (plan_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_plan_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """根据标题获取计划"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            row = conn.execute(
                """
                SELECT id, title, description, status, created_at, updated_at, config_json
                FROM plans WHERE title = ?
            """,
                (title,),
            ).fetchone()
            return dict(row) if row else None

    def list_plans(self) -> List[Dict[str, Any]]:
        """列出所有计划"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            rows = conn.execute("""
                SELECT id, title, description, status, created_at, updated_at, config_json
                FROM plans ORDER BY created_at DESC
            """).fetchall()
            return [dict(row) for row in rows]

    def get_plan_tasks(
        self, plan_id: int, task_category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """获取特定计划的所有任务"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            if task_category:
                query = """
                    SELECT t.id, t.name, t.status, t.priority, t.task_type, 
                           t.parent_id, t.path, t.depth, pt.task_category
                    FROM plans p
                    JOIN plan_tasks pt ON p.id = pt.plan_id
                    JOIN tasks t ON pt.task_id = t.id
                    WHERE p.id = ? AND pt.task_category = ?
                    ORDER BY t.id
                """
                params = (plan_id, task_category)
            else:
                query = """
                    SELECT t.id, t.name, t.status, t.priority, t.task_type, 
                           t.parent_id, t.path, t.depth, pt.task_category
                    FROM plans p
                    JOIN plan_tasks pt ON p.id = pt.plan_id
                    JOIN tasks t ON pt.task_id = t.id
                    WHERE p.id = ?
                    ORDER BY t.id
                """
                params = (plan_id,)

            rows = conn.execute(query, params).fetchall()
            def _row_to_plan_task(row) -> Dict[str, Any]:
                return {
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "priority": row[3],
                    "task_type": row[4],
                    "parent_id": row[5],
                    "path": row[6],
                    "depth": row[7],
                }
            return [_row_to_plan_task(row) for row in rows]

    def get_plan_tasks_summary(self, plan_id: int) -> List[Dict[str, Any]]:
        """获取特定计划的所有任务的ID和名称"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            query = """
                SELECT t.id, t.name
                FROM tasks t
                JOIN plan_tasks pt ON t.id = pt.task_id
                WHERE pt.plan_id = ?
                ORDER BY t.id
            """
            rows = conn.execute(query, (plan_id,)).fetchall()
            return [{"id": row[0], "name": row[1]} for row in rows]

    def get_plan_with_tasks(self, plan_id: int) -> Optional[Dict[str, Any]]:
        """获取完整计划包含所有任务"""
        plan = self.get_plan(plan_id)
        if not plan:
            return None

        tasks = self.get_plan_tasks(plan_id)
        return {"plan": plan, "tasks": tasks}

    def get_plan_for_task(self, task_id: int) -> Optional[int]:
        """Find the plan_id for a given task_id."""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            row = conn.execute(
                "SELECT plan_id FROM plan_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return row[0] if row else None

    def get_plan_summary(self, plan_id: int) -> Optional[Dict[str, Any]]:
        """获取计划汇总信息"""
        with get_db() as conn:
            self._ensure_plans_table(conn)

            # 基础计划信息
            plan = self.get_plan(plan_id)
            if not plan:
                return None

            # 任务统计
            cursor = conn.execute(
                """
                SELECT 
                    COUNT(*) as total_tasks,
                    COUNT(CASE WHEN t.status = 'done' THEN 1 END) as completed_tasks,
                    COUNT(CASE WHEN t.status = 'pending' THEN 1 END) as pending_tasks,
                    COUNT(CASE WHEN t.status = 'failed' THEN 1 END) as failed_tasks
                FROM plan_tasks pt
                JOIN tasks t ON pt.task_id = t.id
                WHERE pt.plan_id = ?
            """,
                (plan_id,),
            )

            stats = cursor.fetchone()
            if stats:
                total = stats[0] or 0
                completed = stats[1] or 0
                progress = completed / total if total > 0 else 0.0

                return {
                    **plan,
                    "task_count": total,
                    "completed_count": completed,
                    "pending_count": stats[2] or 0,
                    "failed_count": stats[3] or 0,
                    "progress": progress,
                }

            return {**plan, "task_count": 0, "completed_count": 0, "progress": 0.0}

    def delete_plan(self, plan_id: int) -> bool:
        """删除计划（级联删除关联的tasks和关联数据）"""
        with get_db() as conn:
            self._ensure_plans_table(conn)

            # 1. Get all task_ids for this plan
            task_rows = conn.execute(
                """
                SELECT task_id FROM plan_tasks WHERE plan_id = ?
            """,
                (plan_id,),
            ).fetchall()
            task_ids = [row[0] for row in task_rows]

            # 2. Delete associated plan_tasks records
            conn.execute("DELETE FROM plan_tasks WHERE plan_id = ?", (plan_id,))

            # 3. Delete evaluation history for related tasks
            for task_id in task_ids:
                conn.execute(
                    "DELETE FROM evaluation_history WHERE task_id = ?", (task_id,)
                )

            # 4. Delete evaluation configs for related tasks
            for task_id in task_ids:
                conn.execute(
                    "DELETE FROM evaluation_configs WHERE task_id = ?", (task_id,)
                )

            # 5. Delete tasks related to this plan
            for task_id in task_ids:
                # Delete task context snapshots
                conn.execute("DELETE FROM task_contexts WHERE task_id = ?", (task_id,))
                # Delete task embeddings
                conn.execute(
                    "DELETE FROM task_embeddings WHERE task_id = ?", (task_id,)
                )
                # Delete task links
                conn.execute(
                    "DELETE FROM task_links WHERE from_id = ? OR to_id = ?",
                    (task_id, task_id),
                )
                # Delete task inputs and outputs
                conn.execute("DELETE FROM task_inputs WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM task_outputs WHERE task_id = ?", (task_id,))
                # Finally delete the task itself
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

            # 6. Delete the plan itself
            cursor = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            conn.commit()
            return cursor.rowcount > 0

    def unlink_task_from_plan(self, plan_id: int, task_id: int) -> bool:
        """从计划中移除任务"""
        with get_db() as conn:
            self._ensure_plans_table(conn)
            cursor = conn.execute(
                """
                DELETE FROM plan_tasks WHERE plan_id = ? AND task_id = ?
            """,
                (plan_id, task_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_plan_statistics(self) -> Dict[str, Any]:
        """获取系统级计划统计"""
        with get_db() as conn:
            self._ensure_plans_table(conn)

            plans_total = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]

            if plans_total == 0:
                return {
                    "total_plans": 0,
                    "active_plans": 0,
                    "total_tasks_in_plans": 0,
                    "plans_with_tasks": 0,
                }

            plans_with_tasks = conn.execute("""
                SELECT COUNT(DISTINCT plan_id) FROM plan_tasks
            """).fetchone()[0]

            total_tasks = conn.execute("""
                SELECT COUNT(*) FROM plan_tasks
            """).fetchone()[0]

            return {
                "total_plans": plans_total,
                "active_plans": conn.execute(
                    "SELECT COUNT(*) FROM plans WHERE status = ?", ("active",)
                ).fetchone()[0],
                "completed_plans": conn.execute(
                    "SELECT COUNT(*) FROM plans WHERE status = ?", ("completed",)
                ).fetchone()[0],
                "total_tasks_in_plans": total_tasks,
                "plans_with_tasks": plans_with_tasks,
            }

    # Legacy support for prefix-based plans
    def migrate_from_prefix_system(self) -> int:
        """从前缀系统迁移到Plan系统 - 前缀系统已删除，返回0"""
        # Prefix system removed - no migration needed
        return 0

    # -----------------------------
    # Chat History
    # -----------------------------

    def add_chat_message(self, plan_id: int, sender: str, message: str) -> int:
        """Adds a new chat message to the history for a plan."""
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO chat_messages (plan_id, sender, message) VALUES (?, ?, ?)",
                (plan_id, sender, message),
            )
            conn.commit()
            return cursor.lastrowid

    def get_chat_history(self, plan_id: int) -> List[Dict[str, Any]]:
        """Retrieves the chat history for a given plan."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT sender, message, timestamp FROM chat_messages WHERE plan_id = ? ORDER BY timestamp ASC",
                (plan_id,),
            ).fetchall()
            return [dict(row) for row in rows]


default_repo = SqliteTaskRepository()
