"""Native (host + Slurm) execution of bio_tools: the part shared by the app and the host runner.

The app container cannot see the host's software, so a native run is a hand-off through a shared
directory: the app writes ``request.json``, a small runner on the host validates it, renders the
command from ``tools_config.json`` and submits it with ``sbatch``.

This module must stay importable on its own (standard library only, Python 3.8 syntax, no relative
imports): the runner loads it as a top-level module from the directory it is installed in.

Trust model: nothing in a request is a command. The request names a tool, an operation and
parameters; the command comes from the configured template, parameters are validated against the
operation's declared parameters, and every path must resolve inside the allowed roots.
"""

import json
import math
import os
import re
import shlex
import tempfile
from string import Formatter
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROTOCOL_VERSION = 1

NATIVE_DIRNAME = "_native"
RUNS_DIRNAME = "runs"
HEARTBEAT_FILENAME = "runner.json"
REQUEST_FILENAME = "request.json"
STATUS_FILENAME = "status.json"
CANCEL_FILENAME = "cancel"
SCRIPT_FILENAME = "job.sh"
STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"
EXIT_CODE_FILENAME = "exit_code"
WORK_DIRNAME = "work"

RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")
STATES_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timeout", "rejected"})

DEFAULT_CPUS = 2
DEFAULT_MAX_CPUS = 32
DEFAULT_MEM_MB = 8192
DEFAULT_TIME_LIMIT_SECONDS = 4 * 3600
MAX_TIME_LIMIT_SECONDS = 7 * 24 * 3600

_MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9_.+\-]+(/[A-Za-z0-9_.+\-]+)*$")
_CONDA_ENV_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_PATH_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9._/\-,:+=@~]+$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_QUOTED_PLACEHOLDER_RE = re.compile(r"""(['"])\{([a-zA-Z_][a-zA-Z0-9_]*)\}\1""")
_WORK_PLACEHOLDER_RE = re.compile(r"/work/\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_WORK_LITERAL_RE = re.compile(r"/work(?![A-Za-z0-9_])")
_PATH_FORBIDDEN_SUBSTRINGS = (";", "|", "&&", "||", ">", "<", "$", "`")

# Keep in step with bio_tools_handler: these decide which parameters are paths.
INPUT_PATH_PARAM_KEYS = frozenset({
    "reference", "query", "target", "db", "index", "depth", "r1", "r2", "contigs", "bam_files",
    "bins", "snakefile", "pipeline", "genome_dir", "tree", "abundance", "coverage", "input",
})
OUTPUT_PATH_PARAM_KEYS = frozenset({
    "output", "output_dir", "output_prefix", "index_prefix", "protein", "nucleotide",
})
MULTI_PATH_PARAM_KEYS = frozenset({"bam_files", "bins"})
# Names a database *prefix*: the files are <prefix>.* so the prefix itself need not exist.
PREFIX_PATH_PARAM_KEYS = frozenset({"db", "index"})
_RESERVED_PARAM_KEYS = frozenset({"input_file", "output_file", "params", "timeout", "background", "job_id"})


class NativeError(ValueError):
    """A request that cannot be run natively; the message is safe to show to the user."""

    def __init__(self, message: str, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------------- config

def load_tools_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def native_spec(config: Dict[str, Any], tool: str) -> Optional[Dict[str, Any]]:
    """The ``native`` block of a tool, or None when the tool has no native support."""
    block = (config.get(tool) or {}).get("native")
    return block if isinstance(block, dict) else None


def supports_native(config: Dict[str, Any], tool: str, operation: str) -> bool:
    spec = native_spec(config, tool)
    if spec is None:
        return False
    op = ((config.get(tool) or {}).get("operations") or {}).get(operation)
    if not isinstance(op, dict):
        return False
    return op.get("native", True) is not False


def validate_native_spec(tool: str, spec: Dict[str, Any]) -> None:
    modules = spec.get("modules") or []
    if not isinstance(modules, list) or not all(isinstance(m, str) and _MODULE_NAME_RE.match(m) for m in modules):
        raise NativeError("%s: native.modules must be a list of module names" % tool, "bad_config")
    env = spec.get("conda_env")
    if env is not None and not (isinstance(env, str) and _CONDA_ENV_RE.match(env)):
        raise NativeError("%s: native.conda_env is not a valid environment name" % tool, "bad_config")
    path_tools = spec.get("path_tools") or []
    if not isinstance(path_tools, list) or not all(isinstance(t, str) and _CONDA_ENV_RE.match(t) for t in path_tools):
        raise NativeError("%s: native.path_tools must be a list of program names" % tool, "bad_config")
    if not modules and not env and not path_tools:
        raise NativeError("%s: native needs modules, conda_env or path_tools" % tool, "bad_config")


# ----------------------------------------------------------------------------- paths

def _reject_control_chars(value: str, key: str) -> None:
    if _CONTROL_CHAR_RE.search(value):
        raise NativeError("Invalid parameter: %s" % key, "invalid_parameter")


def _sanitize_path_text(key: str, value: str) -> str:
    text = value.strip()
    _reject_control_chars(text, key)
    if not text:
        return text
    if any(token in text for token in _PATH_FORBIDDEN_SUBSTRINGS) or not _PATH_SAFE_VALUE_RE.fullmatch(text):
        raise NativeError("Invalid parameter: %s" % key, "invalid_parameter")
    return text


def _real(path: str) -> str:
    return os.path.realpath(path)


def _under(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/")


def _within_any(path: str, roots: Sequence[str]) -> bool:
    return any(_under(path, r) for r in roots)


def _relative_output(key: str, value: str) -> str:
    """An output location given as a relative path; ``/work/`` prefixes from container-style
    callers are accepted and dropped."""
    text = _sanitize_path_text(key, value)
    if text.startswith("/work/"):
        text = text[len("/work/"):]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or not parts or any(p == ".." for p in parts):
        raise NativeError("%s must be a relative path inside the run directory" % key, "invalid_parameter")
    return "/".join(parts)


def resolve_output(key: str, value: str, work_dir: str) -> str:
    work_real = _real(work_dir)
    if value.strip() in (".", "/work"):
        return work_real
    if os.path.isabs(value.strip()) and not value.strip().startswith("/work/"):
        candidate = _real(_sanitize_path_text(key, value))
        if not _under(candidate, work_real):
            raise NativeError("%s must stay inside the run directory" % key, "invalid_parameter")
        return candidate
    candidate = _real(os.path.join(work_real, _relative_output(key, value)))
    if not _under(candidate, work_real):
        raise NativeError("%s must stay inside the run directory" % key, "invalid_parameter")
    return candidate


def resolve_input(key: str, value: str, read_roots: Sequence[str], work_dir: str) -> str:
    text = _sanitize_path_text(key, value)
    if not text:
        return text
    if not os.path.isabs(text):
        raise NativeError(
            "%s must be an absolute path (use the output_path returned by the earlier step that made it)" % key,
            "invalid_parameter",
        )
    allowed = [_real(r) for r in read_roots] + [_real(work_dir)]
    if key in PREFIX_PATH_PARAM_KEYS:
        parent = _real(os.path.dirname(text) or "/")
        if not _within_any(parent, allowed):
            raise NativeError("%s is outside the directories this server may read" % key, "path_not_allowed")
        return os.path.join(parent, os.path.basename(text))
    real = _real(text)
    if not _within_any(real, allowed):
        raise NativeError("%s is outside the directories this server may read" % key, "path_not_allowed")
    if not os.path.exists(real):
        raise NativeError("%s does not exist: %s" % (key, text), "input_not_found")
    return real


# ----------------------------------------------------------------------------- command

def _template_fields(template: str) -> List[str]:
    fields = []  # type: List[str]
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name and field_name not in fields:
            fields.append(field_name)
    return fields


def _declared_params(op: Dict[str, Any]) -> set:
    raw = op.get("extra_params", {})
    if isinstance(raw, dict):
        return {str(k) for k in raw}
    if isinstance(raw, list):
        return {str(k) for k in raw}
    return set()


def _clamp_cpus(spec: Dict[str, Any], threads_value: Any) -> int:
    base = int(spec.get("cpus") or DEFAULT_CPUS)
    cap = int(spec.get("max_cpus") or DEFAULT_MAX_CPUS)
    if threads_value not in (None, ""):
        try:
            wanted = int(str(threads_value).strip())
        except ValueError:
            raise NativeError("threads must be an integer", "invalid_parameter")
        if wanted < 1:
            raise NativeError("threads must be at least 1", "invalid_parameter")
        return min(wanted, cap)
    return min(base, cap)


def time_limit_seconds(spec: Dict[str, Any], requested: Optional[int]) -> int:
    default = int(spec.get("time_limit_seconds") or DEFAULT_TIME_LIMIT_SECONDS)
    seconds = int(requested) if requested and int(requested) > 0 else default
    return max(60, min(seconds, MAX_TIME_LIMIT_SECONDS))


def build_job(
    config: Dict[str, Any],
    *,
    tool: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
    work_dir: str,
    read_roots: Sequence[str],
    database_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a request and render the command for the host. ``work_dir`` and every path in the
    request are host paths. Raises NativeError for anything that must not run."""
    if tool not in config:
        raise NativeError("Unknown tool: %s" % tool, "unknown_tool")
    if not supports_native(config, tool, operation):
        raise NativeError("%s.%s is not available natively on this server" % (tool, operation), "not_native")
    spec = native_spec(config, tool) or {}
    validate_native_spec(tool, spec)
    op = config[tool]["operations"][operation]

    declared = _declared_params(op)
    # ``native_command`` lets a tool pass its CPU allocation ($GAGENT_CPUS) where the Docker command relies on defaults.
    template = _QUOTED_PLACEHOLDER_RE.sub(r"{\2}", op.get("native_command") or op["command"])
    output_keys = set(OUTPUT_PATH_PARAM_KEYS) | set(_WORK_PLACEHOLDER_RE.findall(template))
    uses_database = "/work/database" in template
    if uses_database:
        if not database_path:
            raise NativeError("%s needs a reference database that is not configured on this server" % tool,
                              "database_unavailable")
        template = template.replace("/work/database", "{_db}")
    template = _WORK_PLACEHOLDER_RE.sub(r"{\1}", template)
    template = _WORK_LITERAL_RE.sub("{_work}", template)

    raw = {}  # type: Dict[str, Any]
    for key, value in dict(params or {}).items():
        key = str(key)
        if key not in declared and key not in _RESERVED_PARAM_KEYS:
            raise NativeError("Invalid/unsafe parameter: %s" % key, "invalid_parameter")
        if key in _RESERVED_PARAM_KEYS and key not in declared:
            continue
        if value is None:
            raw[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            raw[key] = str(value)
        else:
            raise NativeError("Invalid/unsafe parameter: %s" % key, "invalid_parameter")

    values = {"input": input_file or "", "output": output_file or ""}  # type: Dict[str, str]
    values.update(raw)
    fields = _template_fields(template)
    if "query" in fields and not values.get("query") and values.get("input"):
        values["query"] = values["input"]
    if "output" in fields and not values.get("output"):
        values["output"] = "%s_%s.out" % (tool, operation)
    values["output_dir"] = values.get("output_dir") or "."

    missing = sorted(f for f in fields if not f.startswith("_") and values.get(f) in ("", None))
    if missing:
        raise NativeError("Missing required parameters for %s.%s: %s" % (tool, operation, ", ".join(missing)),
                          "missing_required_params")

    rendered = {}  # type: Dict[str, str]
    for key, value in values.items():
        if key in output_keys:
            rendered[key] = resolve_output(key, value, work_dir) if value else value
        elif key in INPUT_PATH_PARAM_KEYS:
            if key in MULTI_PATH_PARAM_KEYS:
                parts = [p.strip() for p in value.split(",") if p.strip()]
                rendered[key] = ",".join(resolve_input(key, p, read_roots, work_dir) for p in parts)
            else:
                rendered[key] = resolve_input(key, value, read_roots, work_dir) if value else value
        else:
            _reject_control_chars(value, key)
            rendered[key] = shlex.quote(value)

    cpus = _clamp_cpus(spec, values.get("threads"))
    if "threads" in fields:
        rendered["threads"] = shlex.quote(str(cpus))
    if "cores" in fields and values.get("cores"):
        rendered["cores"] = shlex.quote(str(_clamp_cpus(spec, values.get("cores"))))

    rendered["_work"] = _real(work_dir)
    rendered["_db"] = _real(database_path) if database_path else ""
    command = template.format(**rendered)

    # What a later step should be pointed at: the output file, or for tools that write a database
    # or an output prefix (makeblastdb, bwa index, ...) that prefix.
    output_path = None
    for key in ("output", "db", "output_prefix", "index_prefix", "output_dir", "protein", "nucleotide"):
        if key in fields and key in output_keys and rendered.get(key):
            output_path = rendered[key]
            break
    return {
        "tool": tool,
        "operation": operation,
        "command": command,
        "modules": list(spec.get("modules") or []),
        "conda_env": spec.get("conda_env"),
        "path_tools": list(spec.get("path_tools") or []),
        "cpus": cpus,
        "mem_mb": int(spec.get("mem_mb") or DEFAULT_MEM_MB),
        "time_limit_seconds": time_limit_seconds(spec, timeout),
        "partition": spec.get("partition"),
        "work_dir": _real(work_dir),
        "output_path": output_path,
    }


def render_script(
    job: Dict[str, Any], *, run_dir: str, conda_base: Optional[str] = None, conda_envs_dir: Optional[str] = None
) -> str:
    """The batch script. ``-l`` makes the login profile load Lmod, as it does for a user."""
    lines = [
        "#!/bin/bash -l",
        "# Written by the gagent bio_tools runner. Do not edit.",
        "umask 000",
        # Whatever ends the script (the tool, a failed module load, a stray exit) leaves its status here.
        "trap 'echo $? > %s' EXIT" % shlex.quote(os.path.join(run_dir, EXIT_CODE_FILENAME)),
        "cd %s || exit 98" % shlex.quote(job["work_dir"]),
        "export GAGENT_CPUS=%d OMP_NUM_THREADS=%d" % (int(job["cpus"]), int(job["cpus"])),
    ]
    if job["modules"]:
        lines.append("type module >/dev/null 2>&1 || . /etc/profile.d/z00_lmod.sh")
        lines.append("module load %s || { echo 'module load failed: %s' >&2; exit 97; }" % (
            " ".join(shlex.quote(m) for m in job["modules"]),
            " ".join(job["modules"]),
        ))
    if job.get("conda_env"):
        if not conda_base:
            raise NativeError("conda is not configured on this server", "bad_config")
        # The login profile loads site modules whose PYTHONPATH/PERL5LIB would shadow the environment's own packages.
        lines.append("unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PERL5LIB")
        if conda_envs_dir:
            lines.append("export CONDA_ENVS_PATH=%s" % shlex.quote(conda_envs_dir))
        lines.append("source %s/etc/profile.d/conda.sh && conda activate %s || { echo 'conda activate failed' >&2; exit 96; }" % (
            shlex.quote(conda_base), shlex.quote(job["conda_env"])))
    for program in job.get("path_tools") or []:
        lines.append("command -v %s >/dev/null || { echo 'program not found on this server: %s' >&2; exit 95; }" % (
            shlex.quote(program), program))
    lines += [
        "set -o pipefail",
        job["command"],
        "",
    ]
    return "\n".join(lines)


def slurm_time(seconds: int) -> str:
    minutes = int(math.ceil(seconds / 60.0))
    return "%d:%02d:00" % (minutes // 60, minutes % 60)


# ----------------------------------------------------------------------------- files

def write_json_atomic(path: str, payload: Dict[str, Any], mode: int = 0o644) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def native_root(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, NATIVE_DIRNAME)


def runs_root(runtime_dir: str) -> str:
    return os.path.join(native_root(runtime_dir), RUNS_DIRNAME)


def heartbeat_path(runtime_dir: str) -> str:
    return os.path.join(native_root(runtime_dir), HEARTBEAT_FILENAME)


def split_request_paths(request: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    params = request.get("params")
    return request.get("input_file"), request.get("output_file"), (params if isinstance(params, dict) else {})
