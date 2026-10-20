"""Which bio_tools the model is offered when the server runs them natively (BIO_TOOLS_EXECUTION_MODE=native).

``tool_box.bio_tools`` imports the whole handler (and through it the tool schemas), so the schema and prompt
builders cannot import it at module load. ``native_exec`` is standalone by design (stdlib only, shared with the
host runner), so it is loaded from its file instead.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_NATIVE_EXEC_PATH = Path(__file__).resolve().parents[2] / "tool_box" / "bio_tools" / "native_exec.py"
_native_exec: Optional[Any] = None


def execution_mode() -> str:
    return os.getenv("BIO_TOOLS_EXECUTION_MODE", "").strip().lower()


def native_catalog(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """Tool -> operations with a native implementation."""
    global _native_exec
    if _native_exec is None:
        spec = importlib.util.spec_from_file_location("_gagent_native_exec", _NATIVE_EXEC_PATH)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        _native_exec = module
    return _native_exec.native_catalog(config)


def exposed_catalog(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """Tool -> operations offered to the model for the configured execution mode.

    ``native`` offers only what the host runner can start; ``native_first`` offers everything; any other mode
    offers everything except tools marked ``native_only`` (host software with no Docker image)."""
    mode = execution_mode()
    if mode == "native":
        return {tool: sorted(ops) for tool, ops in native_catalog(config).items()}
    return {
        str(tool): sorted((info or {}).get("operations", {}).keys())
        for tool, info in config.items()
        if mode == "native_first" or not (info or {}).get("native_only")
    }
