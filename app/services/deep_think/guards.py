"""Loop guards for the DeepThink agent (god-class split, behaviour zero-change).

Deliverable tracking and the standalone wall-clock endgame extracted from
app.services.deep_think_agent. Each function here is the body of the
like-named DeepThinkAgent method with `self` renamed to `agent`; the class
keeps thin wrappers so call sites (and subclass overrides) are unaffected.

Env-knob helpers (_time_budget_*_seconds etc.) are resolved through the
deep_think_agent module namespace at call time: tests monkeypatch them there,
and a direct import would freeze the binding.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.deep_think.text_utils import (
    _GUARD_DELIVERABLE_EXT_RE,
    _GUARD_PRODUCTIVE_DIR_RE,
    _GUARD_SCRATCH_RE,
    _INLINE_IMAGE_EXT_RE,
    _INLINE_IMAGE_PRODUCTIVE_RE,
    _INLINE_IMAGE_RAW_TMP_RE,
    _guard_json_payload,
    _missing_expectations,
    _missing_expectations_detailed,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly knob lookup)."""
    from app.services import deep_think_agent

    return deep_think_agent


def _extract_guard_candidates(agent: "DeepThinkAgent", tool_results: List[Dict[str, Any]]) -> List[str]:
    """Harvest deliverable-looking output paths from a tool cycle.

    Only paths under deliverables/ or results/ count — probe files in
    /tmp, workspaces, tool_outputs etc. are scratch, not progress.
    """
    candidates: List[str] = []
    list_keys = ("artifact_paths", "produced_files", "output_files", "session_artifact_paths", "artifacts")
    str_keys = ("file_path", "image_path", "output_path", "saved_path")

    def _push(value: Any) -> None:
        if not isinstance(value, str):
            return
        p = value.strip()
        if not p or ".." in p or "\\" in p:
            return
        from app.services.plans.output_spec import KIND_EXTENSIONS
        spec = getattr(agent, "_acceptance_spec", None)
        declared_match = bool(spec and any(
            not (output.extensions or KIND_EXTENSIONS.get(output.kind))
            or any(Path(p).name.lower().endswith(ext) for ext in (output.extensions or KIND_EXTENSIONS.get(output.kind, ())))
            for output in spec.required_outputs
        ))
        if spec and spec.acceptance_criteria and not declared_match:
            from app.services.plans.task_metadata_generator import is_inferred_task_spec
            criteria = spec.acceptance_criteria
            if not is_inferred_task_spec(criteria):
                declared_match = any(
                    isinstance(check, dict) and Path(str(check.get("path") or "")).suffix.lower() == Path(p).suffix.lower()
                    for check in criteria.get("checks") or []
                )
        if not declared_match and not _GUARD_DELIVERABLE_EXT_RE.search(p):
            return
        if not declared_match and (_GUARD_SCRATCH_RE.search(p) or p.startswith("/tmp/")):
            return
        if not declared_match and not _GUARD_PRODUCTIVE_DIR_RE.search(p):
            return
        if p not in candidates:
            candidates.append(p)

    def _harvest(payload: Dict[str, Any], readonly: bool = False) -> None:
        keys = ("produced_files", "output_files") if readonly else list_keys
        for key in keys:
            values = payload.get(key)
            if isinstance(values, (list, tuple)):
                for entry in values:
                    if isinstance(entry, str):
                        _push(entry)
                    elif isinstance(entry, dict):
                        _push(entry.get("path") or entry.get("file_path"))
        for key in (("output_path", "saved_path") if readonly else str_keys):
            _push(payload.get(key))
        if readonly:
            return
        # file_operations reports the file it wrote or copied as ``path``/``destination``, and
        # deliverable_submit lists what it published; neither uses the keys above, so a deliverable made
        # (or published) with them was invisible to acceptance.
        operation = str(payload.get("operation") or "").lower()
        if operation in {"copy", "move"}:
            _push(payload.get("destination"))
        elif operation == "write":
            _push(payload.get("path"))
        submitted = payload.get("deliverable_submit")
        if isinstance(submitted, dict):
            for artifact in submitted.get("artifacts") or []:
                if isinstance(artifact, dict):
                    _push(artifact.get("path"))

    for item in tool_results:
        payload = item.get("tool_result")
        if not isinstance(payload, dict):
            payload = _guard_json_payload(item.get("tool_result_text"))
        if not isinstance(payload, dict) or payload.get("success") is False:
            continue
        from app.services.plans.output_spec import is_readonly_output_probe
        readonly = is_readonly_output_probe(item.get("tool_name"), item.get("tool_params", {}))
        _harvest(payload, readonly)
        inner = payload.get("result")
        if isinstance(inner, dict):
            _harvest(inner, readonly)
    return candidates


