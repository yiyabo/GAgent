"""Declarative acceptance v2: optional LLM-based deliverable spec extraction.

Default OFF (``DEEP_THINK_ACCEPTANCE_V2_ENABLED``). When enabled and the v1
type heuristic already detects deliverable intent, a single short streaming
JSON call extracts the full spec (count / type / format / constraints /
in-place overwrite). Any failure — disabled tier, timeout, exception, invalid
JSON — returns ``None`` and the run silently keeps the v1 heuristic.

The extracted spec only drives deterministic verification: counts are checked
by file extension on disk; content constraints are surfaced to the model as
prompt guidance, never judged by the LLM itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.llm import stream_chat_collect_async
from app.services.deep_think.text_utils import (
    _EXPECT_KIND_EXTS,
    _acceptance_v2_enabled,
    _acceptance_v2_max_tokens,
    _acceptance_v2_timeout_seconds,
)

logger = logging.getLogger(__name__)

_MAX_OUTPUTS = 6
_MAX_MIN_COUNT = 10
_MAX_CONSTRAINT_CHARS = 240
_MAX_TARGET_PATH_CHARS = 200
_MAX_QUERY_CHARS = 4000
_ALLOWED_TIERS = {"execute", "research"}

_KIND_ALIASES = {
    "image": "image",
    "figure": "image",
    "plot": "image",
    "chart": "image",
    "picture": "image",
    "图": "image",
    "图片": "image",
    "图表": "image",
    "data": "data",
    "table": "data",
    "spreadsheet": "data",
    "csv": "data",
    "dataset": "data",
    "表格": "data",
    "数据表": "data",
    "document": "document",
    "doc": "document",
    "report": "document",
    "markdown": "document",
    "md": "document",
    "报告": "document",
    "文档": "document",
}

_ALLOWED_EXTS = {ext for exts in _EXPECT_KIND_EXTS.values() for ext in exts} | {
    ".json",
    ".jsonl",
    ".parquet",
    ".html",
    ".htm",
    ".tex",
    ".bib",
}


@dataclass
class RequiredOutput:
    kind: str
    min_count: int = 1
    extensions: List[str] = field(default_factory=list)
    constraints: str = ""
    target_path: Optional[str] = None
    in_place: bool = False


@dataclass
class AcceptanceSpec:
    required_outputs: List[RequiredOutput] = field(default_factory=list)
    source: str = "v2_llm"
    fallback_used: bool = False
    raw_error: Optional[str] = None


def _extract_json_object(raw: str) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


def _normalize_kind(value: Any) -> str:
    key = str(value or "").strip().lower()
    return _KIND_ALIASES.get(key, "other")


def _normalize_extensions(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        ext = str(item or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in _ALLOWED_EXTS or ext in out:
            continue
        out.append(ext)
        if len(out) >= 8:
            break
    return out


def _normalize_min_count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(_MAX_MIN_COUNT, parsed))


def _normalize_constraints(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:_MAX_CONSTRAINT_CHARS]


def _normalize_target_path(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text or ".." in text or text.startswith("/") or "\\" in text:
        return None
    return text[:_MAX_TARGET_PATH_CHARS]


def parse_acceptance_spec(raw: Any) -> Optional[AcceptanceSpec]:
    candidate = _extract_json_object(str(raw or ""))
    if candidate is None:
        return None
    try:
        payload = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("required_outputs")
    if not isinstance(items, list):
        return None
    outputs: List[RequiredOutput] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        outputs.append(
            RequiredOutput(
                kind=_normalize_kind(item.get("kind")),
                min_count=_normalize_min_count(item.get("min_count")),
                extensions=_normalize_extensions(item.get("extensions")),
                constraints=_normalize_constraints(item.get("constraints")),
                target_path=_normalize_target_path(item.get("target_path")),
                in_place=bool(item.get("in_place")),
            )
        )
        if len(outputs) >= _MAX_OUTPUTS:
            break
    if not outputs:
        return None
    return AcceptanceSpec(required_outputs=outputs)


def build_extraction_prompt(user_query: str) -> str:
    query = str(user_query or "").strip()[:_MAX_QUERY_CHARS]
    return (
        "Extract the deliverable specification from the user request below as STRICT JSON only.\n"
        "Schema: {\"required_outputs\": [{\"kind\": \"image|data|document|other\", "
        "\"min_count\": <int 1-10>, \"extensions\": [\".png\", \".md\", ...], "
        "\"constraints\": \"short content requirement\", "
        "\"target_path\": \"relative/path or empty\", \"in_place\": true|false}]}\n"
        "Rules: only include outputs the user explicitly requested as files/deliverables; "
        "use in_place=true when the user asks to modify or overwrite an existing file; "
        "no prose, no markdown fence, JSON object only.\n"
        f"User request:\n{query}\n"
    )


async def extract_acceptance_spec(agent: Any, user_query: str) -> Optional[AcceptanceSpec]:
    if not _acceptance_v2_enabled():
        return None
    tier_fn = getattr(agent, "_request_tier", None)
    tier = str(tier_fn() if callable(tier_fn) else "").strip().lower()
    if tier not in _ALLOWED_TIERS:
        return None
    try:
        raw = await asyncio.wait_for(
            stream_chat_collect_async(
                agent.llm_client,
                build_extraction_prompt(user_query),
                max_tokens=_acceptance_v2_max_tokens(),
            ),
            timeout=_acceptance_v2_timeout_seconds(),
        )
    except Exception as exc:
        logger.warning(
            "[DEEP_THINK][acceptance] v2 spec extraction failed, keeping v1 heuristic: %r",
            exc,
        )
        return None
    spec = parse_acceptance_spec(raw)
    if spec is None:
        logger.warning(
            "[DEEP_THINK][acceptance] v2 spec extraction returned invalid JSON, keeping v1 heuristic"
        )
        return None
    logger.info(
        "[DEEP_THINK][acceptance] v2 spec: %s",
        "; ".join(
            f"{out.kind}x{out.min_count}({','.join(out.extensions) or 'any'})"
            for out in spec.required_outputs
        ),
    )
    return spec


def spec_to_expected_kinds(spec: Optional[AcceptanceSpec]) -> List[str]:
    if spec is None:
        return []
    kinds: List[str] = []
    for out in spec.required_outputs:
        if out.kind in _EXPECT_KIND_EXTS and out.kind not in kinds:
            kinds.append(out.kind)
    return kinds


def spec_to_kind_requirements(spec: Optional[AcceptanceSpec]) -> Optional[Dict[str, int]]:
    if spec is None:
        return None
    requirements: Dict[str, int] = {}
    for out in spec.required_outputs:
        if out.kind not in _EXPECT_KIND_EXTS:
            continue
        requirements[out.kind] = requirements.get(out.kind, 0) + out.min_count
    return requirements or None


def build_acceptance_spec_prompt_block(spec: AcceptanceSpec) -> str:
    lines = ["=== DELIVERABLE SPEC (acceptance v2) ==="]
    for out in spec.required_outputs[:_MAX_OUTPUTS]:
        parts = [f"- {out.kind} x{out.min_count}"]
        if out.extensions:
            parts.append(f"({', '.join(out.extensions)})")
        if out.in_place and out.target_path:
            parts.append(f"overwrite in place: {out.target_path}")
        elif out.target_path:
            parts.append(f"target: {out.target_path}")
        if out.constraints:
            parts.append(f"-- must satisfy: {out.constraints}")
        lines.append(" ".join(parts))
    lines.append(
        "Produce every listed item as a real file; the loop guard verifies the "
        "count of each type by extension on disk."
    )
    return "\n".join(lines)[:1500] + "\n"
