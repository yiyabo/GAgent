from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from ..interfaces import TaskRepository
from ..repository.tasks import default_repo
from ..utils import split_prefix
from app.services.context.context_budget import PRIORITY_ORDER
from app.services.foundation.settings import get_settings

# -------------------------------
# Helpers
# -------------------------------


def _resolve_index_path() -> str:
    try:
        return getattr(get_settings(), "global_index_path", "INDEX.md")
    except Exception:
        return os.environ.get("GLOBAL_INDEX_PATH", "INDEX.md")


def _safe_mkdirs(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _format_ts(ts: Optional[str]) -> str:
    # SQLite CURRENT_TIMESTAMP is already 'YYYY-MM-DD HH:MM:SS'
    if not ts:
        return "—"
    try:
        # Try to normalize and include minutes only
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _stage_for_counts(done: int, total: int) -> str:
    if total <= 0:
        return "Planning"
    if done >= total:
        return "Complete"
    if done > 0:
        return "Executing"
    return "Planning"


def _plan_tasks(repo: TaskRepository, title: str) -> List[Dict[str, Any]]:
    rows = repo.list_plan_tasks(title) or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        tid = r.get("id") if isinstance(r, dict) else None
        name = r.get("name") if isinstance(r, dict) else None
        status = r.get("status") if isinstance(r, dict) else None
        prio = r.get("priority") if isinstance(r, dict) else None
        if tid is None or name is None:
            continue
        _, short = split_prefix(name)
        out.append(
            {
                "id": int(tid),
                "name": name,
                "short": short,
                "status": status or "pending",
                "priority": prio,
            }
        )
    return out


def _plan_last_updated(repo: TaskRepository, task_ids: List[int]) -> str:
    latest: Optional[str] = None
    for tid in task_ids:
        try:
            snaps = repo.list_task_contexts(tid)
        except Exception:
            snaps = []
        if not snaps:
            continue
        for s in snaps:
            ts = s.get("created_at")
            if not ts:
                continue
            if latest is None or str(ts) > latest:
                latest = str(ts)
    return _format_ts(latest)


def _requires_links_for_plan(repo: TaskRepository, plan_task_ids: Set[int]) -> List[Tuple[int, int]]:
    try:
        links = repo.list_links(kind="requires")
    except Exception:
        links = []
    edges: List[Tuple[int, int]] = []
    for l in links:
        try:
            u = int(l.get("from_id"))
            v = int(l.get("to_id"))
        except Exception:
            continue
        if u in plan_task_ids and v in plan_task_ids:
            edges.append((u, v))
    return edges


def _find_cycle(adj: Dict[int, List[int]]) -> Optional[List[int]]:
    visited: Set[int] = set()
    stack: Set[int] = set()
    parent: Dict[int, int] = {}

    def dfs(u: int) -> Optional[List[int]]:
        visited.add(u)
        stack.add(u)
        for v in adj.get(u, []):
            if v not in visited:
                parent[v] = u
                cyc = dfs(v)
                if cyc:
                    return cyc
            elif v in stack:
                # reconstruct cycle v -> ... -> u -> v
                path: List[int] = [u]
                x = u
                while x != v and x in parent:
                    x = parent[x]
                    path.append(x)
                path.reverse()
                path.append(v)
                return path
        stack.remove(u)
        return None

    for node in list(adj.keys()):
        if node not in visited:
            cyc = dfs(node)
            if cyc:
                return cyc
    return None


def _bottlenecks(adj: Dict[int, List[int]], nodes: Set[int], topk: int = 3) -> List[int]:
    indeg: Dict[int, int] = {n: 0 for n in nodes}
    outdeg: Dict[int, int] = {n: 0 for n in nodes}
    for u, vs in adj.items():
        outdeg[u] = outdeg.get(u, 0) + len(vs)
        for v in vs:
            indeg[v] = indeg.get(v, 0) + 1
    scored = [(n, indeg.get(n, 0) * outdeg.get(n, 0)) for n in nodes]
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [n for n, s in scored if s > 0][:topk]


def generate_index(
    repo: TaskRepository = default_repo,
    brief_tasks_n: int = 3,
    history_n: int = 10,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate the full INDEX.md content and metadata.

    Returns:
        {"content": str, "meta": {...}, "path": resolved_path}
    """
    resolved_path = path or _resolve_index_path()

    # Collect plan data
    try:
        plan_titles = repo.list_plan_titles()
    except Exception:
        plan_titles = []

    # Stable sort by title
    plan_titles = sorted(plan_titles)

    plans: List[Dict[str, Any]] = []
    total_tasks = 0
    total_done = 0

    for title in plan_titles:
        tasks = _plan_tasks(repo, title)
        total = len(tasks)
        done = sum(1 for t in tasks if str(t.get("status")) == "done")
        total_tasks += total
        total_done += done
        stage = _stage_for_counts(done, total)
        # brief
        brief_items = [t.get("short") for t in tasks[: max(1, int(brief_tasks_n))]]
        brief = "; ".join([bi for bi in brief_items if isinstance(bi, str) and bi])
        # last updated by snapshot timestamps
        last_updated = _plan_last_updated(repo, [t["id"] for t in tasks])
        # dependency summary (requires-only) inside this plan
        id_set: Set[int] = {t["id"] for t in tasks}
        edges = _requires_links_for_plan(repo, id_set)
        adj: Dict[int, List[int]] = {}
        for u, v in edges:
            adj.setdefault(u, []).append(v)
        cyc = _find_cycle(adj)
        bottlenecks = _bottlenecks(adj, id_set) if edges else []

        plans.append(
            {
                "title": title,
                "owner": "—",  # placeholder (can be extended via env or metadata later)
                "stage": stage,
                "done": done,
                "total": total,
                "last_updated": last_updated,
                "tasks": tasks,
                "brief": brief,
                "edges": edges,
                "has_cycle": bool(cyc),
                "sample_cycle": cyc or [],
                "bottlenecks": bottlenecks,
            }
        )

    # Read history lines (existing)
    history_path = f"{resolved_path}.history.jsonl"
    history: List[Dict[str, Any]] = []
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # take last N (newest at end)
        for line in lines[-int(history_n) :][::-1]:  # reverse to show newest first
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    history.append(obj)
            except Exception:
                continue
    except Exception:
        history = []

    # Build markdown
    lines: List[str] = []
    lines.append("# Project Index")
    lines.append("")

    # TOC
    lines.append("## Table of Contents")
    lines.append("- [Plans Overview](#plans-overview)")
    lines.append("- [Context Budget](#context-budget)")
    lines.append("- [Dependency Summary](#dependency-summary)")
    lines.append("- [Plans](#plans)")
    lines.append("- [Changelog](#changelog)")
    lines.append("")

    # Plans Overview table
    lines.append("## Plans Overview")
    lines.append("| Plan | Owner | Stage | Done/Total | Last Updated |")
    lines.append("| --- | --- | --- | ---:| --- |")
    for p in plans:
        lines.append(f"| {p['title']} | {p['owner']} | {p['stage']} | {p['done']}/{p['total']} | {p['last_updated']} |")
    if not plans:
        lines.append("(no plans)")
    lines.append("")

    # Context Budget sidebar
    lines.append("## Context Budget")
    prio = " \u2192 ".join(list(PRIORITY_ORDER))
    lines.append(f"- Priority order: {prio}")
    lines.append("- Note: 'index' is always budgeted first.")
    lines.append("")

    # Dependency Summary (per plan)
    lines.append("## Dependency Summary")
    if not plans:
        lines.append("(no data)")
    for p in plans:
        lines.append(f"### {p['title']}")
        edges_n = len(p.get("edges") or [])
        lines.append(f"- requires edges: {edges_n}")
        has_cycle = p.get("has_cycle")
        if has_cycle:
            cyc = p.get("sample_cycle") or []
            cyc_str = " -> ".join([f"#{n}" for n in cyc]) if cyc else "yes"
            lines.append(f"- cycles: yes ({cyc_str})")
        else:
            lines.append("- cycles: no")
        bots = p.get("bottlenecks") or []
        if bots:
            # map to human-readable names
            id_to_short = {t["id"]: t.get("short") or t.get("name") for t in p.get("tasks") or []}
            bot_names = [id_to_short.get(b, f"#{b}") for b in bots]
            lines.append(f"- bottlenecks: {', '.join(bot_names)}")
        else:
            lines.append("- bottlenecks: —")
        lines.append("")

    # Plans details
    lines.append("## Plans")
    if not plans:
        lines.append("(no plans)")
    for p in plans:
        lines.append(f"### [Plan] {p['title']}")
        lines.append(f"- Brief: {p['brief'] or '—'}")
        lines.append(f"- Stats: {p['done']}/{p['total']} done")
        lines.append("- Tasks")
        id_to_short = {t["id"]: (t.get("short") or t.get("name") or str(t["id"])) for t in p.get("tasks") or []}
        for t in p.get("tasks") or []:
            tid = t["id"]
            prio_val = t.get("priority")
            prio_str = str(int(prio_val)) if isinstance(prio_val, int) else "—"
            status = str(t.get("status") or "pending")
            nm = id_to_short.get(tid, str(tid))
            lines.append(f"  - [#{tid} p={prio_str} status={status}] {nm}")
        lines.append("")

    # Changelog
    lines.append("## Changelog")
    if history:
        for h in history:
            ts = _format_ts(h.get("ts"))
            lines.append(
                f"- {ts} — plans: {h.get('plans')}\uFF0C tasks: {h.get('tasks_total')}\uFF0C done: {h.get('done_total')}/{h.get('tasks_total')}"
            )
    else:
        lines.append("(no history)")
    lines.append("")

    content = "\n".join(lines).rstrip() + "\n"

    meta = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "plans": len(plans),
        "tasks_total": int(total_tasks),
        "done_total": int(total_done),
        "path": resolved_path,
        "history_path": history_path,
    }

    return {"content": content, "meta": meta, "path": resolved_path}


def write_index(content: str, path: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> str:
    """Write content to INDEX.md (resolved) and append a history record.

    Returns the path written to.
    """
    resolved_path = path or _resolve_index_path()
    parent = os.path.dirname(resolved_path)
    if parent:
        _safe_mkdirs(parent)

    # Write the index file
    with open(resolved_path, "w", encoding="utf-8") as f:
        f.write(content or "")

    # Append history JSONL
    history_path = f"{resolved_path}.history.jsonl"
    rec = {
        "ts": (meta or {}).get("generated_at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "plans": (meta or {}).get("plans"),
        "tasks_total": (meta or {}).get("tasks_total"),
        "done_total": (meta or {}).get("done_total"),
    }
    try:
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return resolved_path
