from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Set

from ..database import get_db, plan_db_connection
from ..services.plans.plan_models import PlanNode, PlanSummary, PlanTree
from .plan_storage import (
    get_plan_db_path,
    initialize_plan_database,
    remove_plan_database,
    update_plan_metadata,
)

logger = logging.getLogger(__name__)


class PlanRepository:
    """Repository for plan metadata (main DB) and per-plan SQLite storage."""

    def __init__(self) -> None:
        self._execution_column_checked: set[int] = set()
        self._status_column_checked: set[int] = set()

    def list_plans(self) -> List[PlanSummary]:
        sql = """
        SELECT id, title, description, metadata, plan_db_path, updated_at
        FROM plans
        ORDER BY updated_at DESC, id DESC
        """
        with get_db() as conn:
            rows = conn.execute(sql).fetchall()

        summaries: List[PlanSummary] = []
        for row in rows:
            plan_id = row["id"]
            summaries.append(
                PlanSummary(
                    id=plan_id,
                    title=row["title"],
                    description=row["description"],
                    metadata=_loads_json(row["metadata"]),
                    task_count=self._count_tasks(plan_id),
                    updated_at=row["updated_at"],
                )
            )
        return summaries

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        plan_row = self._get_plan_record(plan_id)
        task_rows, dependency_map = self._load_tasks_and_dependencies(plan_id)
        return _rows_to_plan_tree(plan_id, plan_row, task_rows, dependency_map)

    def get_plan_summary(self, plan_id: int) -> PlanSummary:
        plan_row = self._get_plan_record(plan_id)
        return PlanSummary(
            id=plan_row["id"],
            title=plan_row["title"],
            description=plan_row["description"],
            metadata=_loads_json(plan_row["metadata"]),
            task_count=self._count_tasks(plan_id),
            updated_at=plan_row["updated_at"],
        )

    def create_plan(
        self,
        title: str,
        *,
        owner: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PlanTree:
        metadata_json = _dump_json(metadata or {})
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO plans (title, owner, description, metadata, plan_db_path)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (title, owner, description, metadata_json),
            )
            plan_id = cursor.lastrowid
            plan_rel_path = f"plan_{plan_id}.sqlite"
            conn.execute(
                """
                UPDATE plans
                SET plan_db_path=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (plan_rel_path, plan_id),
            )

        initialize_plan_database(
            plan_id,
            title=title,
            description=description,
            metadata=metadata or {},
        )
        return self.get_plan_tree(plan_id)

    def delete_plan(self, plan_id: int) -> None:
        remove_plan_database(plan_id)
        with get_db() as conn:
            conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))

    def create_task(
        self,
        plan_id: int,
        *,
        name: str,
        status: str = "pending",
        instruction: Optional[str] = None,
        parent_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[int]] = None,
        position: Optional[int] = None,
        anchor_task_id: Optional[int] = None,
        anchor_position: Optional[str] = None,
    ) -> PlanNode:
        status = (status or "pending").strip() or "pending"
        merged_metadata = _merge_metadata(metadata, dependencies)
        metadata_json = _dump_json(merged_metadata)
        deps = _sanitize_dependencies(dependencies)

        context_sections_json = _dump_json_list([])
        context_meta_json = _dump_json({})

        with plan_db_connection(get_plan_db_path(plan_id)) as conn:
            self._ensure_task_columns(conn, plan_id)
            parent_info = self._fetch_parent_info(conn, parent_id)
            position = self._prepare_insert_position(
                conn=conn,
                parent_id=parent_id,
                requested_position=position,
                anchor_task_id=anchor_task_id,
                anchor_position=anchor_position,
            )
            if position is None:
                position = self._next_position(conn, parent_id)
            depth = (parent_info["depth"] + 1) if parent_info else 0
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    name, status, instruction, parent_id, position, depth, path,
                    metadata, execution_result, context_combined, context_sections, context_meta
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    status,
                    instruction,
                    parent_id,
                    position,
                    depth,
                    "",
                    metadata_json,
                    None,
                    None,
                    context_sections_json,
                    context_meta_json,
                ),
            )
            task_id = cursor.lastrowid
            new_path = _build_path(parent_info["path"] if parent_info else "", task_id)
            conn.execute(
                "UPDATE tasks SET path=?, depth=? WHERE id=?",
                (new_path, depth, task_id),
            )
            self._replace_dependencies(conn, task_id, deps)
            self._resequence_children(conn, parent_id)

        self._touch_plan(plan_id)
        return self.get_node(plan_id, task_id)

    def update_task(
        self,
        plan_id: int,
        task_id: int,
        *,
        name: Optional[str] = None,
        status: Optional[str] = None,
        instruction: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[int]] = None,
        context_combined: Optional[str] = None,
        context_sections: Optional[List[Any]] = None,
        context_meta: Optional[Dict[str, Any]] = None,
        execution_result: Optional[str] = None,
    ) -> PlanNode:
        sets: List[str] = []
        params: List[Any] = []
        if name is not None:
            sets.append("name=?")
            params.append(name)
        if instruction is not None:
            sets.append("instruction=?")
            params.append(instruction)
        if metadata is not None or dependencies is not None:
            merged_metadata = _merge_metadata(metadata, dependencies)
            sets.append("metadata=?")
            params.append(_dump_json(merged_metadata))
        if status is not None:
            status = status.strip()
            sets.append("status=?")
            params.append(status)
        if execution_result is not None:
            sets.append("execution_result=?")
            params.append(execution_result)

        context_changed = False
        if context_combined is not None:
            sets.append("context_combined=?")
            params.append(context_combined)
            context_changed = True
        if context_sections is not None:
            sets.append("context_sections=?")
            params.append(_dump_json_list(context_sections))
            context_changed = True
        if context_meta is not None:
            sets.append("context_meta=?")
            params.append(_dump_json(context_meta))
            context_changed = True
        if context_changed:
            sets.append("context_updated_at=CURRENT_TIMESTAMP")

        deps = dependencies

        with plan_db_connection(get_plan_db_path(plan_id)) as conn:
            self._ensure_task_columns(conn, plan_id)
            if sets:
                params.extend([task_id])
                sql = f"UPDATE tasks SET {', '.join(sets)}, updated_at=CURRENT_TIMESTAMP WHERE id=?"
                updated = conn.execute(sql, params).rowcount
                if updated == 0:
                    raise ValueError(f"Task {task_id} not found in plan {plan_id}")
            if deps is not None:
                self._replace_dependencies(conn, task_id, _sanitize_dependencies(deps))

        self._touch_plan(plan_id)
        return self.get_node(plan_id, task_id)

    def delete_task(self, plan_id: int, task_id: int) -> None:
        with plan_db_connection(get_plan_db_path(plan_id)) as conn:
            row = conn.execute(
                "SELECT path, parent_id FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Task {task_id} not found in plan {plan_id}")
            path = row["path"] or f"/{task_id}"
            parent_id = row["parent_id"]
            conn.execute(
                "DELETE FROM tasks WHERE id=? OR path LIKE ?",
                (task_id, f"{path}/%"),
            )
            self._resequence_children(conn, parent_id)

        self._touch_plan(plan_id)

    def move_task(
        self,
        plan_id: int,
        task_id: int,
        *,
        new_parent_id: Optional[int],
        new_position: Optional[int] = None,
    ) -> PlanNode:
        with plan_db_connection(get_plan_db_path(plan_id)) as conn:
            node_row = conn.execute(
                "SELECT id, parent_id, depth, path FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not node_row:
                raise ValueError(f"Task {task_id} not found in plan {plan_id}")
            if node_row["parent_id"] == new_parent_id and new_position is None:
                return self.get_node(plan_id, task_id)

            origin_parent = node_row["parent_id"]
            origin_path = node_row["path"] or f"/{task_id}"
            origin_depth = node_row["depth"] or 0

            parent_info = self._fetch_parent_info(conn, new_parent_id)
            if new_position is None:
                new_position = self._next_position(conn, new_parent_id)
            new_depth = (parent_info["depth"] + 1) if parent_info else 0
            new_path_prefix = _build_path(
                parent_info["path"] if parent_info else "", task_id
            )

            conn.execute(
                """
                UPDATE tasks
                SET parent_id=?, position=?, depth=?, path=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (new_parent_id, new_position, new_depth, new_path_prefix, task_id),
            )

            depth_delta = new_depth - origin_depth
            conn.execute(
                """
                UPDATE tasks
                SET depth = depth + ?, path = REPLACE(path, ?, ?), updated_at=CURRENT_TIMESTAMP
                WHERE path LIKE ? AND id <> ?
                """,
                (
                    depth_delta,
                    f"{origin_path}/",
                    f"{new_path_prefix}/",
                    f"{origin_path}/%",
                    task_id,
                ),
            )

            self._resequence_children(conn, origin_parent)
            self._resequence_children(conn, new_parent_id)

        self._touch_plan(plan_id)
        return self.get_node(plan_id, task_id)

    def upsert_plan_tree(self, tree: PlanTree, *, note: Optional[str] = None) -> None:
        ordered_nodes = tree.ordered_nodes()
        snapshot_json = (
            json.dumps(tree.model_dump(), ensure_ascii=False)
            if note is not None
            else None
        )
        metadata_json = _dump_json(tree.metadata)

        with get_db() as conn:
            updated = conn.execute(
                """
                UPDATE plans
                SET title=?, description=?, metadata=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (tree.title, tree.description, metadata_json, tree.id),
            ).rowcount
            if updated == 0:
                conn.execute(
                    """
                    INSERT INTO plans (id, title, description, metadata, plan_db_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        tree.id,
                        tree.title,
                        tree.description,
                        metadata_json,
                        f"plan_{tree.id}.sqlite",
                    ),
                )

        update_plan_metadata(
            tree.id,
            title=tree.title,
            description=tree.description,
            metadata=tree.metadata,
        )

        plan_path = get_plan_db_path(tree.id)
        with plan_db_connection(plan_path) as conn:
            self._ensure_task_columns(conn, tree.id)
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM task_dependencies")
            dependency_buffer: List[Tuple[int, int]] = []
            existing_ids = set(tree.nodes.keys())

            for node in ordered_nodes:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id,
                        name,
                        status,
                        instruction,
                        parent_id,
                        position,
                        depth,
                        path,
                        metadata,
                        execution_result,
                        context_combined,
                        context_sections,
                        context_meta,
                        context_updated_at,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        node.id,
                        node.name,
                        node.status,
                        node.instruction,
                        node.parent_id,
                        node.position,
                        node.depth,
                        node.path,
                        _dump_json(node.metadata),
                        node.execution_result,
                        node.context_combined,
                        _dump_json_list(node.context_sections),
                        _dump_json(node.context_meta),
                        node.context_updated_at,
                    ),
                )
                if node.dependencies:
                    for dep in node.dependencies:
                        if dep in existing_ids:
                            dependency_buffer.append((node.id, dep))

            if dependency_buffer:
                # Filter dependencies to avoid ancestor cycles and general cycles
                path_map: Dict[int, str] = {n.id: (n.path or f"/{n.id}") for n in ordered_nodes}

                def is_ancestor(a: int, b: int) -> bool:
                    # a is ancestor of b?
                    ap = (path_map.get(a, "").rstrip("/"))
                    bp = (path_map.get(b, "").rstrip("/"))
                    return bool(ap) and bp.startswith(ap + "/")

                adj: Dict[int, List[int]] = defaultdict(list)
                accepted: List[Tuple[int, int]] = []
                seen_pairs: Set[Tuple[int, int]] = set()

                def has_path(start: int, target: int) -> bool:
                    if start == target:
                        return True
                    seen: Set[int] = set()
                    stack: List[int] = [start]
                    while stack:
                        cur = stack.pop()
                        if cur == target:
                            return True
                        if cur in seen:
                            continue
                        seen.add(cur)
                        for nxt in adj.get(cur, []):
                            if nxt not in seen:
                                stack.append(nxt)
                    return False

                dropped_self = dropped_ancestor = dropped_cycle = dropped_missing = 0
                for task_id, dep_id in dependency_buffer:
                    if (task_id, dep_id) in seen_pairs:
                        continue
                    if task_id == dep_id:
                        dropped_self += 1
                        continue
                    if dep_id not in existing_ids or task_id not in existing_ids:
                        dropped_missing += 1
                        continue
                    # Forbid depending on ancestors to avoid parent-child precedence cycle
                    if is_ancestor(dep_id, task_id):
                        dropped_ancestor += 1
                        continue
                    # Forbid introducing cycles via dependency graph
                    if has_path(dep_id, task_id):
                        dropped_cycle += 1
                        continue
                    accepted.append((task_id, dep_id))
                    adj[task_id].append(dep_id)
                    seen_pairs.add((task_id, dep_id))

                if accepted:
                    conn.executemany(
                        "INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
                        accepted,
                    )
                dropped_total = dropped_self + dropped_ancestor + dropped_cycle + dropped_missing
                if dropped_total:
                    logger.warning(
                        "Upsert dependency filtering for plan %s: accepted=%s dropped(self=%s, ancestor=%s, cycle=%s, missing=%s)",
                        tree.id,
                        len(accepted),
                        dropped_self,
                        dropped_ancestor,
                        dropped_cycle,
                        dropped_missing,
                    )

            if snapshot_json is not None:
                conn.execute(
                    "INSERT INTO snapshots (snapshot, note) VALUES (?, ?)",
                    (snapshot_json, note),
                )

        self._touch_plan(tree.id)

    def get_node(self, plan_id: int, task_id: int) -> PlanNode:
        with plan_db_connection(get_plan_db_path(plan_id)) as conn:
            self._ensure_task_columns(conn, plan_id)
            row = conn.execute(
                """
                SELECT
                    id,
                    name,
                    status,
                    instruction,
                    parent_id,
                    position,
                    depth,
                    path,
                    metadata,
                    execution_result,
                    context_combined,
                    context_sections,
                    context_meta,
                    context_updated_at
                FROM tasks
                WHERE id=?
                """,
                (task_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Task {task_id} not found in plan {plan_id}")
            dependencies = self._load_dependencies_for_task(conn, task_id)
            return _row_to_plan_node(plan_id, row, dependencies)

    def subgraph(
        self, plan_id: int, node_id: int, max_depth: int = 2
    ) -> List[PlanNode]:
        plan_path = get_plan_db_path(plan_id)
        with plan_db_connection(plan_path) as conn:
            node = conn.execute(
                "SELECT id, path, depth FROM tasks WHERE id=?",
                (node_id,),
            ).fetchone()
            if not node:
                raise ValueError(f"Task {node_id} not found in plan {plan_id}")
            path = node["path"] or f"/{node_id}"
            base_depth = node["depth"] or 0
            self._ensure_task_columns(conn, plan_id)
            rows = conn.execute(
                """
                SELECT
                    id,
                    name,
                    status,
                    instruction,
                    parent_id,
                    position,
                    depth,
                    path,
                    metadata,
                    execution_result,
                    context_combined,
                    context_sections,
                    context_meta,
                    context_updated_at
                FROM tasks
                WHERE path LIKE ?
                AND depth <= ?
                ORDER BY depth ASC, position ASC, id ASC
                """,
                (f"{path}%", base_depth + max_depth),
            ).fetchall()
            dependency_map = self._load_dependencies_map(conn)
            return [
                _row_to_plan_node(plan_id, row, dependency_map.get(row["id"], []))
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_plan_record(self, plan_id: int) -> Dict[str, Any]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, title, description, metadata, updated_at FROM plans WHERE id=?",
                (plan_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"Plan {plan_id} not found")
        return dict(row)

    def _load_tasks_and_dependencies(
        self, plan_id: int
    ) -> Tuple[List[Any], Dict[int, List[int]]]:
        plan_path = get_plan_db_path(plan_id)
        if not plan_path.exists():
            raise ValueError(f"Plan storage not found for plan {plan_id}")
        with plan_db_connection(plan_path) as conn:
            self._ensure_task_columns(conn, plan_id)
            task_rows = conn.execute(
                """
                SELECT
                    id,
                    name,
                    status,
                    instruction,
                    parent_id,
                    position,
                    depth,
                    path,
                    metadata,
                    execution_result,
                    context_combined,
                    context_sections,
                    context_meta,
                    context_updated_at
                FROM tasks
                ORDER BY depth ASC, position ASC, id ASC
                """
            ).fetchall()
            dependency_map = self._load_dependencies_map(conn)
        return task_rows, dependency_map

    def _load_dependencies_map(self, conn) -> Dict[int, List[int]]:
        mapping: Dict[int, List[int]] = defaultdict(list)
        rows = conn.execute(
            "SELECT task_id, depends_on FROM task_dependencies"
        ).fetchall()
        for row in rows:
            mapping[row["task_id"]].append(int(row["depends_on"]))
        return mapping

    def _load_dependencies_for_task(self, conn, task_id: int) -> List[int]:
        rows = conn.execute(
            "SELECT depends_on FROM task_dependencies WHERE task_id=?",
            (task_id,),
        ).fetchall()
        return [int(row["depends_on"]) for row in rows]

    def _ensure_task_columns(self, conn, plan_id: int) -> None:
        self._ensure_execution_result_column(conn, plan_id)
        self._ensure_status_column(conn, plan_id)

    def _ensure_execution_result_column(self, conn, plan_id: int) -> None:
        if plan_id in self._execution_column_checked:
            return
        info_rows = conn.execute("PRAGMA table_info(tasks)").fetchall()
        column_names = {row["name"] for row in info_rows}
        if "execution_result" not in column_names:
            conn.execute("ALTER TABLE tasks ADD COLUMN execution_result TEXT")
        self._execution_column_checked.add(plan_id)

    def _ensure_status_column(self, conn, plan_id: int) -> None:
        if plan_id in self._status_column_checked:
            return
        info_rows = conn.execute("PRAGMA table_info(tasks)").fetchall()
        column_names = {row["name"] for row in info_rows}
        if "status" not in column_names:
            conn.execute("ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'pending'")
        self._status_column_checked.add(plan_id)

    def _fetch_parent_info(
        self, conn, parent_id: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        if parent_id is None:
            return None
        row = conn.execute(
            "SELECT id, depth, path FROM tasks WHERE id=?",
            (parent_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Parent task {parent_id} not found")
        return {
            "id": row["id"],
            "depth": row["depth"] or 0,
            "path": row["path"] or f"/{parent_id}",
        }

    def _fetch_task_brief(self, conn, task_id: int) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            "SELECT id, parent_id, position FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "parent_id": row["parent_id"],
            "position": row["position"] if row["position"] is not None else 0,
        }

    def _shift_positions(self, conn, parent_id: Optional[int], start_position: int) -> None:
        if start_position < 0:
            start_position = 0
        if parent_id is None:
            conn.execute(
                "UPDATE tasks "
                "SET position = position + 1, updated_at=CURRENT_TIMESTAMP "
                "WHERE parent_id IS NULL AND position IS NOT NULL AND position >= ?",
                (start_position,),
            )
        else:
            conn.execute(
                "UPDATE tasks "
                "SET position = position + 1, updated_at=CURRENT_TIMESTAMP "
                "WHERE parent_id = ? AND position IS NOT NULL AND position >= ?",
                (parent_id, start_position),
            )

    def _prepare_insert_position(
        self,
        *,
        conn,
        parent_id: Optional[int],
        requested_position: Optional[int],
        anchor_task_id: Optional[int],
        anchor_position: Optional[str],
    ) -> Optional[int]:
        if requested_position is not None:
            if requested_position < 0:
                raise ValueError("position 不能为负数。")
            return requested_position

        if anchor_position is None:
            return None

        anchor_mode = anchor_position.strip().lower()
        if anchor_mode not in {"before", "after", "first_child", "last_child"}:
            raise ValueError(f"不支持的 anchor_position: {anchor_position!r}")

        if anchor_mode == "last_child":
            return None

        if anchor_mode == "first_child":
            self._shift_positions(conn, parent_id, 0)
            return 0

        if anchor_task_id is None:
            raise ValueError("anchor_task_id 缺失，无法根据 anchor_position 计算插入位置。")

        anchor = self._fetch_task_brief(conn, anchor_task_id)
        if anchor is None:
            raise ValueError(f"未找到锚点任务 {anchor_task_id}。")

        anchor_parent = anchor["parent_id"]
        normalized_parent = parent_id if parent_id is not None else None
        if (anchor_parent if anchor_parent is not None else None) != normalized_parent:
            raise ValueError("锚点任务不属于目标父节点，无法插入。")

        anchor_pos = anchor["position"]
        if anchor_mode == "before":
            self._shift_positions(conn, parent_id, anchor_pos)
            return anchor_pos

        # anchor_mode == "after"
        insert_pos = anchor_pos + 1
        self._shift_positions(conn, parent_id, insert_pos)
        return insert_pos

    def _next_position(self, conn, parent_id: Optional[int]) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS max_pos FROM tasks WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()
        max_pos = row["max_pos"] if row else -1
        return (max_pos or -1) + 1

    def _resequence_children(self, conn, parent_id: Optional[int]) -> None:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE parent_id IS ? ORDER BY position ASC, id ASC",
            (parent_id,),
        ).fetchall()
        for index, row in enumerate(rows):
            conn.execute(
                "UPDATE tasks SET position=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (index, row["id"]),
            )

    def _replace_dependencies(
        self, conn, task_id: int, dependencies: Optional[List[int]]
    ) -> None:
        conn.execute("DELETE FROM task_dependencies WHERE task_id=?", (task_id,))
        if not dependencies:
            return
        # Load current task/dep paths to detect ancestor relations and prevent cycles
        task_exists = conn.execute(
            "SELECT 1 FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not task_exists:
            return

        def has_dep_path(start: int, target: int) -> bool:
            # BFS over dependency edges: does start ->* target exist?
            seen: Set[int] = set()
            queue: List[int] = [start]
            while queue:
                cur = queue.pop(0)
                if cur == target:
                    return True
                if cur in seen:
                    continue
                seen.add(cur)
                rows = conn.execute(
                    "SELECT depends_on FROM task_dependencies WHERE task_id=?",
                    (cur,),
                ).fetchall()
                for r in rows:
                    nxt = int(r["depends_on"])
                    if nxt not in seen:
                        queue.append(nxt)
            return False

        valid: List[int] = []
        seen_dep: Set[int] = set()
        dropped_self = dropped_cycle = dropped_missing = dropped_invalid = 0
        for dep in dependencies:
            try:
                dep = int(dep)
            except (TypeError, ValueError):
                dropped_invalid += 1
                continue
            if dep == task_id or dep in seen_dep:
                if dep == task_id:
                    dropped_self += 1
                continue
            dep_row = conn.execute("SELECT id, path FROM tasks WHERE id=?", (dep,)).fetchone()
            if not dep_row:
                dropped_missing += 1
                continue
            # Forbid introducing cycles via existing dependency graph (dep ->* task)
            if has_dep_path(dep, task_id):
                dropped_cycle += 1
                continue
            valid.append(dep)
            seen_dep.add(dep)

        if not valid:
            return
        conn.executemany(
            "INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
            [(task_id, d) for d in valid],
        )
        if dropped_self or dropped_cycle or dropped_missing or dropped_invalid:
            logger.warning(
                "Dependency filtering for task %s: kept=%s dropped(self=%s, cycle=%s, missing=%s, invalid=%s)",
                task_id,
                len(valid),
                dropped_self,
                dropped_cycle,
                dropped_missing,
                dropped_invalid,
            )

    def _touch_plan(self, plan_id: int) -> None:
        with get_db() as conn:
            conn.execute(
                "UPDATE plans SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (plan_id,),
            )

    def _count_tasks(self, plan_id: int) -> int:
        plan_path = get_plan_db_path(plan_id)
        if not plan_path.exists():
            return 0
        with plan_db_connection(plan_path) as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM tasks").fetchone()
            return int(row["cnt"]) if row else 0


def _rows_to_plan_tree(
    plan_id: int,
    plan_row: Dict[str, Any],
    task_rows,
    dependency_map: Dict[int, List[int]],
) -> PlanTree:
    nodes: Dict[int, PlanNode] = {}
    adjacency: Dict[Optional[int], List[int]] = defaultdict(list)
    for row in task_rows:
        dependencies = dependency_map.get(row["id"], [])
        node = _row_to_plan_node(plan_id, row, dependencies)
        nodes[node.id] = node
        adjacency[node.parent_id].append(node.id)

    for key in adjacency:
        adjacency[key].sort(key=lambda node_id: nodes[node_id].position)

    return PlanTree(
        id=plan_id,
        title=plan_row["title"],
        description=plan_row["description"],
        metadata=_loads_json(plan_row["metadata"]),
        nodes=nodes,
        adjacency=dict(adjacency),
    )


def _row_to_plan_node(plan_id: int, row, dependencies: List[int]) -> PlanNode:
    metadata = _loads_json(row["metadata"])
    context_sections = _loads_json_list(row["context_sections"])
    context_meta = _loads_json(row["context_meta"])
    return PlanNode(
        id=row["id"],
        plan_id=plan_id,
        name=row["name"],
        status=row["status"] or "pending",
        instruction=row["instruction"],
        parent_id=row["parent_id"],
        position=row["position"] or 0,
        depth=row["depth"] or 0,
        path=row["path"] or f"/{row['id']}",
        metadata=metadata,
        dependencies=[int(dep) for dep in dependencies],
        context_combined=row["context_combined"],
        context_sections=context_sections,
        context_meta=context_meta,
        context_updated_at=row["context_updated_at"],
        execution_result=row["execution_result"],
    )


def _sanitize_dependencies(dependencies: Optional[List[int]]) -> Optional[List[int]]:
    if not dependencies:
        return None
    unique: List[int] = []
    seen = set()
    for dep in dependencies:
        try:
            value = int(dep)
        except (TypeError, ValueError):
            continue
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return unique or None


def _loads_json(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _loads_json_list(raw: Optional[str]) -> List[Any]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _dump_json(data: Optional[Dict[str, Any]]) -> str:
    try:
        return json.dumps(data or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def _dump_json_list(data: Optional[List[Any]]) -> str:
    try:
        return json.dumps(data or [], ensure_ascii=False)
    except Exception:
        return "[]"


def _merge_metadata(
    metadata: Optional[Dict[str, Any]],
    dependencies: Optional[List[int]],
) -> Dict[str, Any]:
    base = dict(metadata or {})
    if dependencies is not None:
        sanitized = _sanitize_dependencies(dependencies) or []
        base["dependencies"] = sanitized
    return base


def _build_path(parent_path: str, node_id: int) -> str:
    parent_path = (parent_path or "").rstrip("/")
    if not parent_path:
        return f"/{node_id}"
    return f"{parent_path}/{node_id}"
