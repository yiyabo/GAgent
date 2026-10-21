"""Put a native bio_tools run's output files where the session that asked for them can use them.

A native run writes into ``runtime/bio_tools/_native/runs/<id>/work``, outside every session. The file tool
refuses that path (it is under ``runtime/``), so the agent could not read, copy or submit what the tool had
just produced and fell back to improvised copies from a code cell; delivery acceptance, which only counts
files a tool result names, then failed on files that existed.

After a successful run the files are copied into ``<session>/results/bio_tools/<tool>_<operation>_<run id>/``
and the result names them in ``artifact_paths`` and ``session_outputs``. A copy that cannot be made is
reported, never raised: the run itself succeeded.

An index or other sidecar of the input (``reads.bam`` -> ``reads.bam.bai``, ``ref.fa`` -> ``ref.fa.bwt``) is
different: the next tool looks for it next to the input, so it is copied there when the input is in the session.
The host user cannot write into a session directory, so the tool is run to write the sidecar in its own run
directory (``native_command`` in ``tools_config.json``) and this module puts it where it belongs.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import output_paths

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
PUBLISH_PARTS = ("results", "bio_tools")


def _safe_name(text: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("._") or "run"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _input_neighbourhood(input_file: Optional[str], session_root: Path) -> "tuple[str, Optional[Path]]":
    """The input's file name and its directory, the latter only when that directory is inside the session."""
    text = str(input_file or "").strip()
    if not text or not os.path.isabs(text):
        return "", None
    path = Path(os.path.abspath(text))
    try:
        directory = path.parent.resolve()
    except OSError:
        return "", None
    return (path.name, directory) if _inside(directory, session_root.resolve()) else ("", None)


def publish_run_outputs(
    result: Dict[str, Any],
    session_dir: Optional[Path],
    requests: Optional[List[output_paths.OutputRequest]] = None,
    input_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ``result`` with the run's output files copied into ``session_dir``.

    Files the run wrote under a name that ``requests`` maps to a location in the session (see
    ``output_paths``) go to that location; a file named ``<input name>.<extension>`` goes next to ``input_file``
    when that is in the session; every other file goes to ``results/bio_tools/<run>/``.

    Adds ``session_outputs`` (path, source, size per file), ``artifact_paths`` (the absolute session paths),
    ``session_output_dir`` and, per ``produced_files`` entry, ``session_path``; ``session_output_path`` when
    the run's main ``output_path`` was among the copies. Files over the size caps are listed in
    ``session_outputs_skipped``. The input is not modified.
    """
    produced = result.get("produced_files")
    if not result.get("success") or not session_dir or not isinstance(produced, list) or not produced:
        return result
    work_dir_text = str(result.get("work_dir") or "")
    if not work_dir_text:
        return result
    work_dir = Path(work_dir_text)
    session_root = Path(session_dir)
    dest_root = session_root.joinpath(
        *PUBLISH_PARTS,
        "%s_%s_%s" % (_safe_name(result.get("tool")), _safe_name(result.get("operation")), _safe_name(result.get("run_id"))),
    )

    input_name, input_dir = _input_neighbourhood(input_file, session_root)
    outputs: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    entries: List[Any] = []
    copied_by_source: Dict[str, str] = {}
    total = 0
    for entry in produced:
        item = dict(entry) if isinstance(entry, dict) else {"path": str(entry)}
        entries.append(item)
        rel = str(item.get("path") or "")
        rel_path = Path(rel)
        if not rel or rel_path.is_absolute() or ".." in rel_path.parts:
            skipped.append({"path": rel, "reason": "unsafe_path"})
            continue
        source = work_dir / rel_path
        requested_target = output_paths.target_for(rel, requests or [])
        beside_input = bool(
            input_dir and not requested_target and len(rel_path.parts) == 1 and rel_path.name.startswith(input_name + ".")
        )
        if requested_target:
            target = Path(requested_target)
        elif beside_input:
            target = input_dir / rel_path.name
        else:
            target = dest_root / rel_path
        try:
            if source.is_symlink() or not source.is_file():
                continue
            size = source.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                skipped.append({"path": rel, "reason": "too_large"})
                continue
            if not _inside(target.resolve(), session_root.resolve()):
                skipped.append({"path": rel, "reason": "unsafe_path"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        except OSError as exc:
            logger.warning("native bio_tools output %s not copied into the session: %s", source, exc)
            skipped.append({"path": rel, "reason": "copy_failed"})
            continue
        total += size
        item["session_path"] = str(target)
        copied_by_source[str(source)] = str(target)
        record = {"path": str(target), "source": str(source), "size": size}
        if requested_target:
            record["requested"] = True
        if beside_input:
            record["beside_input"] = True
        outputs.append(record)

    if not outputs and not skipped:
        return result
    updated = dict(result)
    updated["produced_files"] = entries
    if outputs:
        updated["session_outputs"] = outputs
        updated["artifact_paths"] = [item["path"] for item in outputs]
        if any(not item.get("requested") and not item.get("beside_input") for item in outputs):
            updated["session_output_dir"] = str(dest_root)
        main_output = copied_by_source.get(str(result.get("output_path") or ""))
        if main_output:
            updated["session_output_path"] = main_output
    if skipped:
        updated["session_outputs_skipped"] = skipped
    if requests:
        updated["requested_outputs"] = output_paths.describe(requests)
        updated["output_path_note"] = (
            "An output location inside this session was run under a plain name in the run directory and the "
            "files were copied to the requested location; artifact_paths lists where they are now."
        )
    return updated


def session_dir_for(tool_context: Any) -> Optional[Path]:
    """The session directory of the calling context, or None when it has no session."""
    session_id = str(getattr(tool_context, "session_id", "") or "").strip()
    if not session_id:
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        return Path(get_runtime_session_dir(session_id, create=True))
    except Exception:  # noqa: BLE001 - an id that maps to no directory just means nothing is published
        return None


def publish_for_context(
    result: Dict[str, Any],
    tool_context: Any,
    requests: Optional[List[output_paths.OutputRequest]] = None,
    input_file: Optional[str] = None,
) -> Dict[str, Any]:
    """``publish_run_outputs`` for the session of ``tool_context``; any failure leaves ``result`` as it was."""
    try:
        return publish_run_outputs(result, session_dir_for(tool_context), requests, input_file)
    except Exception:  # noqa: BLE001 - publishing is a convenience on top of a run that already succeeded
        logger.exception("publishing native bio_tools outputs failed")
        return result
