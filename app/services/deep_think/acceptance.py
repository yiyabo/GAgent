"""DeepThink adapter for the shared versioned output contract.

Supplied structured declarations bypass extraction. Optional LLM extraction is
restricted to execution/research turns with concrete file intent, independent
of the legacy kind regex. Verification is deterministic; textual constraints
remain prompt guidance and are reported as unchecked.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from app.llm import stream_chat_collect_async
from app.services.run_budget import RunDeadlineExceeded, run_stage
from app.services.deep_think.text_utils import (
    _EXPECT_KIND_EXTS,
    _derive_expected_outputs,
    _acceptance_v2_enabled,
    _acceptance_v2_max_tokens,
    _acceptance_v2_timeout_seconds,
)

from pathlib import Path
from app.services.plans.output_spec import (
    OutputSpec,
    RequiredOutput as RequiredOutput,
    parse_output_spec,
    output_spec_from_metadata,
    capture_output_inputs,
    validate_output_spec,
    accepted_output_paths,
    session_output_origin_map,
)

AcceptanceSpec = OutputSpec

logger = logging.getLogger(__name__)

_MAX_OUTPUTS = 64
_MAX_QUERY_CHARS = 4000
_ALLOWED_TIERS = {"execute", "research"}


def parse_acceptance_spec(raw: Any) -> Optional[AcceptanceSpec]:
    spec = parse_output_spec(raw)
    return spec if spec is not None and spec.required_outputs else None


def build_extraction_prompt(user_query: str) -> str:
    query = str(user_query or "").strip()[:_MAX_QUERY_CHARS]
    return (
        "Extract the deliverable specification from the user request below as STRICT JSON only.\n"
        'Schema: {"required_outputs": [{"kind": "image|data|document|other", '
        '"min_count": <int 1-10000>, "extensions": [".png", ".md", ...], '
        '"constraints": "short content requirement", '
        '"target_path": "relative/path or empty", "in_place": true|false}]}\n'
        "Rules: only include outputs the user explicitly requested as files/deliverables; "
        "use in_place=true when the user asks to modify or overwrite an existing file; "
        "preserve the requested count and every exact target path; never compress a large count to fit the schema; "
        "at most 64 output declarations, 4096 characters per target path, and 8000 per constraint; "
        "no prose, no markdown fence, JSON object only.\n"
        f"User request:\n{query}\n"
    )


async def extract_acceptance_spec(
    agent: Any, user_query: str
) -> Optional[AcceptanceSpec]:
    if not _acceptance_v2_enabled():
        return None
    tier_fn = getattr(agent, "_request_tier", None)
    tier = str(tier_fn() if callable(tier_fn) else "").strip().lower()
    intent_fn = getattr(agent, "_is_execute_task_request", None)
    # Flat-tier mode pins the tier label to "standard", so execute-task intent
    # also arms acceptance v2 (in legacy mode tier=execute covered it).
    if tier not in _ALLOWED_TIERS and not (callable(intent_fn) and intent_fn()):
        return None
    if not _has_file_deliverable_intent(user_query):
        return None
    try:
        raw = await run_stage(
            stream_chat_collect_async(
                agent.llm_client,
                build_extraction_prompt(user_query),
                max_tokens=_acceptance_v2_max_tokens(),
            ),
            timeout=_acceptance_v2_timeout_seconds(),
            stage="acceptance specification extraction",
        )
    except RunDeadlineExceeded:
        raise
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


def spec_to_kind_requirements(
    spec: Optional[AcceptanceSpec],
) -> Optional[Dict[str, int]]:
    if spec is None:
        return None
    requirements: Dict[str, int] = {}
    for out in spec.required_outputs:
        if out.kind not in _EXPECT_KIND_EXTS:
            continue
        requirements[out.kind] = requirements.get(out.kind, 0) + out.min_count
    return requirements or None


def build_acceptance_spec_prompt_block(
    spec: AcceptanceSpec, base_dir: Optional[str] = None
) -> str:
    lines = ["=== DELIVERABLE SPEC (acceptance v2) ==="]
    if base_dir:
        lines.append(
            f"Output base directory: {base_dir}. Resolve relative target paths here; preserve absolute targets exactly."
        )
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
        "declared formats, exact target paths, distinct file counts, and changes "
        "to in-place targets against their pre-execution snapshots."
    )
    return "\n".join(lines) + "\n"


def _has_file_deliverable_intent(query: str) -> bool:
    if _derive_expected_outputs(query):
        return True
    return bool(
        re.search(
            r"(?:create|generate|write|save|export|modify|overwrite|update|生成|创建|写入|保存|导出|修改|覆盖|更新)",
            query,
            re.I,
        )
        and re.search(
            r"\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*(?:\b|[\s`'\"]|$)", query, re.I
        )
    )


async def prepare_acceptance_spec(
    agent: Any,
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    task_context: Any = None,
) -> Optional[AcceptanceSpec]:
    """Use an existing declaration before any optional extraction call."""
    context = context if isinstance(context, dict) else {}
    spec = output_spec_from_metadata(context)
    if spec is None:
        provided = getattr(task_context, "output_spec", None)
        spec = (
            parse_output_spec(provided, strict=True) if provided is not None else None
        )
    if spec is None:
        spec = output_spec_from_metadata(getattr(agent, "request_profile", {}))
    if spec is None:
        spec = await extract_acceptance_spec(agent, user_query)
    base_dir = (
        context.get("output_spec_base_dir")
        or context.get("working_directory")
        or context.get("task_directory_full")
    )
    if not base_dir:
        profile = getattr(agent, "request_profile", {}) or {}
        from app.services.session_paths import get_runtime_session_dir, get_runtime_root

        session_id = str(context.get("session_id") or profile.get("session_id") or "")
        base_dir = (
            get_runtime_session_dir(session_id) if session_id else get_runtime_root()
        )
    agent._acceptance_base_dir = str(Path(base_dir).resolve())
    snapshot = context.get("output_input_snapshot")
    agent._output_input_snapshot = (
        snapshot
        if isinstance(snapshot, dict)
        else await run_stage(
            asyncio.to_thread(capture_output_inputs, spec, base_dir),
            stage="capture output input snapshot",
        )
    )
    agent._acceptance_spec = spec
    agent._acceptance_task_id = (
        getattr(task_context, "task_id", None)
        or context.get("task_id")
        or (getattr(agent, "request_profile", {}) or {}).get("task_id")
    )
    agent._acceptance_manifest = (
        context.get("_artifact_manifest") or context.get("artifact_manifest") or {}
    )
    profile = getattr(agent, "request_profile", {}) or {}
    agent._acceptance_session_id = context.get("session_id") or profile.get(
        "session_id"
    )
    agent._output_verification = None
    if spec is not None:
        context["output_spec"] = spec.to_dict()
        context["output_input_snapshot"] = agent._output_input_snapshot
        context["output_spec_base_dir"] = agent._acceptance_base_dir
    return spec


def acceptance_missing(
    agent: Any,
    expected: List[str],
    verified_paths: List[str],
    spec: Optional[AcceptanceSpec] = None,
) -> List[str]:
    from app.services.deep_think.text_utils import _missing_expectations_detailed

    spec = spec or getattr(agent, "_acceptance_spec", None)
    if spec is None:
        return _missing_expectations_detailed(expected, verified_paths, None)
    report = validate_output_spec(
        spec,
        [
            *verified_paths,
            *accepted_output_paths(
                getattr(agent, "_acceptance_manifest", None),
                getattr(agent, "_acceptance_task_id", None),
                spec,
                getattr(agent, "_acceptance_base_dir", "."),
                verified_paths,
            ),
        ],
        base_dir=getattr(agent, "_acceptance_base_dir", "."),
        input_snapshot=getattr(agent, "_output_input_snapshot", None),
        artifact_manifest=getattr(agent, "_acceptance_manifest", None),
        origin_map=session_output_origin_map(
            getattr(agent, "_acceptance_session_id", None),
            getattr(agent, "_acceptance_manifest", None),
        ),
    )
    missing = []
    if spec.authoritative:
        for failure in report["failures"]:
            output = spec.required_outputs[failure["output_index"]]
            shortfall = (
                output.min_count - report["matched_counts"][failure["output_index"]]
            )
            label = output.target_path or (
                f"{output.kind}x{shortfall}" if output.min_count > 1 else output.kind
            )
            if label not in missing:
                missing.append(label)
    if spec.acceptance_criteria:
        from app.services.plans.acceptance_criteria import (
            strengthen_acceptance_criteria,
        )
        from app.services.plans.task_metadata_generator import is_inferred_task_spec
        from app.services.plans.task_verification import TaskVerificationService
        import copy

        criteria = strengthen_acceptance_criteria(
            copy.deepcopy(spec.acceptance_criteria)
        )
        verifier = TaskVerificationService()
        for check in criteria.get("checks") or []:
            outcome = verifier._run_check(
                check,
                base_dir=Path(getattr(agent, "_acceptance_base_dir", ".")),
                artifact_paths=report["artifact_paths"],
            )
            if outcome and not outcome.get("success"):
                report["failures"].append(outcome)
                strict = bool(criteria.get("blocking", True)) and (
                    not is_inferred_task_spec(criteria) or bool(check.get("hard"))
                )
                if strict:
                    report["authoritative"] = True
                    label = str(outcome.get("path") or outcome.get("type"))
                    if label not in missing:
                        missing.append(label)
    report["status"] = "failed" if report["failures"] else "passed"
    agent._output_verification = report
    return missing
