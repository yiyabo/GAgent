"""Layer3 phase narrator: cached semantic titles for plan phases.

The dependency layering in :mod:`app.services.plans.todo_list` is authoritative.
This module only produces human-facing titles keyed by ``phase_id`` and never
influences task grouping or execution order; any malformed or mismatched LLM
output is discarded so callers fall back to neutral ``Phase N`` labels.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.llm import get_default_client

from .plan_models import PlanTree
from .todo_list import build_full_plan_todo_list

logger = logging.getLogger(__name__)

PHASE_TITLES_METADATA_KEY = "layer3_phase_titles"

_MAX_TITLE_LEN = 60
_MAX_TASKS_PER_PHASE_IN_PROMPT = 8


def extract_phase_labels(metadata: Optional[Dict[str, Any]]) -> Dict[int, str]:
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get(PHASE_TITLES_METADATA_KEY)
    if not isinstance(raw, dict):
        return {}
    labels: Dict[int, str] = {}
    for key, value in raw.items():
        try:
            phase_id = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, str) and value.strip():
            labels[phase_id] = value.strip()
    return labels


def _extract_json_object(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def _build_prompt(phase_tasks: Dict[int, List[str]]) -> str:
    lines = [
        "You label execution phases of a bioinformatics task plan.",
        "Give each phase a concise 2-5 word English title describing its tasks.",
        "Return ONLY a JSON object mapping each phase index (as a string) to its title.",
        "",
    ]
    for phase_id in sorted(phase_tasks):
        names = phase_tasks[phase_id][:_MAX_TASKS_PER_PHASE_IN_PROMPT]
        lines.append(f"Phase {phase_id}: " + "; ".join(names))
    return "\n".join(lines)


async def generate_phase_titles(tree: PlanTree) -> Dict[int, str]:
    todo = build_full_plan_todo_list(tree, ordering_mode="dependency_phase")
    phases = todo.phases
    if len(phases) <= 1:
        return {}

    phase_tasks: Dict[int, List[str]] = {
        phase.phase_id: [item.name for item in phase.items] for phase in phases
    }
    expected_ids = set(phase_tasks)

    try:
        raw = await get_default_client().chat_async(
            _build_prompt(phase_tasks), max_tokens=300
        )
    except Exception as exc:
        logger.warning("Layer3 phase narration LLM call failed: %s", exc)
        return {}

    try:
        parsed = json.loads(_extract_json_object(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Layer3 phase narration produced unparseable output: %s", exc)
        return {}

    if not isinstance(parsed, dict):
        return {}

    titles: Dict[int, str] = {}
    for key, value in parsed.items():
        try:
            phase_id = int(key)
        except (TypeError, ValueError):
            return {}
        if not isinstance(value, str) or not value.strip():
            return {}
        titles[phase_id] = value.strip()[:_MAX_TITLE_LEN]

    if set(titles) != expected_ids:
        logger.warning(
            "Layer3 phase narration phase-id mismatch (got %s, expected %s); discarding.",
            sorted(titles),
            sorted(expected_ids),
        )
        return {}

    return titles
