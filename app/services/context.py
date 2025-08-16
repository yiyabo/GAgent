from typing import Any, Dict, List, Optional, Tuple
import math
import re
import os
import logging
from functools import lru_cache

from ..interfaces import TaskRepository
from ..repository.tasks import default_repo
from ..utils import split_prefix
from .context_budget import PRIORITY_ORDER

# Debug logging (opt-in via env: CTX_DEBUG/CONTEXT_DEBUG)
_CTX_LOGGER = logging.getLogger("app.context")


def _debug_on() -> bool:
    v = os.environ.get("CTX_DEBUG") or os.environ.get("CONTEXT_DEBUG")
    if not v:
        return False
    v = str(v).strip().lower()
    return v in {"1", "true", "yes", "on"}


def _get_task_by_id(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Inefficient but simple lookup using list_all_tasks().
    Phase 1 keeps repo surface small; we can add get_task() later if needed.
    """
    try:
        rows = repo.list_all_tasks()
    except Exception:
        return None
    for r in rows:
        try:
            if r.get("id") == task_id:
                return r
        except Exception:
            pass
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


# -----------------
# TF-IDF utilities
# -----------------

_TOKEN_RE = re.compile(r"[\w]+", flags=re.UNICODE)

# Minimal English stopwords (extend as needed). Chinese is not aggressively filtered.
_STOPWORDS = {
    "the","is","a","an","and","or","to","in","of","for","with","on","at","by","from","as","it","this","that",
    "these","those","be","are","was","were","been","being","have","has","had","do","does","did","but","not","no",
    "so","if","then","than","also","we","you","i","me","my","our","your","their","they","them","he","she","his",
    "her","its","what","which","who","whom","whose","can","could","should","would","may","might","will","shall",
    "about","into","over","after","before","again","further","here","there","when","where","why","how","all","any",
    "both","each","few","more","most","other","some","such","only","own","same","too","very"
}

# Tunables via env (keep defaults backward compatible)
TFIDF_MAX_CANDIDATES = int(os.environ.get("TFIDF_MAX_CANDIDATES", "500"))
TFIDF_MIN_SCORE = float(os.environ.get("TFIDF_MIN_SCORE", "0"))


def _is_cjk_char(ch: str) -> bool:
    """Return True if the given character is a CJK/Han/Hangul/Kana character.
    This is a minimal heuristic to keep single-character tokens for CJK languages.
    """
    try:
        code = ord(ch)
    except Exception:
        return False
    return (
        0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3040 <= code <= 0x309F  # Hiragana
        or 0x30A0 <= code <= 0x30FF  # Katakana
        or 0xAC00 <= code <= 0xD7AF  # Hangul Syllables
        or 0x1100 <= code <= 0x11FF  # Hangul Jamo
        or 0x3130 <= code <= 0x318F  # Hangul Compatibility Jamo
    )


@lru_cache(maxsize=4096)
def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    toks = [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]
    # Filter stopwords, numeric-only tokens, and single-char noise (keep single-char CJK)
    out: List[str] = []
    for t in toks:
        if t in _STOPWORDS:
            continue
        if t.isdigit():
            continue
        if len(t) == 1:
            if not _is_cjk_char(t[0]):
                continue
        out.append(t)
    return out


def _tfidf_scores(query_tokens: List[str], docs_tokens: List[List[str]]) -> List[float]:
    # Document frequencies
    N = len(docs_tokens)
    df: Dict[str, int] = {}
    for toks in docs_tokens:
        seen = set(toks)
        for t in seen:
            df[t] = df.get(t, 0) + 1

    # IDF with smoothing
    idf: Dict[str, float] = {}
    for t, c in df.items():
        idf[t] = math.log(1.0 + (N / (1.0 + c)))

    # Query term set
    qset = set(query_tokens)

    scores: List[float] = []
    for toks in docs_tokens:
        if not toks:
            scores.append(0.0)
            continue
        # term frequencies normalized by doc length
        tf: Dict[str, float] = {}
        inv_len = 1.0 / float(len(toks))
        for t in toks:
            tf[t] = tf.get(t, 0.0) + inv_len
        s = 0.0
        for t in qset:
            s += tf.get(t, 0.0) * idf.get(t, 0.0)
        scores.append(s)
    return scores


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
                group = 0
            elif "refers" in kind:
                group = 1
            else:
                group = 1
        else:
            group = 2 if kind == "sibling" else 3
    return (group, int(s.get("task_id") or 0))


def gather_context(
    task_id: int,
    repo: TaskRepository = default_repo,
    include_deps: bool = True,
    include_plan: bool = True,
    k: int = 5,
    manual: Optional[List[int]] = None,
    tfidf_k: Optional[int] = None,
    tfidf_min_score: Optional[float] = None,
    tfidf_max_candidates: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble a context bundle for a task.

    - include_deps: include upstream tasks connected via links (requires, refers)
    - include_plan: include sibling tasks in the same plan prefix
    - k: soft cap for number of items taken from each category
    - manual: optional explicit task IDs to include

    Returns a structured dict with sections and a combined string.
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

    # 3) Manual selections
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

    # 4) Optional TF-IDF retrieval from task_outputs across all tasks
    if isinstance(tfidf_k, int) and tfidf_k > 0:
        # Query text: prefer current task input prompt, else task name
        me = _get_task_by_id(task_id, repo)
        query_text = repo.get_task_input_prompt(task_id) if me else None
        if not query_text and me and isinstance(me, dict):
            query_text = me.get("name", "")
        query_text = query_text or ""

        # Build candidate docs from all tasks with outputs
        try:
            all_tasks = repo.list_all_tasks()
        except Exception:
            all_tasks = []
        candidates: List[Tuple[int, str]] = []
        for t in all_tasks:
            try:
                tid = t.get("id")  # type: ignore
            except Exception:
                tid = None
            if not isinstance(tid, int) or tid == task_id:
                continue
            if tid in seen_ids:
                continue
            content = repo.get_task_output_content(tid)
            if content and isinstance(content, str) and content.strip():
                candidates.append((tid, content))

        # Determine effective caps/thresholds (overrides if provided)
        try:
            eff_max_candidates = int(tfidf_max_candidates) if (tfidf_max_candidates is not None) else int(TFIDF_MAX_CANDIDATES)
        except Exception:
            eff_max_candidates = int(TFIDF_MAX_CANDIDATES)
        try:
            eff_min_score = float(tfidf_min_score) if (tfidf_min_score is not None) else float(TFIDF_MIN_SCORE)
        except Exception:
            eff_min_score = float(TFIDF_MIN_SCORE)

        # Cap candidate size for performance (deterministic order from repo)
        if eff_max_candidates <= 0:
            candidates = []
        elif len(candidates) > eff_max_candidates:
            candidates = candidates[:eff_max_candidates]

        if _debug_on():
            _CTX_LOGGER.debug({
                "event": "gather_context.tfidf_candidates",
                "task_id": task_id,
                "candidates": len(candidates),
                "tfidf_k": tfidf_k,
            })

        if candidates:
            # Compute simple TF-IDF scores
            tok_q = _tokenize(query_text)
            if tok_q:
                # Pre-tokenize docs
                doc_tokens: List[List[str]] = [_tokenize(text) for _, text in candidates]
                scores = _tfidf_scores(tok_q, doc_tokens)
                # Take top tfidf_k by score
                order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
                added = 0
                for idx in order:
                    if scores[idx] < eff_min_score:
                        break
                    tid, _ = candidates[idx]
                    t = _get_task_by_id(tid, repo)
                    if not t:
                        continue
                    sec = _section_for_task(t, repo, kind="retrieved")
                    if sec and sec.get("task_id") not in seen_ids:
                        sections.append(sec)
                        seen_ids.add(sec["task_id"])
                        added += 1
                        if added >= int(tfidf_k):
                            break
                if _debug_on():
                    _CTX_LOGGER.debug({
                        "event": "gather_context.tfidf_done",
                        "task_id": task_id,
                        "query_len": len(tok_q),
                        "added": added,
                        "top_score": (scores[order[0]] if order else 0.0),
                        "min_score_threshold": eff_min_score,
                    })

    # 5) Normalize ordering by priority groups even if no budget is applied
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
