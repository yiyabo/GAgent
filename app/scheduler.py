import heapq
from typing import Any, Dict, List, Optional, Tuple

from .repository.tasks import default_repo

# === PLAN-ID BASED SCHEDULING FUNCTIONS (NO TITLE DEPENDENCY) ===


# Required helper functions
def _ensure_hierarchy(rows: List[Dict[str, Any]]) -> None:
    """Ensure each row has parent_id, path, depth."""
    for r in rows:
        tid = int(r.get("id", 0))
        info = default_repo.get_task_info(tid)
        if info:
            r.setdefault("parent_id", info.get("parent_id"))
            r.setdefault("path", info.get("path", f"/{tid}"))
            r.setdefault("depth", info.get("depth", 0))
        else:
            r.setdefault("path", f"/{tid}")
            r.setdefault("depth", 0)


def _root_id_from_path(path: Optional[str], self_id: Optional[int]) -> int:
    """Extract root id from a path like '/12/45/79'. Defaults to self id."""
    if isinstance(path, str) and path.startswith("/"):
        parts = [p for p in path.split("/") if p]
        if parts:
            try:
                return int(parts[0])
            except Exception:
                pass
    try:
        return int(self_id) if self_id is not None else 0
    except Exception:
        return 0


def bfs_schedule(plan_id: int, pending_only: bool = True) -> List[Dict[str, Any]]:
    """Yield tasks for a specific plan ID in breadth-first order."""
    if not plan_id:
        return []

    from .repository.tasks import default_repo

    all_plan_tasks = default_repo.get_plan_tasks(plan_id)
    if pending_only:
        tasks = [t for t in all_plan_tasks if t.get("status") == "pending"]
    else:
        tasks = all_plan_tasks

    if not tasks:
        return []

    _ensure_hierarchy(tasks)

    root_priorities = {}
    for t in tasks:
        if int(t.get("depth") or 0) == 0:
            root_priorities[int(t.get("id"))] = int(t.get("priority") or 100)

    def _enhanced_bfs_key(task: Dict[str, Any]) -> Tuple[int, int, int, str, int]:
        pr = int(task.get("priority") or 100)
        tid = int(task.get("id"))
        path = task.get("path") or f"/{tid}"
        depth = int(task.get("depth") or 0)
        root_id = _root_id_from_path(path, tid)
        root_priority = root_priorities.get(root_id, root_id)
        return (root_priority, depth, pr, path, tid)

    return sorted(tasks, key=_enhanced_bfs_key)


def postorder_schedule(plan_id: int, pending_only: bool = True) -> List[Dict[str, Any]]:
    """Yield tasks for a specific plan ID in post-order traversal."""
    if not plan_id:
        return []

    from .repository.tasks import default_repo

    all_plan_tasks = default_repo.get_plan_tasks(plan_id)
    if pending_only:
        tasks = [t for t in all_plan_tasks if t.get("status") == "pending"]
    else:
        tasks = all_plan_tasks

    if not tasks:
        return []

    _ensure_hierarchy(tasks)

    children_map = {}
    for t in tasks:
        try:
            parent_id = t.get("parent_id")
            if parent_id is not None:
                parent_id = int(parent_id)
                children_map.setdefault(parent_id, []).append(t)
        except Exception:
            continue

    visited = set()
    result = []

    def _postorder_dfs(task):
        task_id = int(task.get("id"))
        if task_id in visited:
            return
        visited.add(task_id)
        children = children_map.get(task_id, [])
        children_sorted = sorted(
            children, key=lambda x: (int(x.get("priority") or 100), int(x.get("id")))
        )
        for child in children_sorted:
            _postorder_dfs(child)
        result.append(task)

    root_tasks = []
    task_ids = {int(t["id"]) for t in tasks}

    for t in tasks:
        parent_id = t.get("parent_id")
        if parent_id is None or int(parent_id) not in task_ids:
            root_tasks.append(t)

    root_tasks_sorted = sorted(
        root_tasks, key=lambda x: (int(x.get("priority") or 100), int(x.get("id")))
    )

    for root in root_tasks_sorted:
        _postorder_dfs(root)

    return result


def requires_dag_order(
    plan_id: int, pending_only: bool = True
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Build DAG for plan tasks using 'requires' links and check for cycles."""
    if not plan_id:
        return [], None

    from .repository.tasks import default_repo

    all_plan_tasks = default_repo.get_plan_tasks(plan_id)
    if pending_only:
        nodes = [t for t in all_plan_tasks if t.get("status") == "pending"]
    else:
        nodes = all_plan_tasks

    if not nodes:
        return [], None

    id_to_row = {r["id"]: r for r in nodes}
    scoped_ids = set(id_to_row.keys())

    links = default_repo.list_links(kind="requires")
    edges = [
        (int(link["from_id"]), int(link["to_id"]))
        for link in links
        if int(link["from_id"]) in scoped_ids
        and int(link["to_id"]) in scoped_ids
    ]

    indeg = {nid: 0 for nid in scoped_ids}
    adj = {nid: [] for nid in scoped_ids}
    for f, t in edges:
        indeg[t] += 1
        adj[f].append(t)

    heap = []
    for nid in scoped_ids:
        if indeg[nid] == 0:
            task = id_to_row[nid]
            key = (
                int(task.get("priority") or 100),
                int(task.get("depth") or 0),
                task.get("path", f"/{nid}"),
                nid,
            )
            heapq.heappush(heap, key)

    ordered = []

    while heap:
        *_, nid = heapq.heappop(heap)
        ordered.append(id_to_row[nid])
        for m in adj.get(nid, []):
            indeg[m] -= 1
            if indeg[m] == 0:
                task = id_to_row[m]
                key = (
                    int(task.get("priority") or 100),
                    int(task.get("depth") or 0),
                    task.get("path", f"/{m}"),
                    m,
                )
                heapq.heappush(heap, key)

    if len(ordered) == len(scoped_ids):
        return ordered, None

    residual = {nid for nid in scoped_ids if nid not in {t["id"] for t in ordered}}
    cyc_edges = [
        {"from": f, "to": t} for (f, t) in edges if f in residual and t in residual
    ]

    cycle_info = {
        "nodes": sorted(list(residual)),
        "edges": cyc_edges,
        "message": f"Cycle detected among {len(residual)} tasks in plan {plan_id}",
    }
    return ordered, cycle_info