def _extract_inline_image_candidates(agent: "DeepThinkAgent", tool_results: List[Dict[str, Any]]) -> List[str]:
    """Harvest produced image paths for inline rendering (guard-independent).

    Same key set and traversal as ``_extract_guard_candidates``, but widened
    to where pi/code_executor figures actually land (``figures/``,
    ``raw_files/tmp/<run>/``, ``deliverables/latest/image_tabular/``).
    ``raw_files/tmp/`` counts as productive for images even though
    ``raw_files`` is scratch for deliverable progress. This feeds only the
    inline-image mirror; it never touches deliverable progress state.
    """
    candidates: List[str] = []
    list_keys = ("artifact_paths", "produced_files", "output_files", "session_artifact_paths", "artifacts")
    str_keys = ("file_path", "image_path", "output_path", "saved_path")

    def _push(value: Any) -> None:
        if not isinstance(value, str):
            return
        p = value.strip()
        if not p or ".." in p or "\\" in p:
            return
        if not _INLINE_IMAGE_EXT_RE.search(p):
            return
        if p.startswith("/tmp/"):
            return
        if _GUARD_SCRATCH_RE.search(p) and not _INLINE_IMAGE_RAW_TMP_RE.search(p):
            return
        if not _INLINE_IMAGE_PRODUCTIVE_RE.search(p):
            return
        if p not in candidates:
            candidates.append(p)

    def _harvest(payload: Dict[str, Any]) -> None:
        for key in list_keys:
            values = payload.get(key)
            if isinstance(values, (list, tuple)):
                for entry in values:
                    if isinstance(entry, str):
                        _push(entry)
                    elif isinstance(entry, dict):
                        _push(entry.get("path") or entry.get("file_path"))
        for key in str_keys:
            _push(payload.get(key))

    for item in tool_results:
        payload = item.get("tool_result")
        if not isinstance(payload, dict):
            payload = _guard_json_payload(item.get("tool_result_text"))
        if not isinstance(payload, dict) or payload.get("success") is False:
            continue
        _harvest(payload)
        inner = payload.get("result")
        if isinstance(inner, dict):
            _harvest(inner)
    return candidates


def _verify_guard_path(agent: "DeepThinkAgent", candidate: str) -> Optional[str]:
    """Return the on-disk path when the candidate exists and is non-empty."""
    p = candidate.strip()
    if not p:
        return None
    attempts: List[str] = []
    if os.path.isabs(p):
        attempts.append(p)
    else:
        base_dir = getattr(agent, "_acceptance_base_dir", None)
        if base_dir:
            attempts.append(os.path.join(str(base_dir), p))
        runtime_root = str(os.getenv("APP_RUNTIME_ROOT") or "/app/runtime").strip()
        session_id = str(agent.request_profile.get("session_id") or "").strip()
        if session_id:
            attempts.append(os.path.join(runtime_root, session_id, p))
        attempts.append(os.path.join(runtime_root, p))
    for path in attempts:
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
        except OSError:
            continue
    return None


def _collect_inline_image_relpaths(agent: "DeepThinkAgent", limit: int = 8) -> List[str]:
    """Session-relative paths of image deliverables produced in this run.

    Fed by the loop-guard mirror of verified deliverables; only images
    that verify on disk under the session's runtime dir are returned, so
    the frontend /file endpoint can actually serve them.
    """
    runtime_root = str(os.getenv("APP_RUNTIME_ROOT") or "/app/runtime").strip()
    session_id = str(agent.request_profile.get("session_id") or "").strip()
    collected: List[str] = []
    sources = (
        getattr(agent, "_produced_image_paths", None)
        or getattr(agent, "_produced_deliverable_paths", None)
        or []
    )
    for candidate in sources:
        p = str(candidate or "").strip()
        if not p or not _INLINE_IMAGE_EXT_RE.search(p):
            continue
        seg = _INLINE_IMAGE_PRODUCTIVE_RE.search(p)
        if not seg:
            continue
        rel = p[seg.start() :].lstrip("/")
        if not rel or rel in collected:
            continue
        if session_id:
            on_disk = os.path.join(runtime_root, session_id, rel)
            try:
                if not (os.path.isfile(on_disk) and os.path.getsize(on_disk) > 0):
                    continue
            except OSError:
                continue
        collected.append(rel)
        if len(collected) >= limit:
            break
    return collected


def _loop_guard_endgame_armed(agent: "DeepThinkAgent") -> bool:
    # The standalone wall-clock endgame only arms for execute-intent work,
    # where a deliverable is expected; plain chat turns exit via the normal
    # submit/early-stop paths.
    return agent._is_execute_task_request()


