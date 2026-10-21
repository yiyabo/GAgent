"""Let a model name an output location inside its own session.

A native run writes only into its own run directory, so ``output``/``protein``/``nucleotide`` and the
``output_file`` argument must be names relative to it. Models naturally give the place they want the file,
for example ``/app/runtime/session_x/results/proteins.faa``; that was refused ("must stay inside the run
directory"), the tool was reported as failed, and the answer came from an improvised fallback.

For a location inside the calling session, the run now gets a plain name in its directory, and the files it
writes under that name are copied to the requested location afterwards (``native_publish``). Anything else
is left unchanged and refused as before.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import native_exec

# Session directories a tool result may be written to. ``uploads`` holds the user's own files and is never
# overwritten this way.
SESSION_OUTPUT_DIRS = frozenset({"results", "scratch", "workspace", "deliverables", "raw_files", "tool_outputs"})


@dataclass(frozen=True)
class OutputRequest:
    key: str
    requested: str
    relative: str
    target: str


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _run_name(path: Path, taken: set) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.name).strip("._") or "output"
    name, index = base, 2
    while name in taken:
        name = "%s_%d" % (base, index)
        index += 1
    taken.add(name)
    return name


def plan_session_outputs(
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    session_dir: Optional[Path],
) -> Tuple[Optional[str], Dict[str, Any], List[OutputRequest]]:
    """``(output_file, params, requests)`` with session-internal absolute outputs turned into run-relative names."""
    new_params = dict(params or {})
    if session_dir is None:
        return output_file, new_params, []
    try:
        session_root = Path(session_dir).resolve()
    except OSError:
        return output_file, new_params, []

    candidates: List[Tuple[str, str]] = []
    if isinstance(output_file, str):
        candidates.append(("output_file", output_file))
    for key in sorted(native_exec.OUTPUT_PATH_PARAM_KEYS):
        if isinstance(new_params.get(key), str):
            candidates.append((key, new_params[key]))

    requests: List[OutputRequest] = []
    rewritten: Dict[str, str] = {}
    taken: set = set()
    for key, value in candidates:
        text = value.strip()
        if not text.startswith("/") or text.startswith("/work/") or text == "/work":
            continue
        if any(ch in text for ch in ("\x00", "\n", "\r")) or ".." in Path(text).parts:
            continue
        real = Path(os.path.realpath(text))
        if not _inside(real, session_root):
            continue
        parts = real.relative_to(session_root).parts
        if not parts or parts[0] not in SESSION_OUTPUT_DIRS:
            continue
        name = _run_name(real, taken)
        rewritten[key] = name
        requests.append(OutputRequest(key=key, requested=text, relative=name, target=str(real)))

    if not requests:
        return output_file, new_params, []
    out_file = rewritten.get("output_file", output_file)
    for key, name in rewritten.items():
        if key != "output_file":
            new_params[key] = name
    return out_file, new_params, requests


def target_for(relative_path: str, requests: List[OutputRequest]) -> Optional[str]:
    """Where a file the run wrote at ``relative_path`` belongs, when a request named it (longest name wins)."""
    best: Optional[OutputRequest] = None
    suffix = ""
    for request in requests:
        if not relative_path.startswith(request.relative):
            continue
        rest = relative_path[len(request.relative):]
        # Same file, a file inside a requested directory, or files sharing a requested prefix (name.bai).
        if rest and not (rest.startswith("/") or rest.startswith(".")):
            continue
        if best is None or len(request.relative) > len(best.relative):
            best, suffix = request, rest
    return best.target + suffix if best is not None else None


def describe(requests: List[OutputRequest]) -> List[Dict[str, str]]:
    return [{"parameter": r.key, "requested": r.requested, "run_name": r.relative} for r in requests]
