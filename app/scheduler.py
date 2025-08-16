from typing import Any, Dict, List, Optional, Set, Tuple
import heapq

from .repository.tasks import default_repo
from .utils import plan_prefix, split_prefix

def bfs_schedule():
    rows = default_repo.list_tasks_by_status('pending')
    # Ensure stable ordering consistent with previous SQL
    rows_sorted = sorted(
        rows,
        key=lambda r: ((r.get('priority') if isinstance(r, dict) else r[3]) or 100, (r.get('id') if isinstance(r, dict) else r[0]))
    )
    for t in rows_sorted:
        yield t


def _priority_key(row: Dict[str, Any]) -> Tuple[int, int]:
    """Return stable priority key (priority ASC, id ASC)."""
    pr = row.get("priority")
    pr_val = int(pr) if isinstance(pr, int) else 100
    rid = int(row.get("id"))
    return (pr_val, rid)


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

    # 4) Initialize heap with indegree==0 nodes (stable by priority, id)
    heap: List[Tuple[int, int, int]] = []  # (priority, id, id)
    for nid in scoped_ids:
        if indeg[nid] == 0:
            pr, _ = _priority_key(id_to_row[nid])
            heap.append((pr, nid, nid))
    heapq.heapify(heap)

    ordered_ids: List[int] = []
    visited: Set[int] = set()

    while heap:
        _, _, nid = heapq.heappop(heap)
        if nid in visited:
            continue
        visited.add(nid)
        ordered_ids.append(nid)
        for m in adj.get(nid, []):
            indeg[m] -= 1
            if indeg[m] == 0:
                pr, _ = _priority_key(id_to_row[m])
                heapq.heappush(heap, (pr, m, m))

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