import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from ...utils import split_prefix
from app.services.context.context_budget import PRIORITY_ORDER
from app.services.foundation.settings import get_settings
from app.services.context.retrieval import get_retrieval_service

# Debug logging (opt-in via env: CTX_DEBUG/CONTEXT_DEBUG)
_CTX_LOGGER = logging.getLogger("app.context")


def _debug_on() -> bool:
    """Use centralized settings for debug flag (CTX/CONTEXT aliases)."""
    try:
        return bool(getattr(get_settings(), "ctx_debug", False))
    except Exception:
        # Fallback to env (defensive)
        v = os.environ.get("CTX_DEBUG") or os.environ.get("CONTEXT_DEBUG")
        return str(v).strip().lower() in {"1", "true", "yes", "on"} if v else False


def _get_task_by_id(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Prefer repository direct lookup when available; fallback to list scan."""
    # Prefer efficient repo method if provided
    if hasattr(repo, "get_task_info"):
        try:
            info = repo.get_task_info(task_id)
            if info:
                return info
        except Exception:
            pass
    # Fallback: linear scan
    try:
        rows = repo.list_all_tasks()
        for r in rows:
            try:
                if r.get("id") == task_id:
                    return r
            except Exception:
                # Support tuple rows
                try:
                    if int(r[0]) == int(task_id):
                        return {
                            "id": r[0],
                            "name": r[1],
                            "status": r[2],
                            "priority": r[3],
                        }
                except Exception:
                    continue
    except Exception:
        return None
    return None


def _section_for_task(task: Dict[str, Any], repo: TaskRepository, kind: str) -> Optional[Dict[str, Any]]:
    """Construct a section from a task record using its output if available, else the input prompt.
    Returns None if neither content nor prompt could be found.
    """
    tid = task.get("id") if isinstance(task, dict) else None
    name = task.get("name") if isinstance(task, dict) else None
    if tid is None or name is None:
        return None

    content = repo.get_task_output_content(tid)
    if not content:
        content = repo.get_task_input_prompt(tid) or ""
    if content is None:
        return None

    title, short = split_prefix(name)
    return {
        "task_id": tid,
        "name": name,
        "short_name": short,
        "kind": kind,
        "content": content,
    }


# -----------------
# Global index helpers (Phase 4)
# -----------------


def _read_index_content() -> str:
    """Read global INDEX.md (or path from GLOBAL_INDEX_PATH). On failure, return empty string.

    This file is treated as a high-priority, always-included context anchor.
    """
    try:
        path = getattr(get_settings(), "global_index_path", "INDEX.md")
    except Exception:
        path = os.environ.get("GLOBAL_INDEX_PATH", "INDEX.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _index_section() -> Dict[str, Any]:
    content = _read_index_content()
    return {
        "task_id": 0,  # synthetic id for deterministic ordering fallback
        "name": "INDEX.md",
        "short_name": "INDEX",
        "kind": "index",
        "content": content,
    }

# -----------------
# Root Brief and Parent Chain (pinned)
# -----------------

def _get_ancestor_chain(task_id: int, repo: TaskRepository) -> List[Dict[str, Any]]:
    """Return ancestor chain from root -> ... -> parent of task_id (excludes self)."""
    chain: List[Dict[str, Any]] = []
    try:
        if hasattr(repo, "get_ancestors"):
            anc = repo.get_ancestors(task_id) or []
            # Normalize order: root first if depth is available
            try:
                anc = sorted(anc, key=lambda x: x.get("depth", 0))
            except Exception:
                pass
            chain = [a for a in anc if a.get("task_type") in {"root", "composite"}]
        else:
            cur = repo.get_parent(task_id) if hasattr(repo, "get_parent") else None
            guard = 0
            stack: List[Dict[str, Any]] = []
            while cur and guard < 100:
                stack.append(cur)
                if cur.get("task_type") == "root":
                    break
                cur = repo.get_parent(cur.get("id")) if hasattr(repo, "get_parent") else None
                guard += 1
            chain = list(reversed(stack))
    except Exception:
        chain = []
    return chain


def _synthesize_root_brief(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Build a concise Root Brief from the root's name and input prompt. Mark as pinned."""
    # Find root task
    root: Optional[Dict[str, Any]] = None
    try:
        me = _get_task_by_id(task_id, repo)
        if me and me.get("task_type") == "root":
            root = me
        else:
            chain = _get_ancestor_chain(task_id, repo)
            for a in chain:
                if a.get("task_type") == "root":
                    root = a
                    break
    except Exception:
        root = None
    if not root:
        return None

    rid = root.get("id")
    rname = root.get("name", "")
    try:
        rprompt = repo.get_task_input_prompt(rid) or ""
    except Exception:
        rprompt = ""
    # Trim overly long prompt
    max_len = 1200
    brief_body = (rprompt or "").strip()
    if len(brief_body) > max_len:
        brief_body = brief_body[:max_len].rstrip()

    content = f"# Root Brief\n\n- Title: {rname}\n- Problem/Goal:\n{brief_body}\n\n要求：所有子任务必须与上述主题保持一致；若信息不足，优先提问澄清或引用Root Brief。"
    return {
        "task_id": rid,
        "name": "ROOT_BRIEF",
        "short_name": "ROOT_BRIEF",
        "kind": "pinned:root_brief",
        "pinned": True,
        "content": content,
    }


def _synthesize_parent_chain(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Produce a single pinned section summarizing the ancestor chain (root -> ... -> parent)."""
    chain = _get_ancestor_chain(task_id, repo)
    if not chain:
        return None
    lines: List[str] = ["# Parent Chain"]
    for a in chain:
        aid = a.get("id")
        name = a.get("name", "")
        ttype = a.get("task_type", "")
        lines.append(f"- [{ttype}] {name} (id={aid})")
    content = "\n".join(lines)
    return {
        "task_id": chain[0].get("id"),
        "name": "PARENT_CHAIN",
        "short_name": "PARENT_CHAIN",
        "kind": "pinned:parent_chain",
        "pinned": True,
        "content": content,
    }


# GLM semantic retrieval replaces TF-IDF utilities


def _priority_key_local(s: Dict[str, Any]) -> Tuple[int, int]:
    kind = s.get("kind") or ""
    # Pinned sections always sort before everything else locally, then INDEX
    if isinstance(kind, str) and kind.startswith("pinned"):
        return (-2, int(s.get("task_id") or 0))
    if kind == "index":
        return (-1, int(s.get("task_id") or 0))
    try:
        group = PRIORITY_ORDER.index(kind)
    except ValueError:
        if isinstance(kind, str) and kind.startswith("dep:"):
            if "requires" in kind:
                group = 1  # dep:requires
            elif "refers" in kind:
                group = 2  # dep:refers
            else:
                group = 2
        elif kind == "ancestor":
            group = 3  # ancestor
        elif kind == "h_sibling":
            group = 5  # h_sibling
        elif kind == "sibling":
            group = 6  # sibling
        else:
            group = 7  # manual and others
    return (group, int(s.get("task_id") or 0))


def gather_context(
    task_id: int,
    repo: TaskRepository = default_repo,
    include_deps: bool = True,
    include_plan: bool = True,
    k: int = 5,
    manual: Optional[List[int]] = None,
    semantic_k: int = 5,
    min_similarity: float = 0.1,
    include_ancestors: bool = False,
    include_siblings: bool = False,
    hierarchy_k: int = 3,
) -> Dict[str, Any]:
    """Assemble a context bundle for a task using GLM semantic search.

    Args:
        task_id: Target task ID for context assembly
        repo: Task repository instance
        include_deps: Include upstream tasks connected via links (requires, refers)
        include_plan: Include sibling tasks in the same plan prefix
        k: Soft cap for number of items taken from each category
        manual: Optional explicit task IDs to include
        semantic_k: Number of semantically similar tasks to retrieve (default: 5)
        min_similarity: Minimum similarity threshold for semantic retrieval (default: 0.1)
        include_ancestors: Include parent/ancestor tasks in hierarchy
        include_siblings: Include sibling tasks in hierarchy (same parent)
        hierarchy_k: Soft cap for hierarchy-based items (ancestors + siblings)

    Returns:
        Dict containing task_id, sections list, and combined text string
    """
    sections: List[Dict[str, Any]] = []
    # 0) Pinned sections: Root Brief and Parent Chain — always at the top and never trimmed
    try:
        rb = _synthesize_root_brief(task_id, repo)
        if rb:
            sections.append(rb)
        pc = _synthesize_parent_chain(task_id, repo)
        if pc:
            sections.append(pc)
    except Exception:
        # Never block context assembly due to pinned synthesis issues
        pass
    seen_ids = set()

    # 1) Always include global index (INDEX.md) as an anchor (after pinned)
    try:
        sections.append(_index_section())
    except Exception:
        # Never block context assembly due to index read issues
        pass

    # 2) Dependencies (requires first, then refers) as provided by repo
    if include_deps:
        try:
            deps = repo.list_dependencies(task_id)
        except Exception:
            deps = []
        # deps already ordered by (kind priority, priority, id)
        for item in deps[:k]:
            sec = _section_for_task(item, repo, kind=f"dep:{item.get('kind','unknown')}")
            if sec and sec.get("task_id") not in seen_ids:
                sections.append(sec)
                seen_ids.add(sec["task_id"])

    # 3) Siblings in same plan (exclude self)
    if include_plan:
        me = _get_task_by_id(task_id, repo)
        if me and isinstance(me, dict):
            _, short = split_prefix(me.get("name", ""))
            title, _ = split_prefix(me.get("name", ""))
            if title:
                try:
                    siblings = repo.list_plan_tasks(title)
                except Exception:
                    siblings = []
                for s in siblings:
                    sid = s.get("id") if isinstance(s, dict) else None
                    if sid is None or sid == task_id:
                        continue
                    sec = _section_for_task(s, repo, kind="sibling")
                    if sec and sec.get("task_id") not in seen_ids:
                        sections.append(sec)
                        seen_ids.add(sec["task_id"])
                        if len([x for x in sections if x.get("kind") == "sibling"]) >= k:
                            break

    # 4) Hierarchy-based context (ancestors and siblings)
    if include_ancestors or include_siblings:
        try:
            # Get current task info to access hierarchy methods
            current_task = repo.get_task_info(task_id) if hasattr(repo, "get_task_info") else None

            # Add ancestors (parent chain)
            if include_ancestors and current_task:
                try:
                    ancestors = repo.get_ancestors(task_id) if hasattr(repo, "get_ancestors") else []
                    for anc in ancestors[:hierarchy_k]:
                        aid = anc.get("id")
                        if aid and aid not in seen_ids:
                            sec = _section_for_task(anc, repo, kind="ancestor")
                            if sec:
                                sections.append(sec)
                                seen_ids.add(aid)
                except Exception:
                    pass

            # Add hierarchy siblings (same parent, different from plan siblings)
            if include_siblings and current_task:
                try:
                    parent_id = current_task.get("parent_id")
                    if parent_id:
                        h_siblings = repo.get_children(parent_id) if hasattr(repo, "get_children") else []
                        added_h_siblings = 0
                        for sib in h_siblings:
                            sid = sib.get("id")
                            if sid and sid != task_id and sid not in seen_ids:
                                sec = _section_for_task(sib, repo, kind="h_sibling")
                                if sec:
                                    sections.append(sec)
                                    seen_ids.add(sid)
                                    added_h_siblings += 1
                                    if added_h_siblings >= hierarchy_k:
                                        break
                except Exception:
                    pass
        except Exception:
            # Never block context assembly due to hierarchy issues
            pass

    # 5) Manual selections
    if manual:
        for mid in manual:
            if mid in seen_ids:
                continue
            mtask = _get_task_by_id(mid, repo)
            if mtask:
                sec = _section_for_task(mtask, repo, kind="manual")
                if sec and sec.get("task_id") not in seen_ids:
                    sections.append(sec)
                    seen_ids.add(sec["task_id"])

    # 6) GLM semantic retrieval (always enabled with default parameters)
    if semantic_k > 0:
        # Query text: prefer current task input prompt, else task name
        me = _get_task_by_id(task_id, repo)
        query_text = repo.get_task_input_prompt(task_id) if me else None
        if not query_text and me and isinstance(me, dict):
            query_text = me.get("name", "")
        query_text = query_text or ""

        if _debug_on():
            _CTX_LOGGER.debug(
                {
                    "event": "gather_context.semantic_retrieval_start",
                    "task_id": task_id,
                    "query_text": query_text[:100],
                    "semantic_k": semantic_k,
                }
            )

        try:
            # Use GLM semantic retrieval service
            retrieval_service = get_retrieval_service()

            # Perform semantic retrieval
            retrieved_results = retrieval_service.search(query=query_text, k=semantic_k, min_similarity=min_similarity)

            # Convert results to sections
            added = 0
            for result in retrieved_results:
                tid = result.get("task_id")
                if not isinstance(tid, int) or tid == task_id or tid in seen_ids:
                    continue

                t = _get_task_by_id(tid, repo)
                if not t:
                    continue

                sec = _section_for_task(t, repo, kind="retrieved")
                if sec and sec.get("task_id") not in seen_ids:
                    # Add retrieval score metadata
                    sec["retrieval_score"] = result.get("similarity", 0.0)
                    sec["retrieval_method"] = "semantic"

                    sections.append(sec)
                    seen_ids.add(sec["task_id"])
                    added += 1

            if _debug_on():
                _CTX_LOGGER.debug(
                    {
                        "event": "gather_context.semantic_retrieval_done",
                        "task_id": task_id,
                        "retrieved": len(retrieved_results),
                        "added": added,
                        "top_score": retrieved_results[0].get("similarity", 0.0) if retrieved_results else 0.0,
                    }
                )

        except Exception as e:
            _CTX_LOGGER.warning(f"GLM semantic retrieval failed: {e}")

    # 6) Normalize ordering by priority groups even if no budget is applied
    sections = sorted(sections, key=_priority_key_local)

    if _debug_on():
        kind_counts: Dict[str, int] = {}
        for s in sections:
            knd = s.get("kind") or "?"
            kind_counts[knd] = kind_counts.get(knd, 0) + 1
        _CTX_LOGGER.debug(
            {
                "event": "gather_context.done",
                "task_id": task_id,
                "sections": len(sections),
                "kinds": kind_counts,
            }
        )

    # Build combined text (simple concatenation with headers)
    combined_parts: List[str] = []
    for s in sections:
        header = s.get("short_name") or s.get("name") or f"Task {s.get('task_id')}"
        combined_parts.append(f"## {header}\n\n{s.get('content','')}")
    combined = "\n\n".join(combined_parts)

    return {
        "task_id": task_id,
        "sections": sections,
        "combined": combined,
    }