def _spec_kind_requirements(guard_state: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Count requirements from an acceptance-v2 spec, None when absent (v1)."""
    spec = guard_state.get("acceptance_spec")
    if spec is None:
        return None
    from app.services.deep_think.acceptance import spec_to_kind_requirements

    return spec_to_kind_requirements(spec)


def _apply_loop_guards(
    agent: "DeepThinkAgent",
    *,
    messages: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
    iteration: int,
    guard_state: Dict[str, Any],
) -> Optional[str]:
    """Track verified deliverables and inline images; enforce the standalone
    wall-clock endgame.

    Returns a break reason when the loop should stop early; otherwise None.
    Repeated-failure and no-progress breakers used to live here. They were
    removed (Hermes has neither): a run is bounded by the iteration cap and the
    shared run budget, and the model reads its own tool errors. Tracebacks of
    five different bugs collapsed to one "failure signature" and cut a healthy
    run off at step 32.
    """
    verified: List[str] = guard_state["verified_deliverables"]

    new_verified: List[str] = []
    for candidate in agent._extract_guard_candidates(tool_results):
        if candidate in verified or candidate in new_verified:
            continue
        confirmed = agent._verify_guard_path(candidate)
        if confirmed and confirmed not in verified and confirmed not in new_verified:
            new_verified.append(confirmed)
    if new_verified:
        verified.extend(new_verified)
        produced = getattr(agent, "_produced_deliverable_paths", None)
        if produced is None:
            produced = []
            agent._produced_deliverable_paths = produced
        for p in new_verified:
            if p not in produced:
                produced.append(p)
        logger.info(
            "[DEEP_THINK][progress] iteration=%s new_deliverables=%s verified_total=%d",
            iteration,
            ",".join(os.path.basename(p) for p in new_verified[:4]),
            len(verified),
        )

    # Image-only mirror for inline rendering: widened, disk-verified collection
    # that runs alongside (never inside) the deliverable-progress logic above.
    new_images: List[str] = []
    for candidate in _extract_inline_image_candidates(agent, tool_results):
        confirmed = agent._verify_guard_path(candidate)
        if confirmed and confirmed not in new_images:
            new_images.append(confirmed)
    if new_images:
        produced_images = getattr(agent, "_produced_image_paths", None)
        if produced_images is None:
            produced_images = []
            agent._produced_image_paths = produced_images
        for p in new_images:
            if p not in produced_images:
                produced_images.append(p)
        logger.debug(
            "[DEEP_THINK][inline-images] iteration=%s new_images=%s total=%d",
            iteration,
            ",".join(os.path.basename(p) for p in new_images[:4]),
            len(produced_images),
        )

    if agent._loop_guard_endgame_armed():
        from .acceptance import acceptance_missing
        missing = acceptance_missing(
            agent,
            guard_state.get("expected_outputs") or [],
            verified,
            guard_state.get("acceptance_spec"),
        )
        elapsed = time.monotonic() - float(guard_state["started_at"])
        from app.services.run_budget import current_run_budget
        shared_budget = current_run_budget()
        if shared_budget is None and elapsed >= _dta()._time_budget_break_seconds():
            agent._termination_reason = "wall_clock_limit"
            if missing:
                guard_state["missing_expectations"] = missing
                agent._acceptance_missing = list(missing)
            return (
                f"Stopped after {int(elapsed)}s of wall-clock time "
                f"(budget {_dta()._time_budget_break_seconds()}s); wrapping up with "
                f"{len(verified)} verified deliverable(s)."
            )
        if shared_budget is None and elapsed >= _dta()._time_budget_nudge_seconds() and not guard_state["time_nudge_sent"]:
            guard_state["time_nudge_sent"] = True
            if verified:
                files_list = "\n".join(f"- {p}" for p in verified[:6])
                content = (
                    f"The run has used {int(elapsed)}s of wall-clock time. "
                    f"The following deliverables already exist and are verified on disk:\n{files_list}\n"
                    "Wrap up NOW: call submit_final_answer with what has been achieved. "
                    "Do NOT start another heavy tool run."
                )
            else:
                content = (
                    f"The run has used {int(elapsed)}s of wall-clock time. "
                    "Wrap up NOW: call submit_final_answer with the best available evidence. "
                    "Do NOT start another heavy tool run."
                )
            messages.append({"role": "user", "content": content})
            logger.warning(
                "[DEEP_THINK][endgame] time-budget nudge at iteration=%s elapsed=%ss",
                iteration,
                int(elapsed),
            )

    logger.info(
        "[DEEP_THINK][iter] iteration=%s tier=%s verified=%d elapsed=%ss",
        iteration,
        agent._request_tier() or "-",
        len(verified),
        int(time.monotonic() - float(guard_state["started_at"])),
    )
    return None
