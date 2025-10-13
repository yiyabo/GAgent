"""
Optimized database queries to resolve N+1 query problems.

Provides batch query methods and efficient data fetching strategies.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional, Set

from app.database_pool import get_db

logger = logging.getLogger(__name__)


class OptimizedTaskQueries:
    """Optimized query methods to prevent N+1 problems."""

    @staticmethod
    def batch_get_tasks(task_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """
        Fetch multiple tasks in a single query.

        Args:
            task_ids: List of task IDs to fetch

        Returns:
            Dictionary mapping task_id to task data
        """
        if not task_ids:
            return {}

        with get_db() as conn:
            placeholders = ",".join(["?"] * len(task_ids))
            query = f"""
                SELECT id, name, description, status, priority, parent_id, 
                       path, depth, task_type, content, dependencies, 
                       context_summary, evaluation_score, created_at, updated_at
                FROM tasks 
                WHERE id IN ({placeholders})
            """

            rows = conn.execute(query, task_ids).fetchall()

            # Convert to dictionary for O(1) lookup
            result = {}
            for row in rows:
                task_data = {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "parent_id": row["parent_id"],
                    "path": row["path"],
                    "depth": row["depth"],
                    "task_type": row["task_type"],
                    "content": row["content"],
                    "dependencies": row["dependencies"],
                    "context_summary": row["context_summary"],
                    "evaluation_score": row["evaluation_score"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                result[row["id"]] = task_data

            return result

    @staticmethod
    def get_ancestors_optimized(task_id: int) -> List[Dict[str, Any]]:
        """
        Get all ancestors of a task with a single query.

        Args:
            task_id: Task ID

        Returns:
            List of ancestor tasks ordered by depth (root first)
        """
        with get_db() as conn:
            # Use recursive CTE to get all ancestors in one query
            query = """
                WITH RECURSIVE ancestors AS (
                    -- Base case: get the task itself
                    SELECT id, name, status, priority, parent_id, path, depth, task_type
                    FROM tasks WHERE id = ?
                    
                    UNION ALL
                    
                    -- Recursive case: get parent of current task
                    SELECT t.id, t.name, t.status, t.priority, t.parent_id, 
                           t.path, t.depth, t.task_type
                    FROM tasks t
                    INNER JOIN ancestors a ON t.id = a.parent_id
                )
                SELECT * FROM ancestors 
                WHERE id != ?  -- Exclude the task itself
                ORDER BY depth ASC
            """

            rows = conn.execute(query, (task_id, task_id)).fetchall()

            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "parent_id": row["parent_id"],
                    "path": row["path"],
                    "depth": row["depth"],
                    "task_type": row["task_type"],
                }
                for row in rows
            ]

    @staticmethod
    def get_descendants_with_details(root_id: int) -> List[Dict[str, Any]]:
        """
        Get all descendants with full details in a single query.

        Args:
            root_id: Root task ID

        Returns:
            List of descendant tasks with full details
        """
        with get_db() as conn:
            # Use recursive CTE for efficient descendant fetching
            query = """
                WITH RECURSIVE descendants AS (
                    -- Base case: get the root task
                    SELECT id, name, status, priority, parent_id, 
                           path, depth, task_type
                    FROM tasks WHERE id = ?
                    
                    UNION ALL
                    
                    -- Recursive case: get children of current tasks
                    SELECT t.id, t.name, t.status, t.priority, 
                           t.parent_id, t.path, t.depth, t.task_type
                    FROM tasks t
                    INNER JOIN descendants d ON t.parent_id = d.id
                )
                SELECT * FROM descendants 
                WHERE id != ?  -- Exclude root itself
                ORDER BY path ASC
            """

            rows = conn.execute(query, (root_id, root_id)).fetchall()

            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "parent_id": row["parent_id"],
                    "path": row["path"],
                    "depth": row["depth"],
                    "task_type": row["task_type"],
                }
                for row in rows
            ]

    @staticmethod
    def get_tasks_with_dependencies(task_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """
        Get tasks with their dependencies pre-loaded.

        Args:
            task_ids: List of task IDs

        Returns:
            Dictionary of tasks with dependencies resolved
        """
        if not task_ids:
            return {}

        with get_db() as conn:
            # First, get all tasks
            tasks = OptimizedTaskQueries.batch_get_tasks(task_ids)

            # Collect all dependency IDs
            all_dep_ids = set()
            for task in tasks.values():
                if task.get("dependencies"):
                    try:
                        import json

                        deps = json.loads(task["dependencies"])
                        if isinstance(deps, list):
                            all_dep_ids.update(deps)
                    except:
                        pass

            # Batch fetch all dependencies
            if all_dep_ids:
                dep_tasks = OptimizedTaskQueries.batch_get_tasks(list(all_dep_ids))

                # Attach dependency details to tasks
                for task_id, task in tasks.items():
                    if task.get("dependencies"):
                        try:
                            import json

                            deps = json.loads(task["dependencies"])
                            if isinstance(deps, list):
                                task["dependency_details"] = [
                                    dep_tasks.get(dep_id) for dep_id in deps if dep_id in dep_tasks
                                ]
                        except:
                            task["dependency_details"] = []

            return tasks

    @staticmethod
    def get_subtree_with_children(root_id: int) -> Dict[str, Any]:
        """
        Get complete subtree with parent-child relationships pre-loaded.

        Args:
            root_id: Root task ID

        Returns:
            Tree structure with tasks and their children
        """
        with get_db() as conn:
            # Get all tasks in subtree with one query
            query = """
                SELECT id, name, status, priority, parent_id,
                       path, depth, task_type
                FROM tasks
                WHERE path LIKE (
                    SELECT path || '/%' FROM tasks WHERE id = ?
                )
                OR id = ?
                ORDER BY depth ASC, priority ASC
            """

            rows = conn.execute(query, (root_id, root_id)).fetchall()

            # Build tree structure
            tasks_by_id = {}
            root = None

            for row in rows:
                task = {
                    "id": row["id"],
                    "name": row["name"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "parent_id": row["parent_id"],
                    "path": row["path"],
                    "depth": row["depth"],
                    "task_type": row["task_type"],
                    "children": [],
                }

                tasks_by_id[task["id"]] = task

                if task["id"] == root_id:
                    root = task

            # Build parent-child relationships
            for task in tasks_by_id.values():
                if task["parent_id"] and task["parent_id"] in tasks_by_id:
                    parent = tasks_by_id[task["parent_id"]]
                    parent["children"].append(task)

            return root or {"error": "Root task not found"}

    @staticmethod
    def batch_update_status(task_ids: List[int], status: str) -> int:
        """
        Update status for multiple tasks in one query.

        Args:
            task_ids: List of task IDs
            status: New status value

        Returns:
            Number of tasks updated
        """
        if not task_ids:
            return 0

        with get_db() as conn:
            placeholders = ",".join(["?"] * len(task_ids))
            query = f"""
                UPDATE tasks 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
            """

            cursor = conn.execute(query, [status] + task_ids)
            return cursor.rowcount

    @staticmethod
    def get_task_statistics() -> Dict[str, Any]:
        """
        Get task statistics with optimized queries.

        Returns:
            Dictionary with various statistics
        """
        with get_db() as conn:
            # Get all statistics in a single query
            query = """
                SELECT 
                    COUNT(*) as total_tasks,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_tasks,
                    COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress_tasks,
                    COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending_tasks,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed_tasks,
                    AVG(evaluation_score) as avg_evaluation_score,
                    MAX(depth) as max_depth,
                    COUNT(DISTINCT CASE WHEN parent_id IS NULL THEN id END) as root_tasks
                FROM tasks
            """

            row = conn.execute(query).fetchone()

            return {
                "total_tasks": row["total_tasks"],
                "completed_tasks": row["completed_tasks"],
                "in_progress_tasks": row["in_progress_tasks"],
                "pending_tasks": row["pending_tasks"],
                "failed_tasks": row["failed_tasks"],
                "avg_evaluation_score": row["avg_evaluation_score"],
                "max_depth": row["max_depth"],
                "root_tasks": row["root_tasks"],
                "completion_rate": row["completed_tasks"] / row["total_tasks"] if row["total_tasks"] > 0 else 0,
            }

    @staticmethod
    def preload_task_graph(root_ids: Optional[List[int]] = None) -> Dict[int, Dict[str, Any]]:
        """
        Preload entire task graph for efficient traversal.

        Args:
            root_ids: Optional list of root task IDs to load. If None, loads all.

        Returns:
            Dictionary of all tasks with relationships pre-loaded
        """
        with get_db() as conn:
            if root_ids:
                placeholders = ",".join(["?"] * len(root_ids))
                query = f"""
                    SELECT * FROM tasks 
                    WHERE id IN ({placeholders})
                    OR path LIKE ANY (
                        SELECT path || '/%' FROM tasks WHERE id IN ({placeholders})
                    )
                """
                rows = conn.execute(query, root_ids + root_ids).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks").fetchall()

            # Build complete graph
            task_graph = {}
            for row in rows:
                task_id = row["id"]
                task_graph[task_id] = {
                    "id": task_id,
                    "name": row["name"],
                    "description": row["description"],
                    "status": row["status"],
                    "priority": row["priority"],
                    "parent_id": row["parent_id"],
                    "path": row["path"],
                    "depth": row["depth"],
                    "task_type": row["task_type"],
                    "content": row["content"],
                    "dependencies": row["dependencies"],
                    "children": [],
                    "parent": None,
                }

            # Build relationships
            for task in task_graph.values():
                if task["parent_id"] and task["parent_id"] in task_graph:
                    parent = task_graph[task["parent_id"]]
                    parent["children"].append(task["id"])
                    task["parent"] = task["parent_id"]

            logger.info(f"Preloaded {len(task_graph)} tasks into graph")
            return task_graph
