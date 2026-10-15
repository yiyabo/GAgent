"""Ordered, bounded Plan references for the existing chat model (never dispatch)."""


def plan_reference_index(tree, *, referenced_task_id=None, limit=100):
    children = {}
    for node in sorted(tree.nodes.values(), key=lambda n: (getattr(n, "position", 0), n.id)):
        children.setdefault(node.parent_id, []).append(node.id)
    roots = children.get(None, [])
    stages = []
    for rid in roots:
        root = tree.nodes[rid]
        if (root.metadata or {}).get("task_type") == "root" or len(roots) == 1:
            stages.extend(children.get(rid, []))
        else:
            stages.append(rid)

    rows, seen = [], set()

    def visit(tid, ancestors, sibling_number):
        if tid in seen or len(rows) >= limit:
            return
        seen.add(tid)
        node = tree.nodes[tid]
        child_ids = children.get(tid, [])
        rows.append({
            "id": tid, "parent_id": node.parent_id, "name": node.name,
            "status": node.status, "ancestor_ids": ancestors,
            "sibling_number": sibling_number, "children_ids": child_ids,
            "task_type": (node.metadata or {}).get("task_type") or
                         ("composite" if child_ids else "atomic"),
        })
        for ordinal, cid in enumerate(child_ids, 1):
            visit(cid, [*ancestors, tid], ordinal)

    for ordinal, rid in enumerate(roots, 1):
        visit(rid, [], ordinal)
    # Retain malformed/disconnected nodes as references; never silently hide them.
    for node in tree.nodes.values():
        visit(node.id, [], None)
    return {
        "plan_id": tree.id, "title": tree.title,
        "referenced_task_id": referenced_task_id,
        "reference_note": "UI selection is a reference, not permission to execute.",
        "stages": [{"number": i, "task_id": tid, "name": tree.nodes[tid].name}
                   for i, tid in enumerate(stages, 1)][:limit],
        "total_tasks": len(tree.nodes), "truncated": len(rows) < len(tree.nodes),
        "tasks": rows,
    }
