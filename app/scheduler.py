from typing import Any, Dict, List, Optional, Set, Tuple
import heapq

from .repository.tasks import default_repo
from .utils import plan_prefix, split_prefix

def bfs_schedule(title: Optional[str] = None):
    """Yield pending tasks in a hierarchy-aware, stable order using a single batch query.

    Behavior:
    - Loads all pending tasks once via list_pending_full() to include hierarchy fields.
    - If title is provided, scopes rows by plan prefix in-memory (no extra DB lookups).
    - Orders by (priority ASC, root_id ASC, depth ASC, path ASC, id ASC), approximating a
      breadth-first traversal grouped by subtree with stable tiebreakers.
    """
    # Batch load all pending tasks with hierarchy info
    rows = default_repo.list_pending_full()

    # Optional scope by plan title
    if title:
        prefix = plan_prefix(title)
        rows = [r for r in rows if (r.get("name") or "").startswith(prefix)]

    # Ensure hierarchy fields present (no-op for list_pending_full, but safe fallback)
    _ensure_hierarchy(rows)

    # Stable, hierarchy-aware ordering optimized for BFS
    # First, build a map of root priorities for proper subtree ordering
    root_priorities = {}
    for r in rows:
        if int(r.get("depth") or 0) == 0:
            root_priorities[int(r.get("id"))] = int(r.get("priority") or 100)
    
    def _enhanced_bfs_key(row: Dict[str, Any]) -> Tuple[int, int, int, str, int]:
        pr, rid = _priority_key(row)
        path = row.get("path") or f"/{rid}"
        depth = int(row.get("depth") or 0)
        root_id = _root_id_from_path(path, rid)
        
        # Use actual root priority for consistent subtree grouping
        root_priority = root_priorities.get(root_id, root_id)
        
        return (root_priority, depth, pr, path, rid)
    
    rows_sorted = sorted(rows, key=_enhanced_bfs_key)
    for r in rows_sorted:
        yield r


def _priority_key(row: Dict[str, Any]) -> Tuple[int, int]:
    """Return stable priority key (priority ASC, id ASC)."""
    pr = row.get("priority")
    pr_val = int(pr) if isinstance(pr, int) else 100
    rid = int(row.get("id"))
    return (pr_val, rid)


def _ensure_hierarchy(rows: List[Dict[str, Any]]) -> None:
    """Ensure each row has parent_id, path, depth. Mutates rows in-place.

    Fallbacks: if missing, fetch via repo.get_task_info(id). If still missing,
    synthesize defaults path=f"/{id}", depth=0.
    """
    for r in rows:
        if (r.get("path") is not None) and (r.get("depth") is not None):
            continue
        try:
            tid = int(r.get("id"))
        except Exception:
            continue
        info = default_repo.get_task_info(tid)
        if info:
            # only fill missing to avoid overwriting existing fields
            for k in ("parent_id", "path", "depth"):
                if r.get(k) is None and (info.get(k) is not None):
                    r[k] = info.get(k)
        # final fallbacks
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


def _dag_heap_key(row: Dict[str, Any]) -> Tuple[int, int, int, str, int]:
    """Stable key for DAG scheduler that is hierarchy-aware.

    Order by:
      1) priority ASC (missing -> 100)
      2) root_id ASC (group by subtree)
      3) depth ASC (parents before deeper nodes when available)
      4) path ASC (stable within subtree)
      5) id ASC (final tiebreaker)
    """
    pr, rid = _priority_key(row)
    path = row.get("path") or f"/{rid}"
    depth = int(row.get("depth") or 0)
    root = _root_id_from_path(path, rid)
    return (pr, root, depth, path, rid)


def _bfs_heap_key(row: Dict[str, Any]) -> Tuple[int, int, int, str, int]:
    """Stable key for BFS scheduler optimized for breadth-first hierarchy traversal.

    Order by:
      1) root priority ASC (priority of root task in the subtree)
      2) depth ASC (parents before children)
      3) priority ASC within same depth level
      4) path ASC (stable ordering)
      5) id ASC (final tiebreaker)
    
    This ensures proper hierarchy: root tasks ordered by their priority,
    then within each subtree, parents come before children.
    """
    pr, rid = _priority_key(row)
    path = row.get("path") or f"/{rid}"
    depth = int(row.get("depth") or 0)
    root_id = _root_id_from_path(path, rid)
    
    # For root priority, we need to find the root task's priority
    # For now, use the task's own priority if it's a root, otherwise use a default
    if depth == 0:
        root_priority = pr
    else:
        # For child tasks, we'd ideally look up the root's priority
        # but for simplicity, use the root_id as a proxy for consistent grouping
        root_priority = root_id
    
    return (root_priority, depth, pr, path, rid)


