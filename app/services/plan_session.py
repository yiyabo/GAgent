"""In-memory plan session cache for conversational workflows.

This module keeps a plan's task graph resident in memory while a user is
interacting with it, so we can serve repeated queries without constantly
round-tripping to SQLite. Updates are applied in a write-through fashion to
preserve existing behaviour, but the session also keeps the canonical graph
structure locally so downstream calls can reason about relationships without
additional queries.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from ..repository.tasks import default_repo


@dataclass
class TaskNode:
    """Lightweight representation of a task within a plan graph."""

    id: int  # logical identifier
    name: str
    status: str
    priority: int
    task_type: str
    parent_id: Optional[int]
    path: Optional[str] = None
    depth: Optional[int] = None
    instruction: Optional[str] = None
    children: List[int] = field(default_factory=list)
    db_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "db_id": self.db_id,
            "name": self.name,
            "status": self.status,
            "priority": self.priority,
            "task_type": self.task_type,
            "parent_id": self.parent_id,
            "path": self.path,
            "depth": self.depth,
        }


class PlanGraphSession:
    """Caches a plan's graph in memory and mirrors updates to the repo."""

    def __init__(self, plan_id: int):
        self.plan_id = plan_id
        self._lock = threading.RLock()
        self._loaded = False
        self._plan: Optional[Dict[str, Any]] = None
        self._tasks: Dict[int, TaskNode] = {}
        self._instructions: Dict[int, str] = {}
        self._last_used: float = time.time()
        self._dirty_tasks: Dict[int, Dict[str, Any]] = {}
        self._dirty_instructions: Dict[int, str] = {}
        self._new_tasks: Dict[int, TaskNode] = {}
        self._deleted_tasks: set[int] = set()  # stores db identifiers
        self._temp_id_counter: int = -1
        self._reparent_ops: Dict[int, Optional[int]] = {}
        self._db_to_logical: Dict[int, int] = {}
        self._logical_to_db: Dict[int, Optional[int]] = {}
        self._next_logical_id: int = 1
        self._context_snapshots: Dict[int, List[Dict[str, Any]]] = {}
        self._output_cache: Dict[int, Optional[str]] = {}

    def touch(self) -> None:
        self._last_used = time.time()

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def plan(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return self._plan

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._plan = default_repo.get_plan(self.plan_id)
            tasks = default_repo.get_plan_tasks(self.plan_id)
            graph: Dict[int, TaskNode] = {}
            parent_lookup: Dict[int, Optional[int]] = {}

            existing_db_to_logical = dict(self._db_to_logical)
            self._db_to_logical.clear()
            self._logical_to_db.clear()

            sorted_tasks = sorted(
                tasks,
                key=lambda t: ((t.get("depth") or 0), t.get("id", 0))
            )

            used_logical_ids: set[int] = set()
            next_logical_candidate = max(existing_db_to_logical.values(), default=0) + 1

            for task in sorted_tasks:
                db_id = task.get("id")
                logical_id = existing_db_to_logical.get(db_id)
                if logical_id is None:
                    logical_id = next_logical_candidate
                    next_logical_candidate += 1

                used_logical_ids.add(logical_id)
                node = TaskNode(
                    id=logical_id,
                    name=task["name"],
                    status=task["status"],
                    priority=task["priority"],
                    task_type=task.get("task_type", "atomic"),
                    parent_id=None,  # assigned after mapping established
                    path=task.get("path"),
                    depth=task.get("depth"),
                    children=[],
                    db_id=db_id,
                )
                graph[logical_id] = node
                parent_lookup[logical_id] = task.get("parent_id")
                if db_id is not None:
                    self._db_to_logical[db_id] = logical_id
                self._logical_to_db[logical_id] = db_id

            for logical_id, parent_db_id in parent_lookup.items():
                node = graph[logical_id]
                if parent_db_id is None:
                    node.parent_id = None
                else:
                    parent_logical = self._db_to_logical.get(parent_db_id)
                    node.parent_id = parent_logical
                    if parent_logical is not None and parent_logical in graph:
                        graph[parent_logical].children.append(logical_id)

            for node in graph.values():
                if node.children:
                    node.children = sorted(node.children)

            self._tasks = graph
            self._loaded = True
            self._dirty_tasks.clear()
            self._dirty_instructions.clear()
            self._new_tasks.clear()
            self._deleted_tasks.clear()
            self._reparent_ops.clear()
            self._context_snapshots.clear()
            self._output_cache.clear()
            # Ensure next logical id continues after current tasks
            self._next_logical_id = max(used_logical_ids, default=0) + 1

    def ensure_loaded(self) -> None:
        """Public helper to force-load the plan graph into memory."""
        self._ensure_loaded()

    def _get_node(self, task_id: int) -> Optional[TaskNode]:
        self._ensure_loaded()
        return self._tasks.get(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [node.to_dict() for node in sorted(self._tasks.values(), key=lambda n: n.id)]

    def list_task_summaries(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [
                {"id": node.id, "db_id": node.db_id, "name": node.name}
                for node in sorted(self._tasks.values(), key=lambda n: n.id)
            ]

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            node = self._get_node(task_id)
            return node.to_dict() if node else None

    def get_or_fetch_instruction(self, task_id: int) -> Optional[str]:
        with self._lock:
            mapped_id = task_id
            if task_id < 0:
                mapped_id = task_id
            if task_id in self._instructions:
                return self._instructions[task_id]
            db_id = self._logical_to_db.get(task_id, task_id)
            if db_id is None:
                return None
            prompt = default_repo.get_task_input(db_id)
            if prompt is not None:
                self._instructions[mapped_id] = prompt
            return prompt

    def set_instruction(self, task_id: int, prompt: str) -> None:
        with self._lock:
            self._instructions[task_id] = prompt
            self._dirty_instructions[task_id] = prompt

    def get_db_id(self, logical_id: int) -> Optional[int]:
        with self._lock:
            return self._logical_to_db.get(logical_id)

    def get_logical_id(self, db_id: int) -> Optional[int]:
        with self._lock:
            return self._db_to_logical.get(db_id)

    def get_child_ids(self, task_id: int) -> List[int]:
        with self._lock:
            node = self._get_node(task_id)
            if not node or not node.children:
                return []
            return list(node.children)

    def get_root_task_ids(self) -> List[int]:
        with self._lock:
            self._ensure_loaded()
            return sorted(
                node.id for node in self._tasks.values() if node.parent_id is None
            )

    def get_task_context_snapshots(
        self, task_id: int, refresh: bool = False
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            if refresh:
                self._context_snapshots.pop(task_id, None)

            if task_id in self._context_snapshots:
                return copy.deepcopy(self._context_snapshots[task_id])

            db_id = self._logical_to_db.get(task_id)
            if db_id is None:
                contexts: List[Dict[str, Any]] = []
            else:
                try:
                    contexts = default_repo.list_task_contexts(db_id) or []
                except Exception:
                    contexts = []

            self._context_snapshots[task_id] = contexts
            return copy.deepcopy(contexts)

    def get_task_output(
        self, task_id: int, refresh: bool = False
    ) -> Optional[str]:
        with self._lock:
            self._ensure_loaded()
            if refresh:
                self._output_cache.pop(task_id, None)

            if task_id in self._output_cache:
                return self._output_cache[task_id]

            db_id = self._logical_to_db.get(task_id)
            if db_id is None:
                output: Optional[str] = None
            else:
                try:
                    output = default_repo.get_task_output_content(db_id)
                except Exception:
                    output = None

            self._output_cache[task_id] = output
            return output

    def create_task(
        self,
        name: str,
        parent_id: Optional[int] = None,
        status: str = "pending",
        priority: Optional[int] = None,
        task_type: str = "atomic",
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            logical_id = self._next_logical_id
            self._next_logical_id += 1

            parent_depth = 0
            if parent_id is not None and parent_id in self._tasks:
                parent_depth = (self._tasks[parent_id].depth or 0) + 1

            node = TaskNode(
                id=logical_id,
                name=name,
                status=status,
                priority=priority if priority is not None else 100,
                task_type=task_type,
                parent_id=parent_id,
                depth=parent_depth,
                db_id=None,
            )
            self._tasks[logical_id] = node
            self._new_tasks[logical_id] = node
            self._logical_to_db[logical_id] = None
            if parent_id is not None and parent_id in self._tasks:
                parent = self._tasks[parent_id]
                parent.children.append(logical_id)
            return node.to_dict()

    def update_task(self, task_id: int, **updates: Any) -> bool:
        with self._lock:
            node = self._get_node(task_id)
            if not node:
                return False

            changes: Dict[str, Any] = {}
            if "name" in updates and updates["name"] is not None:
                node.name = updates["name"]
                changes["name"] = updates["name"]
            if "status" in updates and updates["status"] is not None:
                node.status = updates["status"]
                changes["status"] = updates["status"]
            if "priority" in updates and updates["priority"] is not None:
                node.priority = updates["priority"]
                changes["priority"] = updates["priority"]
            if "task_type" in updates and updates["task_type"] is not None:
                node.task_type = updates["task_type"]
                changes["task_type"] = updates["task_type"]

            if task_id not in self._new_tasks:
                existing_changes = self._dirty_tasks.setdefault(task_id, {})
                existing_changes.update(changes)
            return True

    def delete_task(self, task_id: int) -> Dict[str, Any]:
        """Stage deletion of a task (and its descendants) from the session."""

        with self._lock:
            self._ensure_loaded()
            node = self._tasks.get(task_id)
            if not node:
                raise ValueError(f"Task {task_id} not found in session")

            to_remove: List[int] = []
            stack: List[int] = [task_id]
            while stack:
                current_id = stack.pop()
                current = self._tasks.get(current_id)
                if not current:
                    continue
                to_remove.append(current_id)
                if current.children:
                    stack.extend(current.children)

            parent_id = node.parent_id
            if parent_id is not None and parent_id in self._tasks:
                parent_node = self._tasks[parent_id]
                parent_node.children = [child for child in parent_node.children if child not in to_remove]

            removed_nodes: List[Dict[str, Any]] = []
            for remove_id in to_remove:
                current = self._tasks.pop(remove_id, None)
                if not current:
                    continue
                removed_nodes.append(current.to_dict())
                # Remove from new/dirty caches
                self._new_tasks.pop(remove_id, None)
                self._dirty_tasks.pop(remove_id, None)
                self._dirty_instructions.pop(remove_id, None)
                self._instructions.pop(remove_id, None)
                self._reparent_ops.pop(remove_id, None)
                self._logical_to_db.pop(remove_id, None)
                self._context_snapshots.pop(remove_id, None)
                self._output_cache.pop(remove_id, None)
                if current.db_id is not None:
                    self._db_to_logical.pop(current.db_id, None)

                if current.db_id is not None:
                    self._deleted_tasks.add(current.db_id)

            return {
                "removed_ids": to_remove,
                "removed_nodes": removed_nodes,
                "parent_id": parent_id,
            }

    def build_task_tree(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            clone: Dict[int, Dict[str, Any]] = {}
            for node in self._tasks.values():
                clone[node.id] = {**node.to_dict(), "children": []}
            roots: List[Dict[str, Any]] = []
            for node in self._tasks.values():
                if node.parent_id and node.parent_id in clone:
                    clone[node.parent_id]["children"].append(clone[node.id])
                else:
                    roots.append(clone[node.id])
            return roots

    def refresh(self) -> None:
        with self._lock:
            self._loaded = False
            self._tasks.clear()
            self._instructions.clear()
            self._dirty_tasks.clear()
            self._dirty_instructions.clear()
            self._new_tasks.clear()
            self._deleted_tasks.clear()
            self._reparent_ops.clear()
            self._context_snapshots.clear()
            self._output_cache.clear()
            self._ensure_loaded()

    def has_pending_changes(self) -> bool:
        with self._lock:
            return bool(
                self._new_tasks
                or self._dirty_tasks
                or self._dirty_instructions
                or self._deleted_tasks
                or self._reparent_ops
            )

    def _remap_children(self, id_map: Dict[int, int]) -> None:
        for node in self._tasks.values():
            if node.children:
                node.children = [id_map.get(child, child) for child in node.children]

    def commit(self) -> Dict[int, int]:
        """Persist staged changes to the database. Returns logical→db mapping for newly inserted tasks."""

        with self._lock:
            if not (
                self._new_tasks
                or self._dirty_tasks
                or self._dirty_instructions
                or self._deleted_tasks
                or self._reparent_ops
            ):
                return {}

            new_id_map: Dict[int, int] = {}

            if self._new_tasks:
                new_items = sorted(
                    self._new_tasks.items(),
                    key=lambda item: self._tasks[item[0]].depth or 0,
                )
                for logical_id, node in new_items:
                    parent_logical = node.parent_id
                    parent_db = (
                        None
                        if parent_logical is None
                        else self._logical_to_db.get(parent_logical)
                    )

                    new_db_id = default_repo.create_task(
                        name=node.name,
                        status=node.status,
                        priority=node.priority,
                        parent_id=parent_db,
                        task_type=node.task_type,
                    )
                    default_repo.link_task_to_plan(self.plan_id, new_db_id)
                    info = default_repo.get_task_info(new_db_id) or {}

                    node.db_id = new_db_id
                    node.status = info.get("status", node.status)
                    node.priority = info.get("priority", node.priority)
                    node.task_type = info.get("task_type", node.task_type)
                    node.path = info.get("path")
                    node.depth = info.get("depth", node.depth)

                    self._logical_to_db[logical_id] = new_db_id
                    self._db_to_logical[new_db_id] = logical_id
                    new_id_map[logical_id] = new_db_id

                self._new_tasks.clear()

            if self._dirty_tasks:
                for logical_id, changes in list(self._dirty_tasks.items()):
                    db_id = self._logical_to_db.get(logical_id)
                    if not db_id:
                        continue
                    default_repo.update_task(
                        task_id=db_id,
                        name=changes.get("name"),
                        status=changes.get("status"),
                        priority=changes.get("priority"),
                        task_type=changes.get("task_type"),
                    )
                    node = self._tasks.get(logical_id)
                    if node:
                        if "name" in changes:
                            node.name = changes["name"]
                        if "status" in changes:
                            node.status = changes["status"]
                        if "priority" in changes:
                            node.priority = changes["priority"]
                        if "task_type" in changes:
                            node.task_type = changes["task_type"]
                self._dirty_tasks.clear()

            if self._dirty_instructions:
                for logical_id, prompt in list(self._dirty_instructions.items()):
                    db_id = self._logical_to_db.get(logical_id)
                    if not db_id:
                        continue
                    default_repo.upsert_task_input(db_id, prompt)
                    self._instructions[logical_id] = prompt
                self._dirty_instructions.clear()

            if self._deleted_tasks:
                for db_id in list(self._deleted_tasks):
                    default_repo.delete_task(db_id)
                    logical_id = self._db_to_logical.pop(db_id, None)
                    if logical_id is not None:
                        self._logical_to_db.pop(logical_id, None)
                self._deleted_tasks.clear()

            if self._reparent_ops:
                for logical_id, new_parent_logical in list(self._reparent_ops.items()):
                    task_db = self._logical_to_db.get(logical_id)
                    if not task_db:
                        continue
                    new_parent_db = (
                        None
                        if new_parent_logical is None
                        else self._logical_to_db.get(new_parent_logical)
                    )
                    default_repo.update_task_parent(task_db, new_parent_db)
                self._reparent_ops.clear()
                # Refresh structure to keep metadata in sync
                self.refresh()
            else:
                # Refresh plan metadata if we didn't already reload
                self._plan = default_repo.get_plan(self.plan_id)

            return new_id_map

    def move_task(self, task_id: int, new_parent_id: Optional[int]) -> bool:
        """Schedule a move operation for a task to a new parent."""

        with self._lock:
            self._ensure_loaded()
            node = self._tasks.get(task_id)
            if not node:
                raise ValueError(f"Task {task_id} not found in session")

            if new_parent_id == node.parent_id:
                return False

            if new_parent_id is not None and new_parent_id not in self._tasks:
                raise ValueError(f"Parent task {new_parent_id} not found in session")

            # Prevent cycles: ensure new parent isn't a descendant
            if new_parent_id is not None:
                cursor = new_parent_id
                while cursor is not None:
                    if cursor == task_id:
                        raise ValueError("Cannot move a task under its own subtree")
                    parent = self._tasks.get(cursor)
                    cursor = parent.parent_id if parent else None

            # Update in-memory parent references
            if node.parent_id is not None and node.parent_id in self._tasks:
                parent = self._tasks[node.parent_id]
                if node.id in parent.children:
                    parent.children = [c for c in parent.children if c != node.id]

            node.parent_id = new_parent_id

            if new_parent_id is not None and new_parent_id in self._tasks:
                parent = self._tasks[new_parent_id]
                if node.id not in parent.children:
                    parent.children.append(node.id)

            self._tasks[task_id] = node
            if task_id not in self._new_tasks:
                self._reparent_ops[task_id] = new_parent_id
            else:
                # For new nodes, just update their stored parent
                self._new_tasks[task_id].parent_id = new_parent_id
            return True


class PlanSessionManager:
    """Maintains plan sessions keyed by plan_id."""

    def __init__(self):
        self._sessions: Dict[int, PlanGraphSession] = {}
        self._lock = threading.RLock()

    def get_session(self, plan_id: int) -> PlanGraphSession:
        with self._lock:
            session = self._sessions.get(plan_id)
            if session is None:
                session = PlanGraphSession(plan_id)
                self._sessions[plan_id] = session
            session.touch()
            return session

    def release_session(self, plan_id: int) -> None:
        with self._lock:
            if plan_id in self._sessions:
                del self._sessions[plan_id]

    def activate_plan(self, plan_id: int) -> PlanGraphSession:
        """Clear other plan caches and eagerly load the requested plan."""
        with self._lock:
            # Drop any cached sessions for other plans
            for cached_id in list(self._sessions.keys()):
                if cached_id != plan_id:
                    del self._sessions[cached_id]

            session = self._sessions.get(plan_id)
            if session is None:
                session = PlanGraphSession(plan_id)
                self._sessions[plan_id] = session
            session.touch()

        session.ensure_loaded()
        return session

    def flush_stale(self, ttl_seconds: float = 1800.0) -> None:
        """Drop sessions that have been idle longer than ttl_seconds."""
        now = time.time()
        with self._lock:
            stale = [
                plan_id
                for plan_id, session in self._sessions.items()
                if now - session.last_used > ttl_seconds
            ]
            for plan_id in stale:
                del self._sessions[plan_id]


plan_session_manager = PlanSessionManager()

__all__ = ["PlanGraphSession", "PlanSessionManager", "plan_session_manager"]
