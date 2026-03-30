"""Bounded verification for local_mutation + terminal_session.write (exit marker + side effects)."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from tool_box import execute_tool

from .subject_identity import canonicalize_subject_ref

logger = logging.getLogger(__name__)

_EXIT_MARKER_RE = re.compile(r"__CODEX_EXIT__(\d+)__(-?\d+)")
_FAILURE_SNIPPETS = (
    "command not found",
    "cannot open zipfile",
    "cannot find",
    "no such file or directory",
    "fatal error",
    "permission denied",
    "unzip:  cannot find",
    "unzip: cannot find",
)
_MAX_REPLAY_POLLS = 18
_REPLAY_POLL_INTERVAL_S = 0.35


def wrap_local_mutation_command(data: str, marker_id: int) -> str:
    """Append a shell line that prints a unique exit-code marker (system-injected)."""
    raw = str(data or "")
    if not raw.endswith("\n"):
        raw = raw + "\n"
    return raw + f"printf '__CODEX_EXIT__{int(marker_id)}__%s\\n' \"$?\"\n"


async def snapshot_active_subject_fs(agent: Any) -> Optional[Dict[str, Any]]:
    """Best-effort listing/exists for the current active_subject (subject-scoped verification)."""
    extra = getattr(agent, "extra_context", {}) or {}
    subj = extra.get("active_subject")
    if not isinstance(subj, dict):
        return None
    kind = str(subj.get("kind") or "workspace").strip().lower()
    ref = canonicalize_subject_ref(subj.get("canonical_ref") or subj.get("display_ref"))
    if not ref:
        return None
    try:
        if kind in {"directory", "workspace"}:
            r = await execute_tool("file_operations", operation="list", path=ref)
            names: set[str] = set()
            if isinstance(r, dict) and r.get("success"):
                for it in r.get("items") or []:
                    if isinstance(it, dict):
                        n = it.get("name")
                        if isinstance(n, str) and n.strip():
                            names.add(n.strip())
                    elif isinstance(it, str) and it.strip():
                        names.add(it.strip())
            return {"kind": "dir", "path": ref, "names": names, "raw": r}
        if kind == "file":
            r = await execute_tool("file_operations", operation="exists", path=ref)
            exists = bool(isinstance(r, dict) and r.get("exists"))
            return {"kind": "file", "path": ref, "exists": exists, "raw": r}
    except Exception as exc:
        logger.debug("snapshot_active_subject_fs failed: %s", exc)
    return None


def _replay_entries_to_text(replay: Any) -> str:
    if not isinstance(replay, list):
        return ""
    parts: List[str] = []
    for item in replay:
        if not isinstance(item, dict):
            continue
        b64 = item.get("data") or ""
        if not isinstance(b64, str) or not b64.strip():
            continue
        try:
            raw = base64.b64decode(b64)
            parts.append(raw.decode("utf-8", errors="replace"))
        except Exception:
            continue
    return "".join(parts)


def infer_mutation_kind(command: str) -> str:
    c = command.lower()
    if re.search(r"\b(unzip|bsdtar|7z\s+x|tar\s+.*-[jx]vf?)\b", c):
        return "extract"
    if re.search(r"(?:^|\s)rm(?:\s+|$)", c):
        return "delete"
    if re.search(r"(?:^|\s)cp(?:\s+|$)", c):
        return "copy"
    if re.search(r"(?:^|\s)mv(?:\s+|$)", c):
        return "move"
    return "unknown"


def extract_path_tokens(command: str) -> List[str]:
    """Pull quoted and obvious path-like tokens for light-weight checks."""
    out: List[str] = []
    for m in re.finditer(r"'([^']+)'|\"([^\"]+)\"", command):
        g = m.group(1) or m.group(2) or ""
        g = str(g).strip()
        if g and ("/" in g or g.startswith(".")):
            out.append(g)
    for m in re.finditer(r"(?:^|\s)(/[A-Za-z0-9_./\-]+)", command):
        p = m.group(1).strip()
        if len(p) > 1 and p.count("/") >= 1:
            out.append(p)
    dedup: List[str] = []
    seen = set()
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


async def _path_exists(path: str) -> bool:
    r = await execute_tool("file_operations", operation="exists", path=canonicalize_subject_ref(path))
    return bool(isinstance(r, dict) and r.get("exists"))


def _extract_side_effects_ok(
    *,
    pre: Optional[Dict[str, Any]],
    post: Optional[Dict[str, Any]],
) -> Tuple[bool, str]:
    """Directory-listing based checks (sync)."""
    if pre is None or post is None:
        return False, "missing_snapshot"
    pk = pre.get("kind")
    if pk != post.get("kind"):
        return False, "subject_kind_changed"
    if pk != "dir":
        return False, "extract_non_dir_subject"
    pre_names = pre.get("names") if isinstance(pre.get("names"), set) else set()
    post_names = post.get("names") if isinstance(post.get("names"), set) else set()
    if post_names > pre_names or post_names != pre_names:
        return True, "dir_listing_changed"
    return False, "extract_no_dir_change"


async def _async_verify_paths(mut: str, command: str) -> Tuple[bool, str]:
    """Path existence checks after exit code 0."""
    toks = extract_path_tokens(command)
    if mut == "delete":
        if not toks:
            return False, "delete_paths_unknown"
        for t in toks:
            if await _path_exists(t):
                return False, f"still_exists:{t}"
        return True, "targets_absent"

    if mut == "copy":
        if len(toks) < 2:
            return False, "copy_paths_incomplete"
        dest = toks[-1]
        if await _path_exists(dest):
            return True, "dest_exists"
        return False, "dest_missing"

    if mut == "move":
        if len(toks) < 2:
            return False, "move_paths_incomplete"
        src, dest = toks[-2], toks[-1]
        src_gone = not await _path_exists(src)
        dest_there = await _path_exists(dest)
        if src_gone and dest_there:
            return True, "src_gone_dest_present"
        if not src_gone and dest_there:
            return False, "src_still_present"
        return False, "move_inconclusive"

    return False, "not_path_mutation"


def _failure_hint_in_output(text: str) -> bool:
    low = text.lower()
    return any(s in low for s in _FAILURE_SNIPPETS)


async def verify_local_mutation_terminal_write(
    agent: Any,
    *,
    sanitized: Dict[str, Any],
    params: Dict[str, Any],
    pre_snapshot: Optional[Dict[str, Any]],
    marker_id: int,
    original_command: str,
) -> Dict[str, Any]:
    """Augment terminal_session write result with verification_* fields."""
    out = dict(sanitized)
    terminal_id = str(out.get("terminal_id") or params.get("terminal_id") or "").strip()
    if not terminal_id:
        out["command_state"] = "unverified"
        out["verification_state"] = "unverified"
        out["exit_code"] = None
        out["verification_summary"] = "command dispatched; verification pending (missing terminal_id)"
        out["verification_evidence"] = {"stage": "no_terminal_id"}
        return out

    combined = str(out.get("output") or "")
    last_code: Optional[int] = None
    poll = 0
    job = None
    try:
        from app.services.plans.decomposition_jobs import get_current_job, plan_decomposition_jobs

        job = get_current_job()
    except Exception:
        job = None

    while poll < _MAX_REPLAY_POLLS:
        try:
            rep = await execute_tool(
                "terminal_session",
                operation="replay",
                terminal_id=terminal_id,
                limit=1200,
            )
            replay_list = rep.get("replay") if isinstance(rep, dict) else None
            text = _replay_entries_to_text(replay_list)
            blob = combined + "\n" + text
            for m in _EXIT_MARKER_RE.finditer(blob):
                mid = int(m.group(1))
                code = int(m.group(2))
                if mid == marker_id:
                    last_code = code
                    break
            if last_code is not None:
                break
        except Exception as exc:
            logger.debug("terminal replay poll failed: %s", exc)
        poll += 1
        if job is not None:
            try:
                pct = int(min(99, (poll / max(1, _MAX_REPLAY_POLLS)) * 100))
                plan_decomposition_jobs.update_stats_from_context(
                    {
                        "tool_progress": {
                            "tool": "terminal_session",
                            "terminal_id": terminal_id,
                            "percent": pct,
                            "status": "verifying",
                            "phase": "mutation_replay",
                        }
                    }
                )
            except Exception:
                pass
        await asyncio.sleep(_REPLAY_POLL_INTERVAL_S)

    exit_code = last_code
    blob_full = combined
    try:
        rep2 = await execute_tool(
            "terminal_session", operation="replay", terminal_id=terminal_id, limit=1200
        )
        replay_list = rep2.get("replay") if isinstance(rep2, dict) else None
        blob_full = combined + "\n" + _replay_entries_to_text(replay_list)
    except Exception:
        pass

    if exit_code is None:
        if _failure_hint_in_output(blob_full):
            out["command_state"] = "unverified"
            out["verification_state"] = "verified_failure"
            out["exit_code"] = None
            out["verification_summary"] = "command failed verification: shell output suggests failure (no exit marker)"
            out["verification_evidence"] = {"stage": "replay_error_hint", "marker_id": marker_id}
            return out
        out["command_state"] = "unverified"
        out["verification_state"] = "unverified"
        out["exit_code"] = None
        out["verification_summary"] = "command dispatched; verification pending (exit marker not observed)"
        out["verification_evidence"] = {"stage": "no_marker", "marker_id": marker_id}
        return out

    if exit_code != 0:
        out["command_state"] = "verified_failure"
        out["verification_state"] = "verified_failure"
        out["exit_code"] = exit_code
        out["verification_summary"] = f"command failed verification: exit code {exit_code}"
        out["verification_evidence"] = {"stage": "exit_nonzero", "marker_id": marker_id}
        return out

    mut = infer_mutation_kind(original_command)
    post_snapshot = await snapshot_active_subject_fs(agent)

    if mut in {"delete", "copy", "move"}:
        ok, reason = await _async_verify_paths(mut, original_command)
        if ok:
            out["command_state"] = "verified_success"
            out["verification_state"] = "verified_success"
            out["exit_code"] = 0
            out["verification_summary"] = f"command verified successfully ({reason})"
            out["verification_evidence"] = {"stage": "path_checks", "reason": reason, "marker_id": marker_id}
            return out
        if _failure_hint_in_output(blob_full):
            out["command_state"] = "verified_failure"
            out["verification_state"] = "verified_failure"
            out["exit_code"] = 0
            out["verification_summary"] = "command failed verification: output suggests error despite exit 0"
            out["verification_evidence"] = {"stage": "stderr_hint_exit0", "path_reason": reason}
            return out
        out["command_state"] = "unverified"
        out["verification_state"] = "unverified"
        out["exit_code"] = 0
        out["verification_summary"] = (
            f"command dispatched; verification pending (exit 0 but paths not confirmed: {reason})"
        )
        out["verification_evidence"] = {"stage": "path_mismatch", "reason": reason}
        return out

    if mut == "extract":
        ok, reason = _extract_side_effects_ok(pre=pre_snapshot, post=post_snapshot)
        if ok:
            out["command_state"] = "verified_success"
            out["verification_state"] = "verified_success"
            out["exit_code"] = 0
            out["verification_summary"] = f"command verified successfully ({reason})"
            out["verification_evidence"] = {"stage": "dir_snapshot", "reason": reason, "marker_id": marker_id}
            return out
        if _failure_hint_in_output(blob_full):
            out["command_state"] = "verified_failure"
            out["verification_state"] = "verified_failure"
            out["exit_code"] = 0
            out["verification_summary"] = "command failed verification: output suggests error despite exit 0"
            out["verification_evidence"] = {"stage": "stderr_hint_exit0", "side_effect": reason}
            return out
        out["command_state"] = "unverified"
        out["verification_state"] = "unverified"
        out["exit_code"] = 0
        out["verification_summary"] = (
            f"command dispatched; verification pending (exit 0 but directory change not confirmed: {reason})"
        )
        out["verification_evidence"] = {"stage": "extract_unverified", "reason": reason}
        return out

    if _failure_hint_in_output(blob_full):
        out["command_state"] = "verified_failure"
        out["verification_state"] = "verified_failure"
        out["exit_code"] = 0
        out["verification_summary"] = "command failed verification: output suggests error despite exit 0"
        out["verification_evidence"] = {"stage": "stderr_hint_exit0", "kind": mut}
        return out

    out["command_state"] = "unverified"
    out["verification_state"] = "unverified"
    out["exit_code"] = 0
    out["verification_summary"] = (
        "command dispatched; verification pending (exit 0; mutation kind unknown or not verified)"
    )
    out["verification_evidence"] = {"stage": "unknown_kind", "kind": mut}
    return out


async def prepare_local_mutation_terminal_write(
    agent: Any, tool_name: str, params: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[int], Optional[str]]:
    """Optionally wrap write payload and capture pre-snapshot for local_mutation."""
    if tool_name != "terminal_session":
        return params, None, None, None
    if str(params.get("operation") or "").strip().lower() != "write":
        return params, None, None, None
    intent = str((getattr(agent, "extra_context", {}) or {}).get("intent_type") or "").strip().lower()
    if intent != "local_mutation":
        return params, None, None, None
    enc = str(params.get("encoding") or "utf-8").strip().lower()
    if enc == "base64":
        # Cannot safely inject exit-marker suffix into opaque base64 payloads.
        return params, None, None, None
    data = params.get("data")
    if not isinstance(data, str) or not data.strip():
        return params, None, None, None
    ctx = dict(getattr(agent, "extra_context", {}) or {})
    ctx["_mutation_marker_seq"] = int(ctx.get("_mutation_marker_seq") or 0) + 1
    marker_id = int(ctx["_mutation_marker_seq"])
    agent.extra_context = ctx
    pre = await snapshot_active_subject_fs(agent)
    original = data
    new_params = dict(params)
    new_params["data"] = wrap_local_mutation_command(original, marker_id)
    return new_params, pre, marker_id, original
