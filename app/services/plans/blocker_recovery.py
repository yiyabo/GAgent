"""Bounded self-repair when a plan task reports that it is blocked.

A task that cannot proceed reports ``BLOCKED_DEPENDENCY``. Before this module
the background chain simply stopped there, even when the blocker was a fixable
problem inside an earlier task (for example a reference download that stopped
at the 5-minute cell limit). Here the job layer gets one bounded repair round:

1. diagnose which task owns the blocker (the blocked task itself or one of the
   tasks that ran before it) and what to try differently — one LLM call, with a
   deterministic fallback when the model is unavailable;
2. rerun that task with a repair brief describing the failure and the
   alternatives to try;
3. rerun the blocked task once.

Only dependency-blocked results are handled. Ordinary failures already went
through PlanExecutor's own bounded repair, so they are not retried again here.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.run_budget import RunDeadlineExceeded

logger = logging.getLogger(__name__)

SELF_REPAIR_CONTEXT_KEY = "plan_self_repair"
_MAX_TEXT = 1800
_MAX_CANDIDATES = 8

_GENERIC_ALTERNATIVES = (
    (
        "Look for an existing copy of the missing resource first: session files, upstream task outputs and the "
        "shared reference data directory (/data) or bio_tools databases."
    ),
    (
        "If a long download or computation is genuinely needed and can finish in a reasonable time, run it in one "
        "execute_code cell with a larger timeout_seconds (up to 2 hours), resuming any partial file (`curl -C -`), "
        "then verify checksums."
    ),
    (
        "Prefer a server-side tool (bio_tools, native runners) over in-cell work for heavy jobs such as hmmscan "
        "or large database builds."
    ),
    (
        "If one method is unavailable, use an equivalent documented method and record the substitution and its "
        "limits instead of stopping."
    ),
)


def enabled() -> bool:
    raw = (os.getenv("PLAN_BLOCKER_RECOVERY_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def max_rounds_per_job() -> int:
    try:
        value = int(os.getenv("PLAN_BLOCKER_RECOVERY_MAX_ROUNDS") or 2)
    except ValueError:
        value = 2
    return max(0, min(5, value))


def is_blocked_result(result: Any) -> bool:
    if result is None or str(getattr(result, "status", "") or "").strip().lower() not in {"skipped", "blocked"}:
        return False
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict) or not metadata.get("blocked_by_dependencies"):
        return False
    if metadata.get("termination_reason") in {"cancelled", "run_deadline_exceeded", "llm_unavailable"}:
        return False
    return not metadata.get(SELF_REPAIR_CONTEXT_KEY)


@dataclass
class RepairDiagnosis:
    target_task_id: int
    brief: str
    alternatives: list[str] = field(default_factory=list)
    source: str = "heuristic"


@dataclass
class RecoveryOutcome:
    result: Any
    diagnosis: RepairDiagnosis
    repair_status: str
    summary: dict[str, Any]


def _clip(text: Any, limit: int = _MAX_TEXT) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[:limit] + " …"


def _task_text(node: Any) -> str:
    raw = getattr(node, "execution_result", None) or ""
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return _clip(raw)
    if not isinstance(payload, dict):
        return _clip(raw)
    parts = [str(payload.get("content") or "")]
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    failures = metadata.get("tool_failures") if isinstance(metadata, dict) else None
    if isinstance(failures, list) and failures:
        parts.append("Tool failures: " + "; ".join(_clip(json.dumps(f, ensure_ascii=False), 300) for f in failures[:5]))
    return _clip("\n".join(p for p in parts if p))


def _candidates(tree: Any, blocked_task_id: int, task_order: Sequence[int]) -> list[Any]:
    nodes = getattr(tree, "nodes", {}) or {}
    blocked = nodes.get(blocked_task_id)
    ids: list[int] = []
    for dep in list(getattr(blocked, "dependencies", None) or []):
        if dep in nodes and dep not in ids:
            ids.append(dep)
    if blocked_task_id in task_order:
        earlier = list(task_order)[: list(task_order).index(blocked_task_id)]
        for task_id in reversed(earlier):
            if task_id in nodes and task_id not in ids:
                ids.append(task_id)
    ids = [i for i in ids if getattr(nodes[i], "status", "") == "completed"][:_MAX_CANDIDATES]
    return [nodes[i] for i in ids]


_TASK_REF_RE = re.compile(r"(?:task|任务)\s*#?\s*(\d+)", re.IGNORECASE)


def _heuristic(blocked: Any, detail: str, candidates: Sequence[Any]) -> RepairDiagnosis:
    by_id = {c.id: c for c in candidates}
    target: int | None = None
    for match in _TASK_REF_RE.finditer(detail):
        task_id = int(match.group(1))
        if task_id in by_id:
            target = task_id
            break
    if target is None:
        for candidate in candidates:
            name = str(getattr(candidate, "name", "") or "").split()[0:1]
            if name and len(name[0]) >= 2 and name[0] in detail:
                target = candidate.id
                break
    if target is None:
        for candidate in candidates:
            if "BLOCKED" in _task_text(candidate).upper():
                target = candidate.id
                break
    resolved: int = target if target is not None else (candidates[0].id if candidates else blocked.id)
    return RepairDiagnosis(target_task_id=resolved, brief=_clip(detail, 900),
                           alternatives=list(_GENERIC_ALTERNATIVES), source="heuristic")


_SYSTEM_PROMPT = """You repair a stalled research plan. A task reported that it is blocked.
Decide which task must be rerun to remove the blocker, and what that rerun must do differently.

