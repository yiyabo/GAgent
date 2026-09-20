"""Loop guards for the DeepThink agent (god-class split, behaviour zero-change).

Deliverable acceptance, failure-trap, and no-progress endgame logic extracted
from app.services.deep_think_agent. Each function here is the body of the
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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.deep_think.text_utils import (
    _GUARD_DELIVERABLE_EXT_RE,
    _GUARD_DIGIT_RE,
    _GUARD_PATH_NORMALIZE_RE,
    _GUARD_PRODUCTIVE_DIR_RE,
    _GUARD_SCRATCH_RE,
    _INLINE_IMAGE_EXT_RE,
    _PRODUCTIVE_SEGMENT_RE,
    _guard_json_payload,
    _missing_expectations,
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
        if not _GUARD_DELIVERABLE_EXT_RE.search(p):
            return
        if _GUARD_SCRATCH_RE.search(p) or p.startswith("/tmp/"):
            return
        if not _GUARD_PRODUCTIVE_DIR_RE.search(p):
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
    for candidate in getattr(agent, "_produced_deliverable_paths", None) or []:
        p = str(candidate or "").strip()
        if not p or not _INLINE_IMAGE_EXT_RE.search(p):
            continue
        seg = _PRODUCTIVE_SEGMENT_RE.search(p)
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


def _failure_signature_for_result(agent: "DeepThinkAgent", item: Dict[str, Any]) -> Optional[str]:
    """Stable signature for a failed tool result (tool + normalized error)."""
    payload = item.get("tool_result")
    if not isinstance(payload, dict):
        payload = _guard_json_payload(item.get("tool_result_text"))
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is not False:
        inner = payload.get("result")
        if not (isinstance(inner, dict) and inner.get("success") is False):
            return None
        payload = inner
    error = str(
        payload.get("error") or payload.get("stderr") or payload.get("summary") or ""
    ).strip()
    if not error:
        return None
    error = _GUARD_PATH_NORMALIZE_RE.sub("<path>", error)
    error = _GUARD_DIGIT_RE.sub("<n>", error)
    tool_name = str(item.get("tool_name") or "unknown").strip().lower() or "unknown"
    return f"{tool_name}:{error[:60]}"


def _loop_guard_endgame_armed(agent: "DeepThinkAgent") -> bool:
    # The no-progress endgame only arms for execute-tier work, where a
    # deliverable is expected. Research/standard tiers keep the existing
    # iteration-position nudge ladder.
    return agent._request_tier() == "execute" or agent._is_execute_task_request()


def _apply_loop_guards(
    agent: "DeepThinkAgent",
    *,
    messages: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
    iteration: int,
    guard_state: Dict[str, Any],
) -> Optional[str]:
    """Deliverable acceptance, failure-trap, and no-progress endgame.

    Returns a break reason when the loop should stop early; otherwise None.
    Healthy runs see no behaviour change — every mechanism only fires on
    pathological patterns.
    """
    verified: List[str] = guard_state["verified_deliverables"]
    failure_counts: Dict[str, int] = guard_state["failure_sig_counts"]
    failure_warned: set = guard_state["failure_sig_warned"]

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
        guard_state["last_progress_iteration"] = iteration
        logger.info(
            "[DEEP_THINK][progress] iteration=%s new_deliverables=%s verified_total=%d",
            iteration,
            ",".join(os.path.basename(p) for p in new_verified[:4]),
            len(verified),
        )

    for item in tool_results:
        signature = agent._failure_signature_for_result(item)
        if not signature:
            continue
        failure_counts[signature] = failure_counts.get(signature, 0) + 1
        count = failure_counts[signature]
        if count == _dta()._failure_signature_warn_count() and signature not in failure_warned:
            failure_warned.add(signature)
            messages.append({
                "role": "user",
                "content": (
                    f"The same failure has now occurred {count} times: {signature}. "
                    "Stop retrying this approach — change strategy, adjust parameters or tools, "
                    "or wrap up with what has been achieved so far."
                ),
            })
            logger.warning(
                "[DEEP_THINK][trap] repeated failure signature x%d at iteration=%s: %s",
                count,
                iteration,
                signature,
            )
        if count >= _dta()._failure_signature_break_count():
            trap_missing = _missing_expectations(
                guard_state.get("expected_outputs") or [], verified
            )
            if trap_missing:
                guard_state["missing_expectations"] = trap_missing
                agent._acceptance_missing = list(trap_missing)
            return (
                f"Stopped after {count} repetitions of the same failure ({signature}); "
                f"wrapping up with {len(verified)} verified deliverable(s)."
            )

    if agent._loop_guard_endgame_armed():
        missing = _missing_expectations(guard_state.get("expected_outputs") or [], verified)
        elapsed = time.monotonic() - float(guard_state["started_at"])
        if elapsed >= _dta()._time_budget_break_seconds():
            if missing:
                guard_state["missing_expectations"] = missing
                agent._acceptance_missing = list(missing)
            return (
                f"Stopped after {int(elapsed)}s of wall-clock time "
                f"(budget {_dta()._time_budget_break_seconds()}s); wrapping up with "
                f"{len(verified)} verified deliverable(s)."
            )
        if elapsed >= _dta()._time_budget_nudge_seconds() and not guard_state["time_nudge_sent"]:
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
        streak = iteration - int(guard_state["last_progress_iteration"])
        if streak >= _dta()._progress_free_nudge_streak() and not guard_state["no_progress_nudge_sent"]:
            guard_state["no_progress_nudge_sent"] = True
            if verified:
                files_list = "\n".join(f"- {p}" for p in verified[:6])
                content = (
                    f"No new deliverable has been produced in the last {streak} steps. "
                    f"The following deliverables already exist and are verified on disk:\n{files_list}\n"
                    "If these satisfy the user's request, call submit_final_answer NOW. "
                    "Continue only for a concrete missing piece — do not re-probe or re-verify "
                    "files that already exist."
                )
            else:
                content = (
                    f"You have run {streak} steps without producing any deliverable. "
                    "Stop probing and change strategy: produce the requested output now, "
                    "or call submit_final_answer describing the concrete blocker."
                )
            messages.append({"role": "user", "content": content})
            logger.warning(
                "[DEEP_THINK][endgame] no-progress nudge at iteration=%s streak=%s verified=%d",
                iteration,
                streak,
                len(verified),
            )
        if streak >= _dta()._progress_free_break_streak():
            if missing and not guard_state.get("acceptance_extend_used"):
                # Declarative acceptance: the run has not delivered what was
                # asked for — grant one short extension instead of breaking.
                guard_state["acceptance_extend_used"] = True
                guard_state["last_progress_iteration"] = iteration - (
                    _dta()._progress_free_break_streak() - 4
                )
                labels = ", ".join(missing)
                messages.append({
                    "role": "user",
                    "content": (
                        f"The run is NOT complete: required deliverable type(s) still missing: {labels}. "
                        "Produce them NOW as real files under deliverables/ or results/ — "
                        "do not re-probe, do not describe them in text only. "
                        "If (and only if) producing them is genuinely impossible, call "
                        "submit_final_answer and state explicitly which deliverable is missing and why."
                    ),
                })
                logger.warning(
                    "[DEEP_THINK][acceptance] iteration=%s missing=%s -> extension granted",
                    iteration,
                    labels,
                )
                return None
            if missing:
                guard_state["missing_expectations"] = missing
                agent._acceptance_missing = list(missing)
                logger.warning(
                    "[DEEP_THINK][acceptance] iteration=%s missing=%s -> break with gaps",
                    iteration,
                    ",".join(missing),
                )
            return (
                f"Stopped after {streak} steps without new deliverables; "
                f"wrapping up with {len(verified)} verified deliverable(s)."
            )

    logger.info(
        "[DEEP_THINK][iter] iteration=%s tier=%s verified=%d failure_sigs=%d last_progress=%s elapsed=%ss",
        iteration,
        agent._request_tier() or "-",
        len(verified),
        len(failure_counts),
        guard_state["last_progress_iteration"],
        int(time.monotonic() - float(guard_state["started_at"])),
    )
    return None
