"""Resolve explicit seed hierarchy without inferring a different task definition."""

from copy import deepcopy


def prepare(tasks):
    rows = []
    for index, raw in enumerate(tasks or [], 1):
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        row["name"] = str(row.get("name") or row.get("title") or f"Task {index}").strip()
        row["key"] = str(row.get("key") or row["name"]).strip()
        metadata = row.get("metadata") or {}
        row["parent_key"] = row.get("parent_key") or metadata.get("parent_stage")
        kind = row.get("task_type") or metadata.get("task_type") or metadata.get("kind")
        row["task_type"] = kind if kind in {"atomic", "composite"} else "composite"
        rows.append(row)
    by_key = {row["key"]: row for row in rows}
    if len(by_key) != len(rows):
        raise ValueError("Task seed keys must be unique; give repeated task names distinct keys")
    by_name = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row["key"])

    def resolve(value):
        value = str(value).strip()
        if value in by_key:
            return value
        candidates = by_name.get(value, [])
        if len(candidates) != 1:
            raise ValueError(f"Unknown or ambiguous seed reference: {value}")
        return candidates[0]

    for row in rows:
        if row["parent_key"]:
            row["parent_key"] = resolve(row["parent_key"])
        resolved = []
        for dep in row.get("dependencies") or []:
            # Numeric IDs remain a legacy input; key/name references are the
            # normal create API, before actual SQLite node IDs exist.
            if isinstance(dep, int) or (str(dep).isdigit() and str(dep) not in by_key):
                resolved.append(int(dep))
            else:
                key = resolve(dep)
                if key == row["key"]:
                    raise ValueError("A task cannot depend on itself")
                resolved.append(key)
        row["dependencies"] = resolved
    ordered, visiting, visited = [], set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("Task seed hierarchy contains a cycle")
        if key in visited:
            return
        visiting.add(key)
        parent = by_key[key]["parent_key"]
        if parent:
            if by_key[parent]["task_type"] == "atomic":
                raise ValueError("An explicitly atomic task cannot contain child tasks")
            visit(parent)
        visiting.remove(key)
        visited.add(key)
        ordered.append(by_key[key])

    for row in rows:
        visit(row["key"])
    return ordered


def complete(tree, nodes):
    leaves = [node for node in nodes if not tree.children_ids(node.id)]
    return bool(leaves) and all(node.metadata.get("task_type") == "atomic" for node in leaves)
