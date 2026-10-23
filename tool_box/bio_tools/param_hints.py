"""Parameters a caller guessed that a bio_tools operation does not declare.

The models know the command-line flags of these programs and pass them as parameters (``min_len`` for
``seqkit seq``, ``seed`` for ``seqtk sample``, ``count`` for ``samtools view``). An undeclared name used to
come back as ``Invalid/unsafe parameter: min_len`` and nothing else; the accepted names were in the stored
result but not in the part the model reads. This module

* maps near-certain synonyms onto the declared name (``min_len`` -> ``min_length``, ``--min-len`` too),
* checks types and enumerations of the declared parameters,
* builds the details a caller needs to retry: every accepted parameter with type, default and meaning, and
  the other operations of the tool.

Nothing here runs a tool; it only reads ``tools_config.json``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FLAG_ALIASES = {
    "exclude_flag": "exclude_flags",
    "flag_exclude": "exclude_flags",
    "flags_exclude": "exclude_flags",
    "exclude": "exclude_flags",
    "F": "exclude_flags",
    "require_flag": "require_flags",
    "include_flags": "require_flags",
    "include_flag": "require_flags",
    "flag_require": "require_flags",
    "f": "require_flags",
    "mapq": "min_mapq",
    "min_quality": "min_mapq",
    "min_mapping_quality": "min_mapq",
    "q": "min_mapq",
}
_PRODIGAL_ALIASES = {
    "output": "protein",
    "output_file": "protein",
    "proteins": "protein",
    "protein_file": "protein",
    "output_protein": "protein",
    "output_proteins": "protein",
    "nucleotides": "nucleotide",
    "output_nucleotide": "nucleotide",
    "gff_file": "gff",
    "output_gff": "gff",
}
# (tool, operation) -> {guessed name: declared name}. Only synonyms with one possible meaning belong here:
# ``flag`` could be samtools -f or -F, so it is reported instead of guessed.
ALIASES: Dict[Tuple[str, str], Dict[str, str]] = {
    ("seqkit", "seq"): {
        "min_len": "min_length",
        "minimum_length": "min_length",
        "min_size": "min_length",
        "min": "min_length",
        "max_len": "max_length",
        "maximum_length": "max_length",
        "max_size": "max_length",
        "max": "max_length",
    },
    ("seqtk", "sample"): {"random_seed": "seed", "rand_seed": "seed", "s": "seed"},
    ("samtools", "view"): _FLAG_ALIASES,
    ("samtools", "count"): _FLAG_ALIASES,
    ("prodigal", "predict"): _PRODIGAL_ALIASES,
    ("prodigal", "meta"): _PRODIGAL_ALIASES,
}

# Keys the handler itself understands inside ``params`` (see ``_SYSTEM_RESERVED_PARAM_KEYS``).
RESERVED_KEYS = frozenset({"input_file", "output_file", "params", "timeout", "background", "job_id"})

_NUMBER_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_MAX_DESCRIPTION = 140


def _operation(config: Dict[str, Any], tool: str, operation: str) -> Dict[str, Any]:
    op = ((config.get(tool) or {}).get("operations") or {}).get(operation)
    return op if isinstance(op, dict) else {}


def declared(config: Dict[str, Any], tool: str, operation: str) -> Dict[str, Dict[str, Any]]:
    """Parameter name -> its metadata (empty for the legacy list form)."""
    raw = _operation(config, tool, operation).get("extra_params")
    if isinstance(raw, dict):
        return {str(key): (meta if isinstance(meta, dict) else {}) for key, meta in raw.items()}
    if isinstance(raw, list):
        return {str(key): {} for key in raw}
    return {}


def _normalise(key: str) -> str:
    return str(key).strip().lstrip("-").replace("-", "_").lower()


def resolve(
    config: Dict[str, Any], tool: str, operation: str, params: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """``params`` with guessed names replaced by declared ones, and the replacements made (guess -> name).

    A name that is declared, reserved or has no unambiguous match is left alone; a replacement is made only
    when the declared name is free, so a caller that passes both ``min_len`` and ``min_length`` keeps the latter.
    """
    names = declared(config, tool, operation)
    if not params or not names:
        return dict(params or {}), {}
    aliases = ALIASES.get((tool, operation), {})
    result: Dict[str, Any] = {}
    renames: Dict[str, str] = {}
    for key, value in dict(params).items():
        key = str(key)
        if key in names or key in RESERVED_KEYS:
            result[key] = value
            continue
        target = aliases.get(key)
        if target is None:
            flat = _normalise(key)
            target = flat if flat in names else aliases.get(flat)
        if target in names and target not in params and target not in result:
            result[target] = value
            renames[key] = target
        else:
            result[key] = value
    return result, renames


def unknown_keys(config: Dict[str, Any], tool: str, operation: str, params: Optional[Dict[str, Any]]) -> List[str]:
    names = declared(config, tool, operation)
    return [str(key) for key in dict(params or {}) if str(key) not in names and str(key) not in RESERVED_KEYS]


def details(config: Dict[str, Any], tool: str, operation: str) -> List[Dict[str, Any]]:
    """Every accepted parameter with the facts a caller needs to use it."""
    rows = []
    for name, meta in declared(config, tool, operation).items():
        row: Dict[str, Any] = {"name": name, "type": meta.get("type") or "string", "required": bool(meta.get("required"))}
        if meta.get("default") is not None:
            row["default"] = meta["default"]
        if isinstance(meta.get("enum"), list):
            row["allowed"] = list(meta["enum"])
        text = str(meta.get("description") or "").strip()
        if text:
            row["description"] = text if len(text) <= _MAX_DESCRIPTION else text[: _MAX_DESCRIPTION - 1] + "…"
        rows.append(row)
    return rows


def _summary(row: Dict[str, Any]) -> str:
    bits = [str(row["type"])]
    if row.get("required"):
        bits.append("required")
    if "default" in row:
        bits.append("default %s" % row["default"])
    if "allowed" in row:
        bits.append("one of %s" % "/".join(str(item) for item in row["allowed"]))
    return "%s (%s)" % (row["name"], ", ".join(bits))


def hint(config: Dict[str, Any], tool: str, operation: str, unknown: List[str]) -> str:
    """One paragraph telling the caller what the operation accepts and where else to look."""
    rows = details(config, tool, operation)
    label = "%s.%s" % (tool, operation)
    if unknown:
        what = "has no parameter %s" % ", ".join(repr(key) for key in unknown)
    else:
        what = "was called with parameters it does not accept"
    if rows:
        accepted = "It accepts: " + "; ".join(_summary(row) for row in rows) + "."
    else:
        accepted = "It takes no parameters besides the input file."
    others = [name for name in ((config.get(tool) or {}).get("operations") or {}) if name != operation]
    parts = ["%s %s. %s" % (label, what, accepted)]
    op = _operation(config, tool, operation)
    if "/work/database" in str(op.get("native_command") or op.get("command") or ""):
        parts.append("The reference database is configured on the server; do not pass a database parameter.")
    if others:
        parts.append("Other operations of %s: %s." % (tool, ", ".join(others)))
    parts.append("If none of this does what you need, say so or use execute_code; do not repeat the same call.")
    return " ".join(parts)


def check_values(config: Dict[str, Any], tool: str, operation: str, params: Optional[Dict[str, Any]]) -> Optional[str]:
    """A message for the first declared parameter whose value has the wrong type or is not an allowed choice."""
    names = declared(config, tool, operation)
    for key, value in dict(params or {}).items():
        meta = names.get(str(key))
        if not meta or value is None:
            continue
        kind = str(meta.get("type") or "").lower()
        text = str(value).strip()
        if kind in {"integer", "int"} and (isinstance(value, bool) or not _INTEGER_RE.match(text)):
            return "%s must be an integer, got %r" % (key, value)
        if kind in {"number", "float"} and (isinstance(value, bool) or not _NUMBER_RE.match(text)):
            return "%s must be a number, got %r" % (key, value)
        allowed = meta.get("enum")
        if isinstance(allowed, list) and text.lower() not in {str(item).lower() for item in allowed}:
            return "%s must be one of %s, got %r" % (key, ", ".join(str(item) for item in allowed), value)
    return None


def relative_prefix_error(
    config: Dict[str, Any], tool: str, operation: str, params: Optional[Dict[str, Any]]
) -> Optional[Tuple[str, str]]:
    """``(parameter, message)`` for a database/index prefix given as a bare name such as ``nt``.

    A native run needs the absolute prefix of a database built in the session; a name is never resolved.
    A parameter the command writes to (`-out /work/{db}` of makeblastdb, `hmmpress /work/{db}`) is the opposite: it is
    a name under the work directory, and a bare name is exactly what it takes.
    """
    names = declared(config, tool, operation)
    command = str(_operation(config, tool, operation).get("command") or "")
    for key in ("db", "index"):
        value = dict(params or {}).get(key)
        if "/work/{%s}" % key in command:
            continue
        if key in names and isinstance(value, str) and value.strip() and not value.strip().startswith("/"):
            meta = names[key]
            description = str(meta.get("description") or "").strip()
            message = (
                "%s must be the absolute path prefix of a database built in this session, not a name like %r."
                % (key, value.strip())
            )
            if description:
                message += " " + description
            return key, message
    return None


def log_refusal(tool: str, operation: str, unknown: List[str], renames: Optional[Dict[str, str]] = None) -> None:
    """One structured line per refused or rewritten call, so the usage in production can be counted."""
    if unknown:
        logger.info("[bio_tools] param_refused tool=%s operation=%s unknown=%s", tool, operation, ",".join(unknown))
    if renames:
        logger.info(
            "[bio_tools] param_renamed tool=%s operation=%s renames=%s",
            tool,
            operation,
            ",".join("%s->%s" % pair for pair in renames.items()),
        )