def requires_dag_order(title: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Build a dependency-aware execution order using 'requires' links.

    - Scope:
      * If title is None: consider all pending tasks globally.
      * Else: consider only pending tasks whose name starts with plan prefix for 'title'.
    - Edges:
      * Include only 'requires' edges where both endpoints are in the scoped pending set
        (external or non-pending dependencies are treated as already satisfied).
    - Ordering:
      * Kahn's algorithm with a min-heap keyed by (priority ASC, id ASC) for stability.
    - Cycle detection:
      * If residual nodes remain, return cycle info with node ids and intra-scope edges.

    Returns: (order_rows, cycle_info)
    - order_rows: list of task rows (dicts) in stable topological order
    - cycle_info: optional dict with {nodes: [...], edges: [{from, to}, ...], names: {id: name}, message}
    """
    # 1) Nodes (scoped pending)
    if title is None:
        nodes = default_repo.list_tasks_by_status('pending')
    else:
        prefix = plan_prefix(title)
        nodes = default_repo.list_tasks_by_prefix(prefix, pending_only=True, ordered=False)

    id_to_row: Dict[int, Dict[str, Any]] = {}
    for r in nodes:
        try:
            rid = int(r.get("id"))
        except Exception:
            continue
        id_to_row[rid] = r
    scoped_ids: Set[int] = set(id_to_row.keys())

    # Early exit: no nodes
    if not scoped_ids:
        return [], None

    # 2) Edges within scope (requires only)
    links = default_repo.list_links(kind='requires')
    edges: List[Tuple[int, int]] = []  # (from_id -> to_id)
    for l in links:
        try:
            f = int(l.get("from_id"))
            t = int(l.get("to_id"))
        except Exception:
            continue
        if (f in scoped_ids) and (t in scoped_ids):
            edges.append((f, t))

    # 3) Build indegree and adjacency
    indeg: Dict[int, int] = {nid: 0 for nid in scoped_ids}
    adj: Dict[int, List[int]] = {nid: [] for nid in scoped_ids}
    for f, t in edges:
        indeg[t] += 1
        adj[f].append(t)

    # 4) Initialize heap with indegree==0 nodes (stable, hierarchy-aware)
    heap: List[Tuple[int, int, int, str, int]] = []  # key from _dag_heap_key + id
    # enrich rows with hierarchy fields for grouping
    _ensure_hierarchy(list(id_to_row.values()))
    for nid in scoped_ids:
        if indeg[nid] == 0:
            k = _dag_heap_key(id_to_row[nid])
            # append id to ensure tuple has unique last element for heap stability
            heap.append((*k, nid))
    heapq.heapify(heap)

    ordered_ids: List[int] = []
    visited: Set[int] = set()

    while heap:
        # pop based on hierarchy-aware key
        *_, nid = heapq.heappop(heap)
        if nid in visited:
            continue
        visited.add(nid)
        ordered_ids.append(nid)
        for m in adj.get(nid, []):
            indeg[m] -= 1
            if indeg[m] == 0:
                k = _dag_heap_key(id_to_row[m])
                heapq.heappush(heap, (*k, m))

    # 5) Collect result and cycle info
    order_rows = [id_to_row[i] for i in ordered_ids]
    if len(ordered_ids) == len(scoped_ids):
        return order_rows, None

    # Residual nodes indicate a cycle
    residual: Set[int] = {nid for nid in scoped_ids if nid not in visited}
    cyc_edges = [
        {"from": f, "to": t}
        for (f, t) in edges
        if (f in residual) and (t in residual)
    ]
    # Use short names (without [title] prefix) for readability in cycle info
    names = {}
    for rid in residual:
        full = id_to_row[rid].get("name")
        _, short = split_prefix(full or "")
        names[rid] = short
    cycle_info = {
        "nodes": sorted(list(residual)),
        "edges": cyc_edges,
        "names": names,
        "message": "Cycle detected in requires DAG within the selected scope.",
    }
    return order_rows, cycle_info


def requires_dag_schedule(title: Optional[str] = None):
    """Yield tasks in dependency-aware order; ignores cycle leftovers (reported via requires_dag_order)."""
    ordered, _ = requires_dag_order(title)
    for r in ordered:
        yield r