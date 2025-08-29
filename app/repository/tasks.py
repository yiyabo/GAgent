from typing import Any, Dict, List, Optional
import json

from ..database import get_db
from ..interfaces import TaskRepository
from ..utils import plan_prefix, split_prefix
from .optimized_queries import OptimizedTaskQueries


# -------------------------------
# Concrete repository implementation
# -------------------------------


class _SqliteTaskRepositoryBase(TaskRepository):
    """SQLite-backed implementation of TaskRepository using context-managed connections."""

    # --- mutations ---
    def create_task(self, name: str, status: str = "pending", priority: Optional[int] = None, parent_id: Optional[int] = None, task_type: str = "atomic") -> int:
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
            rows = conn.execute("SELECT id, name, status, priority FROM tasks").fetchall()
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


    def list_tasks_by_prefix(self, prefix: str, pending_only: bool = False, ordered: bool = True) -> List[Dict[str, Any]]:
        where = "name LIKE ?"
        params = [prefix + "%"]
        if pending_only:
            where += " AND status='pending'"
        order = "ORDER BY priority ASC, id ASC" if ordered else ""
        sql = f"SELECT id, name, status, priority, parent_id, path, depth, task_type FROM tasks WHERE {where} {order}"
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_full(r) for r in rows]


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

    def get_task_context(self, task_id: int, label: Optional[str] = "latest") -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            self._ensure_task_contexts_table(conn)
            row = None
            if label is not None:
                row = conn.execute(
                    "SELECT task_id, label, combined, sections, meta, created_at FROM task_contexts WHERE task_id=? AND label=?",
                    (task_id, label),
                ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT task_id, label, combined, sections, meta, created_at FROM task_contexts WHERE task_id=? ORDER BY datetime(created_at) DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
        if not row:
            return None
        try:
            tid = row[0]
            lbl = row[1]
            combined = row[2]
            sections = row[3]
            meta = row[4]
            created_at = row[5]
        except Exception:
            tid = row["task_id"]
            lbl = row["label"]
            combined = row["combined"]
            sections = row["sections"]
            meta = row["meta"]
            created_at = row["created_at"]
        try:
            sections_obj = json.loads(sections) if isinstance(sections, str) else sections
        except Exception:
            sections_obj = []
        try:
            meta_obj = json.loads(meta) if isinstance(meta, str) else meta
        except Exception:
            meta_obj = {}
        return {
            "task_id": tid,
            "label": lbl,
            "combined": combined,
            "sections": sections_obj,
            "meta": meta_obj,
            "created_at": created_at,
        }

    def list_task_contexts(self, task_id: int) -> List[Dict[str, Any]]:
        with get_db() as conn:
            self._ensure_task_contexts_table(conn)
            rows = conn.execute(
                "SELECT label, created_at, meta FROM task_contexts WHERE task_id=? ORDER BY datetime(created_at) DESC",
                (task_id,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                lbl = r[0]
                created_at = r[1]
                meta = r[2]
            except Exception:
                lbl = r["label"]
                created_at = r["created_at"]
                meta = r["meta"]
            try:
                meta_obj = json.loads(meta) if isinstance(meta, str) else meta
            except Exception:
                meta_obj = {}
            out.append({"label": lbl, "created_at": created_at, "meta": meta_obj})
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
            row = conn.execute("SELECT parent_id FROM tasks WHERE id=?", (task_id,)).fetchone()
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
        """Get ancestors using optimized query to avoid N+1 problem."""
        return OptimizedTaskQueries.get_ancestors_optimized(task_id)

    def get_descendants(self, root_id: int) -> List[Dict[str, Any]]:
        """Get descendants using optimized query for better performance."""
        return OptimizedTaskQueries.get_descendants_with_details(root_id)

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
                old_depth = (row["depth"] or 0)

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
                if old_path and (p_path == old_path or p_path.startswith(old_path + "/")):
                    raise ValueError("Cannot move a task under its own subtree")

                new_parent_path = p_path
                new_parent_depth = (p_depth or 0)
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
                        desc_new_path = new_root_path + desc_old_path[len(old_path):]
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

    def store_task_embedding(self, task_id: int, embedding_vector: str, model: str = "embedding-2") -> None:
        """Store embedding vector for a task."""
        with get_db() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO task_embeddings 
                (task_id, embedding_vector, embedding_model, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (task_id, embedding_vector, model))
            conn.commit()

    def get_task_embedding(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get embedding for a specific task."""
        with get_db() as conn:
            row = conn.execute('''
                SELECT task_id, embedding_vector, embedding_model, created_at, updated_at
                FROM task_embeddings 
                WHERE task_id = ?
            ''', (task_id,)).fetchone()
            
            if row:
                return {
                    "task_id": row["task_id"],
                    "embedding_vector": row["embedding_vector"],
                    "embedding_model": row["embedding_model"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

    def get_tasks_with_embeddings(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all tasks that have embeddings with their content."""
        with get_db() as conn:
            query = '''
                SELECT 
                    t.id, t.name, t.status, t.priority,
                    toutput.content,
                    te.embedding_vector, te.embedding_model, te.updated_at
                FROM tasks t
                LEFT JOIN task_outputs toutput ON t.id = toutput.task_id
                INNER JOIN task_embeddings te ON t.id = te.task_id
                ORDER BY te.updated_at DESC
            '''
            
            if limit:
                query += f' LIMIT {limit}'
            
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
                        "updated_at": row[7]
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
                        "updated_at": row["updated_at"]
                    }
                result.append(task_dict)
            return result

    def get_tasks_without_embeddings(self, status: Optional[str] = "done") -> List[Dict[str, Any]]:
        """Get tasks that don't have embeddings yet."""
        with get_db() as conn:
            where_clause = ""
            params = []
            
            if status:
                where_clause = "WHERE t.status = ?"
                params.append(status)
            
            query = f'''
                SELECT t.id, t.name, t.status, t.priority, to.content
                FROM tasks t
                LEFT JOIN task_outputs to ON t.id = to.task_id
                LEFT JOIN task_embeddings te ON t.id = te.task_id
                {where_clause}
                AND te.task_id IS NULL
                AND to.content IS NOT NULL
                AND TRIM(to.content) != ""
                ORDER BY t.id DESC
            '''
            
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
            total_embeddings = conn.execute("SELECT COUNT(*) FROM task_embeddings").fetchone()[0]
            
            model_stats = conn.execute('''
                SELECT embedding_model, COUNT(*) as count
                FROM task_embeddings
                GROUP BY embedding_model
            ''').fetchall()
            
            return {
                "total_tasks": total_tasks,
                "total_embeddings": total_embeddings,
                "coverage_percent": (total_embeddings / total_tasks * 100) if total_tasks > 0 else 0,
                "model_distribution": {row[0]: row[1] for row in model_stats}
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
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Store evaluation history for a task iteration."""
        with get_db() as conn:
            cursor = conn.execute('''
                INSERT INTO evaluation_history 
                (task_id, iteration, content, overall_score, dimension_scores, suggestions, needs_revision, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task_id,
                iteration,
                content,
                overall_score,
                json.dumps(dimension_scores),
                json.dumps(suggestions),
                needs_revision,
                json.dumps(metadata) if metadata else None
            ))
            conn.commit()
            return cursor.lastrowid

    def get_evaluation_history(self, task_id: int) -> List[Dict[str, Any]]:
        """Get evaluation history for a task."""
        with get_db() as conn:
            rows = conn.execute('''
                SELECT id, task_id, iteration, content, overall_score, dimension_scores, 
                       suggestions, needs_revision, timestamp, metadata
                FROM evaluation_history
                WHERE task_id = ?
                ORDER BY iteration ASC
            ''', (task_id,)).fetchall()
            
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
                        "metadata": json.loads(row[9]) if row[9] else None
                    }
                except Exception:
                    # Fallback for sqlite3.Row objects
                    row_dict = {
                        "id": row["id"],
                        "task_id": row["task_id"],
                        "iteration": row["iteration"],
                        "content": row["content"],
                        "overall_score": row["overall_score"],
                        "dimension_scores": json.loads(row["dimension_scores"]) if row["dimension_scores"] else {},
                        "suggestions": json.loads(row["suggestions"]) if row["suggestions"] else [],
                        "needs_revision": bool(row["needs_revision"]),
                        "timestamp": row["timestamp"],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else None
                    }
                result.append(row_dict)
            return result

    def get_latest_evaluation(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get the latest evaluation for a task."""
        with get_db() as conn:
            row = conn.execute('''
                SELECT id, task_id, iteration, content, overall_score, dimension_scores, 
                       suggestions, needs_revision, timestamp, metadata
                FROM evaluation_history
                WHERE task_id = ?
                ORDER BY iteration DESC
                LIMIT 1
            ''', (task_id,)).fetchone()
            
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
                    "metadata": json.loads(row[9]) if row[9] else None
                }
            except Exception:
                # Fallback for sqlite3.Row objects
                return {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "iteration": row["iteration"],
                    "content": row["content"],
                    "overall_score": row["overall_score"],
                    "dimension_scores": json.loads(row["dimension_scores"]) if row["dimension_scores"] else {},
                    "suggestions": json.loads(row["suggestions"]) if row["suggestions"] else [],
                    "needs_revision": bool(row["needs_revision"]),
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None
                }

    def store_evaluation_config(
        self,
        task_id: int,
        quality_threshold: float = 0.8,
        max_iterations: int = 3,
        evaluation_dimensions: Optional[List[str]] = None,
        domain_specific: bool = False,
        strict_mode: bool = False,
        custom_weights: Optional[Dict[str, float]] = None
    ) -> None:
        """Store evaluation configuration for a task."""
        with get_db() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO evaluation_configs
                (task_id, quality_threshold, max_iterations, evaluation_dimensions, 
                 domain_specific, strict_mode, custom_weights, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                task_id,
                quality_threshold,
                max_iterations,
                json.dumps(evaluation_dimensions) if evaluation_dimensions else None,
                domain_specific,
                strict_mode,
                json.dumps(custom_weights) if custom_weights else None
            ))
            conn.commit()

    def get_evaluation_config(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get evaluation configuration for a task."""
        with get_db() as conn:
            row = conn.execute('''
                SELECT task_id, quality_threshold, max_iterations, evaluation_dimensions,
                       domain_specific, strict_mode, custom_weights, created_at, updated_at
                FROM evaluation_configs
                WHERE task_id = ?
            ''', (task_id,)).fetchone()
            
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
                    "updated_at": row[8]
                }
            except Exception:
                # Fallback for sqlite3.Row objects
                return {
                    "task_id": row["task_id"],
                    "quality_threshold": row["quality_threshold"],
                    "max_iterations": row["max_iterations"],
                    "evaluation_dimensions": json.loads(row["evaluation_dimensions"]) if row["evaluation_dimensions"] else None,
                    "domain_specific": bool(row["domain_specific"]),
                    "strict_mode": bool(row["strict_mode"]),
                    "custom_weights": json.loads(row["custom_weights"]) if row["custom_weights"] else None,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }

    def delete_evaluation_history(self, task_id: int) -> None:
        """Delete all evaluation history for a task."""
        with get_db() as conn:
            conn.execute("DELETE FROM evaluation_history WHERE task_id = ?", (task_id,))
            conn.commit()

    def get_evaluation_stats(self) -> Dict[str, Any]:
        """Get overall evaluation statistics."""
        with get_db() as conn:
            total_evaluations = conn.execute("SELECT COUNT(*) FROM evaluation_history").fetchone()[0]
            
            avg_score = conn.execute('''
                SELECT AVG(overall_score) FROM evaluation_history
            ''').fetchone()[0] or 0.0
            
            iteration_stats = conn.execute('''
                SELECT AVG(iteration) as avg_iterations, MAX(iteration) as max_iterations
                FROM (
                    SELECT task_id, MAX(iteration) as iteration
                    FROM evaluation_history
                    GROUP BY task_id
                )
            ''').fetchone()
            
            quality_distribution = conn.execute('''
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
            ''').fetchall()
            
            return {
                "total_evaluations": total_evaluations,
                "average_score": round(avg_score, 3),
                "average_iterations": round(iteration_stats[0] or 0, 2),
                "max_iterations_used": iteration_stats[1] or 0,
                "quality_distribution": {row[0]: row[1] for row in quality_distribution}
            }


default_repo = SqliteTaskRepository()
