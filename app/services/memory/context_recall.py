"""One bounded recall envelope for chat, DeepThink and plan executors.

Recall reads existing evidence; it does not learn model weights, spend tokens on
embedding calls, or turn a historical assistant claim into a verified result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.repository import context_recall as repository

logger = logging.getLogger(__name__)
MAX_CONTEXT_CHARS = 6500
_RECALL_CUE = re.compile(r"上次|之前|以前|昨天|前天|上周|上个月|历史|记得|回顾|曾经|earlier|previous|last (?:time|week|month)|yesterday|remember", re.I)
_STOP = {"the", "and", "for", "with", "from", "this", "that", "please", "what", "was", "were", "have", "我们", "一下", "什么", "这个", "那个", "帮我", "看看", "结果", "任务", "项目"}


def query_terms(query: str) -> list[str]:
    text = _RECALL_CUE.sub(" ", query[:8000].lower())
    parts = re.findall(r"[a-z0-9_./-]{3,}|[\u4e00-\u9fff]+", text)
    tokens = []
    for part in parts:
        tokens.extend([part[:120]] if not re.search(r"[\u4e00-\u9fff]", part) else [part[i:i+2] for i in range(len(part)-1)])
    return list(dict.fromkeys(term for term in tokens if term not in _STOP))[:16]


def _score(content: str, terms: list[str]) -> int:
    text = content.lower()
    return sum(term in text for term in terms)


def recall(session_id: str | None, query: str, *, enabled: bool = True, client_message_id: str | None = None, history: bool | None = None) -> dict:
    result: dict = {"version": 1, "memories": [], "history": []}
    if not enabled or not session_id:
        return result
    scope = repository.session_scope(session_id)
    if not scope:
        return result
    result["scope"] = {"session_id": scope["id"], "project_id": scope["project_id"]}
    terms = query_terms(query)
    notes = [row for row in repository.memory_candidates(scope, terms)
             if not (row["source_session_id"] == session_id and row["content"].strip() in {query.strip(), "[user] " + query.strip()})]
    notes.sort(key=lambda row: (_score(row["content"] + str(row.get("keywords") or ""), terms), row.get("importance") in {"critical", "high"}, str(row.get("created_at") or "")), reverse=True)
    profile = [row for row in notes if row['scope'] == 'user' and row['importance'] in {'critical','high'}][:2]
    notes = profile + [row for row in notes if row not in profile]
    seen = set()
    for row in notes:
        identity = row["content"].strip()
        if identity in seen:
            continue
        seen.add(identity)
        result["memories"].append({"id": row["id"], "content": row["content"][:900], "memory_type": row["memory_type"], "scope": row["scope"], "session_id": row["source_session_id"], "source_store": row["source_store"], "importance": row["importance"], "created_at": str(row.get("created_at") or "")})
        if len(result["memories"]) == 5:
            break
    if history is True or (history is None and _RECALL_CUE.search(query)):
        rows = repository.history_candidates(scope, terms, client_message_id=client_message_id, current_message=query if history is not True else None)
        rows.sort(key=lambda row: (_score(row["content"], terms), row["id"]), reverse=True)
        anchors = rows[:3]
        neighbors = repository.history_neighbors(scope, anchors, client_message_id=client_message_id, current_message=query if history is not True else None)
        selected = {row['id']: row for row in [*anchors, *neighbors]}
        for row in sorted(selected.values(), key=lambda row: (row['session_id'],row['id'])):
            try:
                metadata = json.loads(row.get("metadata") or "{}")
            except (ValueError, TypeError):
                metadata = {}
            result["history"].append({"message_id": row["id"], "session_id": row["session_id"], "session_title": row["session_title"], "role": row["role"], "content": row["content"][:900], "created_at": str(row["created_at"]), "status": metadata.get("status") if isinstance(metadata, dict) else None})
            if row['role']=='assistant':
                from .artifact_recall import artifact_references
                refs=artifact_references(row['session_id'],row['content'])
                if refs:result['history'][-1]['artifact_refs']=refs
    # The same admitted evidence is shown in the UI and in every prompt.
    while len(json.dumps(result, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        key = "history" if result["history"] else "memories"
        if not result[key]:
            break
        result[key].pop()
    return result


def hydrate_context(context: dict[str, Any], session_id: str | None, query: str, *, client_message_id: str | None = None, refresh: bool = False) -> dict:
    if not refresh and isinstance(context.get("recall_context"), dict):
        return context
    try:
        context["recall_context"] = recall(session_id, query, enabled=context.get("memory_enabled") is not False, client_message_id=client_message_id)
    except Exception as exc:
        # Recall is an optional reference service; failure is visible, not a chat failure.
        logger.warning("Context recall unavailable: %s", type(exc).__name__)
        context["recall_context"] = {"version": 1, "memories": [], "history": [], "unavailable": True}
    return context


async def hydrate_chat_context(context: dict, session_id: str | None, query: str, *, client_message_id: str | None = None) -> None:
    await asyncio.to_thread(hydrate_context, context, session_id, query, client_message_id=client_message_id, refresh=True)


def format_recall_context(context: dict | None) -> str:
    if not context or context.get("memory_enabled") is False:
        return ""
    envelope = context.get("recall_context")
    if not isinstance(envelope, dict):
        # Older clients may still send notes; preserve them without mutating context.
        notes = context.get("memories")
        envelope = {"memories": notes[:5] if isinstance(notes, list) else [], "history": []}
    if not envelope.get("memories") and not envelope.get("history"):
        return ""
    return ("=== RECALL REFERENCES ===\n"
            "These are past notes and original messages, not verified current results. Use only when relevant. "
            "Do not claim that recalled work has run in this turn. Verify old file paths before reuse. "
            "artifact_refs resolve links in their source session; untracked paths are current locations, not verified historical snapshots. "
            "When relying on history, identify its session/date/message source; if evidence is absent, say so.\n"
            + json.dumps(envelope, ensure_ascii=False)[:MAX_CONTEXT_CHARS])


def inherit_recall_context(agent: Any) -> dict:
    extra = getattr(agent, "extra_context", None) or {}
    return {key: extra[key] for key in ("recall_context", "memory_enabled", "learned_skill_context", "learned_skill_ids", "learned_skill_versions") if key in extra}


def attach_recall_metadata(metadata: dict, context: dict | None) -> None:
    envelope = (context or {}).get("recall_context")
    if isinstance(envelope, dict) and (envelope.get("memories") or envelope.get("history") or envelope.get("unavailable")):
        metadata["recall_context"] = envelope