Rules:
- target_task_id is the blocked task itself when the problem is inside it (tooling, timeout, environment),
  or one of the listed earlier tasks when that task left its output incomplete.
- Do not suggest inventing data, skipping validation or lowering quality gates.
- Give 2-5 concrete alternatives the rerun should try (a longer execute_code timeout_seconds for a needed download,
  existing shared copies under /data or bio_tools databases, server-side tools such as bio_tools hmmer, an
  equivalent documented method). Each alternative under 200 characters.

Return STRICT JSON only:
{"target_task_id": <int>, "brief": "<what went wrong, under 400 characters>", "alternatives": ["...", "..."]}"""


def _parse(raw: str, allowed: Sequence[int]) -> RepairDiagnosis | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw_target = data.get("target_task_id")
    if isinstance(raw_target, bool) or not isinstance(raw_target, (int, str)):
        return None
    try:
        target = int(raw_target)
    except ValueError:
        return None
    if target not in allowed:
        return None
    brief = _clip(data.get("brief"), 900)
    alternatives = [_clip(item, 300) for item in (data.get("alternatives") or []) if str(item or "").strip()][:5]
    if not brief:
        return None
    return RepairDiagnosis(target_task_id=target, brief=brief, alternatives=alternatives, source="llm")


def diagnose(blocked: Any, detail: str, candidates: Sequence[Any],
             chat: Callable[[str], str] | None = None) -> RepairDiagnosis:
    fallback = _heuristic(blocked, detail, candidates)
    lines = [
        _SYSTEM_PROMPT,
        "",
        f"Blocked task #{blocked.id}: {blocked.display_name()}",
        f"Instruction: {_clip(getattr(blocked, 'instruction', ''), 800)}",
        f"Blocker report: {_clip(detail, 1500)}",
        "",
        "Earlier completed tasks (most recent first):",
    ]
    for candidate in candidates:
        lines.append(f"- #{candidate.id} {candidate.display_name()}: {_clip(_task_text(candidate), 900)}")
    prompt = "\n".join(lines)
    try:
        if chat is None:
            from app.llm import usage_context_override
            from app.services.llm.llm_service import get_llm_service

            with usage_context_override(phase="plan_repair", call_purpose="plan_blocker_diagnosis"):
                raw = get_llm_service().chat(prompt, max_tokens=700, timeout=45, retry_attempts=1)
        else:
            raw = chat(prompt)
    except RunDeadlineExceeded:
        raise
    except Exception as exc:  # fail soft: a heuristic diagnosis still allows the repair round
        logger.warning("Blocker diagnosis LLM call failed for task %s: %s", blocked.id, exc)
        return fallback
    allowed = [blocked.id] + [c.id for c in candidates]
    parsed = _parse(raw, allowed)
    if parsed is None:
        return fallback
    if not parsed.alternatives:
        parsed.alternatives = list(_GENERIC_ALTERNATIVES)
    return parsed


def repair_context(base: dict[str, Any] | None, *, diagnosis: RepairDiagnosis, blocked_task_id: int,
                   role: str) -> dict[str, Any]:
    context = dict(base or {})
    context[SELF_REPAIR_CONTEXT_KEY] = {
        "role": role,
        "blocked_task_id": blocked_task_id,
        "target_task_id": diagnosis.target_task_id,
        "brief": diagnosis.brief,
        "alternatives": list(diagnosis.alternatives),
    }
    return context


def prompt_lines(session_context: dict[str, Any] | None) -> list[str]:
    info = (session_context or {}).get(SELF_REPAIR_CONTEXT_KEY)
    if not isinstance(info, dict):
        return []
    alternatives = "\n".join(f"  - {item}" for item in info.get("alternatives") or [])
    if info.get("role") == "upstream_repair":
        head = (f"SELF-REPAIR RUN: task #{info.get('blocked_task_id')} is blocked by this task's output. "
                "The previous attempt of this task did not remove the blocker:")
    else:
        head = "SELF-REPAIR RETRY: the previous attempt of this task stopped as blocked:"
    line = (
        f"{head} {info.get('brief')}\n"
        "Do not repeat the same approach. Try these alternatives in order and keep provenance of what changed:\n"
        f"{alternatives}\n"
        "Reuse valid partial outputs. Report BLOCKED_DEPENDENCY again only after these alternatives were actually "
        "tried, and state each attempt and why it failed."
    )
    return [line]


def _execute(executor: Any, plan_id: int, task_id: int, config_factory: Callable[..., Any],
             session_context: dict[str, Any]) -> Any:
    return executor.execute_task(plan_id, task_id, config=config_factory(session_context=session_context,
                                                                        force_rerun=True))


def attempt(*, executor: Any, plan_id: int, task_id: int, result: Any, task_order: Sequence[int],
            session_context: dict[str, Any] | None, config_factory: Callable[..., Any],
            log: Callable[..., Any], chat: Callable[[str], str] | None = None) -> RecoveryOutcome | None:
    """Run one repair round for a blocked task; return ``None`` when not applicable."""
    if not enabled() or not is_blocked_result(result):
        return None
    tree = executor._repo.get_plan_tree(plan_id)
    blocked = getattr(tree, "nodes", {}).get(task_id)
    if blocked is None:
        return None
    metadata = getattr(result, "metadata", None) or {}
    detail = str(metadata.get("blocked_dependency_detail") or getattr(result, "content", "") or "")
    candidates = _candidates(tree, task_id, task_order)
    diagnosis = diagnose(blocked, detail, candidates, chat=chat)
    log("info", "Self-repair: diagnosing blocked task.", {
        "sub_type": "self_repair", "task_id": task_id, "target_task_id": diagnosis.target_task_id,
        "source": diagnosis.source, "brief": diagnosis.brief,
    })
    summary: dict[str, Any] = {"blocked_task_id": task_id, "target_task_id": diagnosis.target_task_id,
                               "diagnosis_source": diagnosis.source, "brief": diagnosis.brief}
    repair_status = "not_needed"
    if diagnosis.target_task_id != task_id:
        upstream = _execute(executor, plan_id, diagnosis.target_task_id, config_factory,
                            repair_context(session_context, diagnosis=diagnosis, blocked_task_id=task_id,
                                           role="upstream_repair"))
        repair_status = str(getattr(upstream, "status", "") or "unknown")
        summary["upstream_status"] = repair_status
        log("info" if repair_status == "completed" else "warning", "Self-repair: upstream task rerun finished.", {
            "sub_type": "self_repair", "task_id": diagnosis.target_task_id, "blocked_task_id": task_id,
            "status": repair_status,
        })
        if repair_status != "completed":
            # Upstream is still blocked: retrying the downstream task would only repeat the blocker.
            return RecoveryOutcome(result=result, diagnosis=diagnosis, repair_status=repair_status, summary=summary)
    retried = _execute(executor, plan_id, task_id, config_factory,
                       repair_context(session_context, diagnosis=diagnosis, blocked_task_id=task_id,
                                      role="blocked_retry"))
    retried_meta = getattr(retried, "metadata", None)
    if isinstance(retried_meta, dict):
        retried_meta.setdefault(SELF_REPAIR_CONTEXT_KEY, summary)
    summary["retry_status"] = str(getattr(retried, "status", "") or "unknown")
    log("info" if summary["retry_status"] == "completed" else "warning", "Self-repair: blocked task retried.", {
        "sub_type": "self_repair", "task_id": task_id, "status": summary["retry_status"],
    })
    return RecoveryOutcome(result=retried, diagnosis=diagnosis, repair_status=repair_status, summary=summary)


__all__ = [
    "SELF_REPAIR_CONTEXT_KEY",
    "RecoveryOutcome",
    "RepairDiagnosis",
    "attempt",
    "diagnose",
    "enabled",
    "is_blocked_result",
    "max_rounds_per_job",
    "prompt_lines",
    "repair_context",
]
