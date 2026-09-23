"""Output pipeline for execute_code: ANSI strip, head/tail truncation, spill, hints."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[@-_]")

_HEAD_FRACTION = 0.4  # 40% head / 60% tail, byte-level


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def head_tail_split(cap: int) -> Tuple[int, int]:
    head = int(cap * _HEAD_FRACTION)
    return head, cap - head


def spill_stdout(text: str, scratch_dir: Path) -> Optional[str]:
    """Write full stdout under <scratch>/spill; content-addressed, best-effort."""
    try:
        if len(text) > config.MAX_SPILLED_STDOUT_BYTES:
            text = (
                text[: config.MAX_SPILLED_STDOUT_BYTES]
                + f"\n\n[... spill capped at {config.MAX_SPILLED_STDOUT_BYTES:,} bytes ...]"
            )
        spill_dir = Path(scratch_dir) / "spill"
        spill_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
        path = spill_dir / f"stdout-{digest}.txt"
        path.write_text(text, encoding="utf-8", errors="replace")
        return str(path)
    except Exception as exc:  # noqa: BLE001 - spill must never fail the cell result
        logger.debug("execute_code stdout spill failed: %s", exc)
        return None


def truncate_stdout(
    stdout_text: str, scratch_dir: Path
) -> Tuple[str, Dict[str, Any]]:
    """Cap stdout at MAX_STDOUT_BYTES (40% head / 60% tail, byte-level).

    Over-cap full text is spilled under the session scratch dir; metadata
    carries counts plus the spill path and a page-don't-rerun warning.
    """
    stdout_bytes = stdout_text.encode("utf-8", errors="replace")
    total = len(stdout_bytes)
    captured = min(total, config.MAX_STDOUT_BYTES)
    metadata: Dict[str, Any] = {
        "stdout_truncated": total > captured,
        "stdout_bytes_captured": captured,
        "stdout_bytes_total": total,
        "stdout_bytes_omitted": total - captured,
    }
    if total <= config.MAX_STDOUT_BYTES:
        return stdout_bytes.decode("utf-8", errors="replace"), metadata

    head_bytes, tail_bytes = head_tail_split(config.MAX_STDOUT_BYTES)
    notice = (
        f"\n\n[... {total - captured:,} of {total:,} stdout bytes omitted "
        "(middle section) ...]\n\n"
    )
    text = (
        stdout_bytes[:head_bytes].decode("utf-8", errors="replace")
        + notice
        + stdout_bytes[-tail_bytes:].decode("utf-8", errors="replace")
    )
    metadata["warning"] = (
        "execute_code stdout was truncated (head/tail shown); the cell did run. "
        "Re-run with narrower output if the omitted middle is required."
    )
    spill_path = spill_stdout(stdout_text, scratch_dir)
    if spill_path:
        metadata["stdout_spill_path"] = spill_path
        metadata["warning"] = (
            "execute_code stdout was truncated (head/tail shown); the cell did run. "
            f"FULL output saved to {spill_path} — page it with the file/document tools "
            f'(e.g. file_operations operation="read" on that path) instead of re-running. '
            "Kernel state persists: printing a narrower slice next cell is often cheaper."
        )
    return text, metadata


def truncate_stderr(stderr_text: str) -> str:
    raw = stderr_text.encode("utf-8", errors="replace")
    if len(raw) <= config.MAX_STDERR_BYTES:
        return stderr_text
    head_bytes, tail_bytes = head_tail_split(config.MAX_STDERR_BYTES)
    return (
        raw[:head_bytes].decode("utf-8", errors="replace")
        + f"\n[... stderr truncated to {config.MAX_STDERR_BYTES:,} bytes ...]\n"
        + raw[-tail_bytes:].decode("utf-8", errors="replace")
    )


def _missing_import_hint(match: re.Match, allowed_tools: List[str]) -> str:
    missing = match.group(1)
    if missing in {"json_parse", "retry"}:
        return (
            f"Import helpers with `from gagent_tools import {missing}`. If that import "
            "failed, the generated module may be stale — retry the cell with reset=true "
            "to rebuild the kernel."
        )
    return (
        f"'{missing}' is not available inside execute_code. Importable functions here: "
        f"{', '.join(sorted(allowed_tools))} (plus json_parse, retry). For anything "
        "else, use the normal tool call instead of execute_code."
    )


# (regex, formatter) — first match wins. Mirrors the Hermes production-mined
# top-4 failure classes, adapted to the gagent_tools module name.
_FAILURE_HINT_RULES = (
    (
        r"cannot import name '(\w+)' from 'gagent_tools'",
        _missing_import_hint,
    ),
    (
        r"NameError: name '(json_parse|retry)' is not defined",
        lambda match, _: (
            f"Import {match.group(1)} before calling it: "
            f"from gagent_tools import {match.group(1)}"
        ),
    ),
    (
        r"ModuleNotFoundError: No module named '([\w.]+)'",
        lambda match, _: (
            f"'{match.group(1)}' is not installed in the kernel interpreter. "
            "execute_code runs with the backend's Python environment and stdlib; "
            "install the package into that environment or use code_executor instead."
        ),
    ),
    (
        r"the JSON object must be str.*not '?dict'?|string indices must be integers"
        r"|'dict' object has no attribute 'loads'",
        lambda match, _: (
            "gagent_tools functions return DICTS (already parsed) — do not "
            "json.loads() them. Example: web_search(query='...')['results']."
        ),
    ),
)


def failure_hint(traceback_text: str, allowed_tools: List[str]) -> Optional[str]:
    """Map well-known cell failures to one actionable hint (first match wins)."""
    if not traceback_text:
        return None
    window = traceback_text[:4000]
    try:
        for pattern, formatter in _FAILURE_HINT_RULES:
            match = re.search(pattern, window)
            if match:
                return formatter(match, list(allowed_tools))
    except Exception:
        return None
    return None
