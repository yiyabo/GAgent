"""Operation and tool names a caller guessed that bio_tools does not have.

The models guess an operation name from the program they know: ``run`` for phabox2, geNomad, CheckV and
iPHoP, ``cluster`` and ``search`` for MMseqs2, ``align`` and ``tree`` for the phylogeny tools, ``ani`` for
fastANI. The refusal used to name the valid operations and nothing else, so the next call often guessed
again. This module

* accepts a spelling that differs only in case, separators or a ``tool.`` prefix (``End-To-End``),
* ranks the operations a guess most likely meant,
* builds the usage of the best match (input, parameters, an example call) and a one-line description of
  every operation, so the caller can retry once with the right call.

Nothing here runs a tool; it only reads ``tools_config.json``.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, Iterable, List, Optional

from . import param_hints

# Guesses that mean "the whole pipeline" or "the tool's main step".
_DEFAULT_GUESSES = frozenset(
    {"run", "execute", "exec", "start", "default", "main", "all", "full", "pipeline", "workflow"}
)
_DEFAULT_OPERATION = "end_to_end"
_MAX_SUGGESTIONS = 3
_MAX_DESCRIPTION = 120
_WORD_RE = re.compile(r"[a-z0-9]+")


def _flat(name: Any) -> str:
    return re.sub(r"[\s\-.]+", "_", str(name or "").strip().lower()).strip("_")


def _operations(config: Dict[str, Any], tool: str) -> Dict[str, Dict[str, Any]]:
    ops = (config.get(tool) or {}).get("operations") or {}
    return {str(name): (info if isinstance(info, dict) else {}) for name, info in ops.items()}


def resolve_operation(operation: str, tool: str, names: Iterable[str]) -> Optional[str]:
    """The declared operation ``operation`` spells differently (case, ``-`` or spaces, a ``tool.`` prefix)."""
    names = list(names)
    if operation in names:
        return operation
    flat = _flat(operation)
    prefix = _flat(tool) + "_"
    candidates = [flat, flat[len(prefix):]] if flat.startswith(prefix) else [flat]
    for candidate in candidates:
        for name in names:
            if _flat(name) == candidate:
                return name
    return None


def suggest_operations(config: Dict[str, Any], tool: str, operation: str, names: Iterable[str]) -> List[str]:
    """Up to three operations ``operation`` most likely meant, best first; empty when nothing is close."""
    names = list(names)
    if not names:
        return []
    guess = _flat(operation)
    prefix = _flat(tool) + "_"
    if guess.startswith(prefix):
        guess = guess[len(prefix):]
    ranked: List[str] = []

    def add(items: Iterable[str]) -> None:
        for item in items:
            if item in names and item not in ranked:
                ranked.append(item)

    if guess in _DEFAULT_GUESSES or guess == _flat(tool):
        if _DEFAULT_OPERATION in names:
            add([_DEFAULT_OPERATION])
        elif len(names) == 1:
            add(names)
    if len(guess) >= 3:
        add(name for name in names if guess in _flat(name) or (len(_flat(name)) >= 4 and _flat(name) in guess))
    add(difflib.get_close_matches(guess, names, n=_MAX_SUGGESTIONS, cutoff=0.6))
    if len(guess) >= 4:
        ops = _operations(config, tool)
        add(
            name
            for name in names
            if any(word.startswith(guess) for word in _WORD_RE.findall(str(ops.get(name, {}).get("description") or "").lower()))
        )
    if not ranked and len(names) == 1:
        add(names)
    return ranked[:_MAX_SUGGESTIONS]


def _short(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _MAX_DESCRIPTION else text[: _MAX_DESCRIPTION - 1] + "…"


def operation_summaries(config: Dict[str, Any], tool: str, names: Iterable[str]) -> Dict[str, str]:
    ops = _operations(config, tool)
    return {name: _short(ops.get(name, {}).get("description")) for name in names}


def example_call(config: Dict[str, Any], tool: str, operation: str) -> Dict[str, Any]:
    """A call of ``tool.operation`` with every required input and parameter filled with a placeholder."""
    op = _operations(config, tool).get(operation, {})
    call: Dict[str, Any] = {"tool_name": tool, "operation": operation}
    if op.get("requires_input"):
        call["input_file"] = "/absolute/path/to/input"
    params = {row["name"]: "..." for row in param_hints.details(config, tool, operation) if row.get("required")}
    if params:
        call["params"] = params
    return call


def usage(config: Dict[str, Any], tool: str, operation: str) -> str:
    """One sentence on how ``tool.operation`` is called."""
    op = _operations(config, tool).get(operation, {})
    label = "%s.%s" % (tool, operation)
    description = _short(op.get("description"))
    parts = ["%s%s." % (label, (" (" + description.rstrip(".") + ")") if description else "")]
    if op.get("requires_input"):
        parts.append("It needs input_file (or sequence_text).")
    rows = param_hints.details(config, tool, operation)
    if rows:
        parts.append("Params: " + "; ".join(param_hints._summary(row) for row in rows) + ".")
    return " ".join(parts)


def unknown_operation_details(config: Dict[str, Any], tool: str, operation: str, names: Iterable[str]) -> Dict[str, Any]:
    """The error message and the retry details for an operation ``tool`` does not have."""
    names = list(names)
    suggested = suggest_operations(config, tool, operation, names)
    summaries = operation_summaries(config, tool, names)
    lines = ["Unknown operation %r for %s." % (operation, tool)]
    if suggested:
        lines.append("Did you mean %s?" % " or ".join(repr(name) for name in suggested))
        lines.append("Usage: " + usage(config, tool, suggested[0]))
    lines.append("Operations of %s: %s." % (tool, "; ".join("%s (%s)" % (n, d) if d else n for n, d in summaries.items())))
    lines.append(
        "Call bio_tools again with one of these exact operation names (operation='help' lists every parameter); "
        "if none fits, say so or use execute_code instead of guessing another name."
    )
    details: Dict[str, Any] = {
        "error": " ".join(lines),
        "available_operations": summaries,
        "suggested_operations": suggested,
    }
    if suggested:
        details["retry_hint"] = example_call(config, tool, suggested[0])
    return details


def suggest_tools(tool: str, names: Iterable[str]) -> List[str]:
    """Up to three tool names ``tool`` most likely meant (``phabox`` -> ``phabox2``)."""
    names = list(names)
    guess = _flat(tool).split("_")[0] if "." in str(tool) else _flat(tool)
    exact = [name for name in names if _flat(name) == guess]
    if exact:
        return exact
    ranked = [name for name in names if len(guess) >= 3 and (_flat(name).startswith(guess) or guess.startswith(_flat(name)))]
    for name in difflib.get_close_matches(guess, names, n=_MAX_SUGGESTIONS, cutoff=0.6):
        if name not in ranked:
            ranked.append(name)
    return ranked[:_MAX_SUGGESTIONS]


def unknown_tool_details(tool: str, names: Iterable[str]) -> Dict[str, Any]:
    names = list(names)
    suggested = suggest_tools(tool, names)
    message = "Unknown tool: %s." % tool
    if suggested:
        message += " Did you mean %s?" % " or ".join(repr(name) for name in suggested)
    message += " tool_name must be one of the available_tools; call operation='help' on it to see its operations."
    return {"error": message, "available_tools": names, "suggested_tools": suggested}
