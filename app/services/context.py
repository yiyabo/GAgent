from typing import Any, Dict, List, Optional, Tuple
import os
import logging

from ..interfaces import TaskRepository
from ..repository.tasks import default_repo
from ..utils import split_prefix
from .context_budget import PRIORITY_ORDER
from .retrieval import get_retrieval_service

# Debug logging (opt-in via env: CTX_DEBUG/CONTEXT_DEBUG)
_CTX_LOGGER = logging.getLogger("app.context")


def _debug_on() -> bool:
    v = os.environ.get("CTX_DEBUG") or os.environ.get("CONTEXT_DEBUG")
    if not v:
        return False
    v = str(v).strip().lower()
    return v in {"1", "true", "yes", "on"}


def _get_task_by_id(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Prefer repository direct lookup when available; fallback to list scan."""
    # Prefer efficient repo method if provided
    if hasattr(repo, 'get_task_info'):
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


# GLM semantic retrieval replaces TF-IDF utilities


def _priority_key_local(s: Dict[str, Any]) -> Tuple[int, int]:
    kind = s.get("kind") or ""
    # Ensure global index sorts before everything else locally
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
    seen_ids = set()

    # 0) Always include global index (INDEX.md) as the top-priority anchor
    try:
        sections.append(_index_section())
    except Exception:
        # Never block context assembly due to index read issues
        pass

    # 1) Dependencies (requires first, then refers) as provided by repo
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

    # 2) Siblings in same plan (exclude self)
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

    # 3) Hierarchy-based context (ancestors and siblings)
    if include_ancestors or include_siblings:
        try:
            # Get current task info to access hierarchy methods
            current_task = repo.get_task_info(task_id) if hasattr(repo, 'get_task_info') else None
            
            # Add ancestors (parent chain)
            if include_ancestors and current_task:
                try:
                    ancestors = repo.get_ancestors(task_id) if hasattr(repo, 'get_ancestors') else []
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
                        h_siblings = repo.get_children(parent_id) if hasattr(repo, 'get_children') else []
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

    # 4) Manual selections
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

    # 5) GLM semantic retrieval (always enabled with default parameters)
    if semantic_k > 0:
        # Query text: prefer current task input prompt, else task name
        me = _get_task_by_id(task_id, repo)
        query_text = repo.get_task_input_prompt(task_id) if me else None
        if not query_text and me and isinstance(me, dict):
            query_text = me.get("name", "")
        query_text = query_text or ""

        if _debug_on():
            _CTX_LOGGER.debug({
                "event": "gather_context.semantic_retrieval_start",
                "task_id": task_id,
                "query_text": query_text[:100],
                "semantic_k": semantic_k,
            })

        try:
            # Use GLM semantic retrieval service
            retrieval_service = get_retrieval_service()
            
            # Perform semantic retrieval
            retrieved_results = retrieval_service.search(
                query=query_text,
                k=semantic_k,
                min_similarity=min_similarity
            )
            
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
                _CTX_LOGGER.debug({
                    "event": "gather_context.semantic_retrieval_done",
                    "task_id": task_id,
                    "retrieved": len(retrieved_results),
                    "added": added,
                    "top_score": retrieved_results[0].get("similarity", 0.0) if retrieved_results else 0.0,
                })
                
        except Exception as e:
            _CTX_LOGGER.warning(f"GLM semantic retrieval failed: {e}")

    # 6) Normalize ordering by priority groups even if no budget is applied
    sections = sorted(sections, key=_priority_key_local)

    if _debug_on():
        kind_counts: Dict[str, int] = {}
        for s in sections:
            knd = s.get("kind") or "?"
            kind_counts[knd] = kind_counts.get(knd, 0) + 1
        _CTX_LOGGER.debug({
            "event": "gather_context.done",
            "task_id": task_id,
            "sections": len(sections),
            "kinds": kind_counts,
        })

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
