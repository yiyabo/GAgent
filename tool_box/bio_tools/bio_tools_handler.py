#!/usr/bin/env python3
"""
Bio Tools Handler

 Docker 
 35+ ， Docker 
"""

import asyncio
import json
import logging
import os
import re
import shlex
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from string import Formatter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from .remote_executor import (
    RemoteExecutionConfig,
    create_remote_run_dirs,
    download_remote_run_dir,
    execute_remote_command,
    resolve_auth,
    resolve_remote_uid_gid,
    upload_files,
)

logger = logging.getLogger(__name__)

# 
BIO_TOOLS_CONFIG_PATH = Path(__file__).parent / "tools_config.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_BASE_DIR = os.getenv(
    "BIO_TOOLS_RUNTIME_DIR",
    str(PROJECT_ROOT / "runtime" / "bio_tools"),
)
DEFAULT_TIMEOUT = int(os.getenv("BIO_TOOLS_DEFAULT_TIMEOUT", "3600"))
DEFAULT_EXECUTION_MODE = "local"
BIO_TOOLS_BACKGROUND_JOB_TYPE = "bio_tools_run"
BIO_TOOLS_BACKGROUND_MODE = "bio_tools_background"
JOB_STATUS_OPERATION = "job_status"

_CONTROL_PARAM_KEYS = {"background", "async", "detached", "wait", "job_id"}
_SYSTEM_RESERVED_PARAM_KEYS = {
    "input_file",
    "output_file",
    "params",
    "timeout",
    "background",
    "job_id",
}

INPUT_PATH_PARAM_KEYS = {
    "reference",
    "query",
    "target",
    "db",
    "index",
    "depth",
    "r1",
    "r2",
    "contigs",
    "bam_files",
    "bins",
    "snakefile",
    "pipeline",
    "genome_dir",
    "tree",
    "abundance",
    "coverage",
    "input",
}
OUTPUT_PATH_PARAM_KEYS = {
    "output",
    "output_dir",
    "output_prefix",
    "index_prefix",
    "protein",
    "nucleotide",
}
MULTI_PATH_PARAM_KEYS = {"bam_files", "bins"}

_PATH_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9._/\-,:+=@~]+$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_QUOTED_PLACEHOLDER_RE = re.compile(r"""(['"])\{([a-zA-Z_][a-zA-Z0-9_]*)\}\1""")
_INLINE_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")
_SEQUENCE_ALLOWED_CHARS_RE = re.compile(r"^[A-Za-z*?.-]+$")
_PATH_FORBIDDEN_SUBSTRINGS = (
    ";",
    "|",
    "&&",
    "||",
    ">",
    "<",
    "$",
    "`",
)
_DEFAULT_SEQUENCE_TEXT_MAX_BYTES = 5 * 1024 * 1024


class UnsafeParameterError(ValueError):
    def __init__(self, key: str):
        super().__init__(f"Invalid/unsafe parameter: {key}")
        self.key = key


def load_tools_config() -> Dict[str, Any]:
    """"""
    if not BIO_TOOLS_CONFIG_PATH.exists():
        logger.error(f"Tools config not found: {BIO_TOOLS_CONFIG_PATH}")
        return {}
    
    with open(BIO_TOOLS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# 
_tools_config: Optional[Dict[str, Any]] = None


def get_tools_config() -> Dict[str, Any]:
    """（）"""
    global _tools_config
    if _tools_config is None:
        _tools_config = load_tools_config()
    return _tools_config


def get_available_bio_tools() -> List[Dict[str, Any]]:
    """"""
    config = get_tools_config()
    tools = []
    for name, info in config.items():
        tools.append({
            "name": name,
            "description": info.get("description", ""),
            "category": info.get("category", ""),
            "operations": list(info.get("operations", {}).keys())
        })
    return tools


def ensure_tool_directory(tool_name: str) -> Path:
    """"""
    tool_dir = Path(RUNTIME_BASE_DIR) / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    return tool_dir


def _needs_container_shell(command: str) -> bool:
    return any(token in command for token in ("|", ">", "<", "&&", "||", ";"))


def _template_fields(template: str) -> List[str]:
    fields: List[str] = []
    for _, field_name, _, _ in Formatter().parse(template):
        if not field_name:
            continue
        if field_name not in fields:
            fields.append(field_name)
    return fields


def _normalize_quoted_placeholders(template: str) -> str:
    # Command templates like -p '{pattern}' should be normalized to -p {pattern}
    # so each placeholder is quoted exactly once by the safe renderer.
    return _QUOTED_PLACEHOLDER_RE.sub(r"{\2}", template)


def _reject_control_chars(value: str, *, key: str) -> None:
    if _CONTROL_CHAR_RE.search(value):
        raise UnsafeParameterError(key)


def _normalize_operation_declared_params(op_config: Dict[str, Any]) -> set:
    raw_params = op_config.get("extra_params", {})
    if isinstance(raw_params, dict):
        return {str(k) for k in raw_params.keys()}
    if isinstance(raw_params, list):
        return {str(k) for k in raw_params}
    return set()


def _sanitize_path_component(key: str, value: str) -> str:
    text = value.strip()
    _reject_control_chars(text, key=key)
    if not text:
        return text
    if any(token in text for token in _PATH_FORBIDDEN_SUBSTRINGS):
        raise UnsafeParameterError(key)
    if not _PATH_SAFE_VALUE_RE.fullmatch(text):
        raise UnsafeParameterError(key)
    return text


def _sanitize_param_value(
    key: str,
    value: Any,
    *,
    path_like: bool,
    multi_path: bool,
) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, bool):
        raw = "true" if value else "false"
    else:
        raw = str(value)

    _reject_control_chars(raw, key=key)

    if path_like:
        if multi_path:
            parts = [p.strip() for p in raw.split(",")]
            safe_parts = [_sanitize_path_component(key, part) for part in parts if part]
            return ",".join(safe_parts)
        return _sanitize_path_component(key, raw)

    # Free text is shell-quoted per-parameter before it enters templates.
    return shlex.quote(raw)


def _validate_and_normalize_operation_params(
    tool_name: str,
    operation: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    config = get_tools_config()
    tool_config = config.get(tool_name, {})
    op_config = tool_config.get("operations", {}).get(operation, {})
    declared_keys = _normalize_operation_declared_params(op_config)
    allowed_keys = declared_keys | _SYSTEM_RESERVED_PARAM_KEYS

    normalized: Dict[str, Any] = {}
    for raw_key, raw_value in dict(params or {}).items():
        key = str(raw_key)
        if key not in allowed_keys:
            raise UnsafeParameterError(key)
        if key in _SYSTEM_RESERVED_PARAM_KEYS and key not in declared_keys:
            continue

        path_like = key in INPUT_PATH_PARAM_KEYS or key in OUTPUT_PATH_PARAM_KEYS
        if path_like:
            normalized[key] = _sanitize_param_value(
                key,
                raw_value,
                path_like=True,
                multi_path=key in MULTI_PATH_PARAM_KEYS,
            )
            continue

        if raw_value is None:
            normalized[key] = ""
            continue

        if isinstance(raw_value, (str, int, float, bool)):
            text = str(raw_value)
            _reject_control_chars(text, key=key)
            normalized[key] = text
            continue

        raise UnsafeParameterError(key)

    return normalized


def _render_safe_command(template: str, safe_params: Dict[str, Any]) -> str:
    normalized_template = _normalize_quoted_placeholders(template)
    return normalized_template.format(**safe_params)


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _normalize_handler_timeout(timeout: Any) -> Optional[int]:
    if timeout is None:
        return None
    try:
        parsed = int(timeout)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if parsed <= 0:
        return None
    return parsed


def _extract_control_flags(
    params: Optional[Dict[str, Any]],
    background: Optional[bool],
    job_id: Optional[str],
) -> Tuple[Dict[str, Any], bool, Optional[str]]:
    source = dict(params or {})
    requested_background = _coerce_bool(background, default=False)
    for key in ("background", "async", "detached"):
        if key in source:
            requested_background = _coerce_bool(source.get(key), default=requested_background)

    requested_job_id = (str(job_id).strip() if job_id is not None else "")
    if not requested_job_id:
        raw_job_id = source.get("job_id")
        if raw_job_id is not None:
            requested_job_id = str(raw_job_id).strip()

    clean_params = {k: v for k, v in source.items() if k not in _CONTROL_PARAM_KEYS}
    return clean_params, requested_background, (requested_job_id or None)


def _build_bio_tools_error(
    *,
    tool_name: str,
    operation: str,
    error: str,
    error_code: Optional[str] = None,
    error_stage: Optional[str] = None,
    no_claude_fallback: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": False,
        "error": error,
        "tool": tool_name,
        "operation": operation,
    }
    if error_code:
        payload["error_code"] = error_code
    if error_stage:
        payload["error_stage"] = error_stage
    if no_claude_fallback:
        payload["no_claude_fallback"] = True
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _operation_requires_input(tool_name: str, operation: str) -> bool:
    config = get_tools_config()
    op_config = config.get(tool_name, {}).get("operations", {}).get(operation, {})
    return bool(op_config.get("requires_input"))


def _get_sequence_text_max_bytes() -> int:
    raw = str(os.getenv("BIO_TOOLS_SEQUENCE_TEXT_MAX_BYTES", "") or "").strip()
    if not raw:
        return _DEFAULT_SEQUENCE_TEXT_MAX_BYTES
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SEQUENCE_TEXT_MAX_BYTES
    if parsed <= 0:
        return _DEFAULT_SEQUENCE_TEXT_MAX_BYTES
    return parsed


def _validate_sequence_string(sequence: str, *, label: str) -> str:
    compact = re.sub(r"\s+", "", sequence or "")
    if not compact:
        raise ValueError(f"{label} is empty.")
    if not _SEQUENCE_ALLOWED_CHARS_RE.fullmatch(compact):
        raise ValueError(
            f"{label} contains unsupported characters; allowed characters are letters and '*', '-', '.', '?'."
        )
    return compact


def _parse_and_normalize_fasta_text(sequence_text: str) -> str:
    records: List[Tuple[str, str]] = []
    current_header: Optional[str] = None
    current_seq_lines: List[str] = []

    for raw_line in sequence_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_header is not None:
                normalized_seq = _validate_sequence_string(
                    "".join(current_seq_lines),
                    label=f"FASTA record '{current_header}'",
                )
                records.append((current_header, normalized_seq))
            current_header = line[1:].strip()
            if not current_header:
                raise ValueError("FASTA header cannot be empty.")
            current_seq_lines = []
            continue

        if current_header is None:
            raise ValueError("FASTA content must start with a '>' header line.")
        current_seq_lines.append(line)

    if current_header is None:
        raise ValueError("No FASTA records found in sequence_text.")

    normalized_seq = _validate_sequence_string(
        "".join(current_seq_lines),
        label=f"FASTA record '{current_header}'",
    )
    records.append((current_header, normalized_seq))

    lines: List[str] = []
    for header, sequence in records:
        lines.append(f">{header}")
        lines.append(sequence)
    lines.append("")
    return "\n".join(lines)


def _resolve_inline_input_dir(session_id: Optional[str]) -> Path:
    if session_id:
        try:
            from app.services.session_paths import get_runtime_session_dir

            session_dir = get_runtime_session_dir(str(session_id), create=True)
            target = session_dir / "tool_outputs" / "bio_tools_inputs"
            target.mkdir(parents=True, exist_ok=True)
            return target.resolve()
        except Exception as exc:
            logger.warning("Failed to resolve session-scoped bio_tools input directory: %s", exc)

    fallback = Path(RUNTIME_BASE_DIR) / "inline_inputs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()


def _prepare_sequence_input(
    *,
    tool_name: str,
    operation: str,
    sequence_text: str,
    session_id: Optional[str],
) -> Tuple[str, str]:
    text = str(sequence_text or "").strip()
    if not text:
        raise ValueError("sequence_text must be a non-empty string.")

    max_bytes = _get_sequence_text_max_bytes()
    raw_size = len(text.encode("utf-8"))
    if raw_size > max_bytes:
        raise ValueError(
            f"sequence_text is too large ({raw_size} bytes). Maximum allowed is {max_bytes} bytes."
        )

    stripped = text.lstrip()
    if stripped.startswith(">"):
        normalized_fasta = _parse_and_normalize_fasta_text(text)
        input_origin = "sequence_text_fasta"
    else:
        normalized_seq = _validate_sequence_string(text, label="Sequence text")
        normalized_fasta = f">seq_1\n{normalized_seq}\n"
        input_origin = "sequence_text_raw"

    target_dir = _resolve_inline_input_dir(session_id)
    safe_tool = _INLINE_NAME_SAFE_RE.sub("_", tool_name).strip("_") or "tool"
    safe_operation = _INLINE_NAME_SAFE_RE.sub("_", operation).strip("_") or "operation"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    filename = f"inline_{safe_tool}_{safe_operation}_{timestamp}_{suffix}.fasta"
    target_path = (target_dir / filename).resolve()
    target_path.write_text(normalized_fasta, encoding="utf-8")
    return str(target_path), input_origin


def _build_docker_command_args(
    tool_name: str,
    operation: str,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
    work_dir_override: Optional[str] = None,
    validate_input_exists: bool = True,
    run_as_user: Optional[Tuple[int, int]] = None,
) -> List[str]:
    """Build docker command as argv list."""
    config = get_tools_config()

    if tool_name not in config:
        raise ValueError(f"Unknown tool: {tool_name}")

    tool_config = config[tool_name]
    operations = tool_config.get("operations", {})

    if operation not in operations:
        available_ops = list(operations.keys())
        raise ValueError(f"Unknown operation '{operation}' for {tool_name}. Available: {available_ops}")

    op_config = operations[operation]
    image = tool_config["image"]
    command_template = _normalize_quoted_placeholders(op_config["command"])
    needs_container_shell = _needs_container_shell(command_template)
    validated_extra_params = _validate_and_normalize_operation_params(
        tool_name,
        operation,
        extra_params,
    )

    #  / 
    if work_dir_override:
        tool_dir_abs = str(Path(work_dir_override).expanduser())
    else:
        tool_dir = ensure_tool_directory(tool_name)
        tool_dir_abs = str(tool_dir.resolve())

    mounts = [f"{tool_dir_abs}:/work"]

    # ，
    if input_file:
        input_path = Path(input_file)
        # 
        if not input_path.is_absolute():
            input_path = input_path.resolve()

        if input_path.exists():
            input_dir_abs = str(input_path.parent.resolve())
            mounts.append(f"{input_dir_abs}:/input:ro")
            input_file = f"/input/{input_path.name}"
        elif validate_input_exists:
            logger.warning(f"Input file not found: {input_path}")

    #  checkv，
    if tool_name == "checkv":
        # CheckV  ()
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5"
        mounts.append(f"{db_path}:/work/database")

    #  genomad，
    if tool_name == "genomad":
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db"
        mounts.append(f"{db_path}:/work/database")

    #  virsorter2，
    if tool_name == "virsorter2":
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db"
        mounts.append(f"{db_path}:/work/database")

    #  iphop，
    if tool_name == "iphop":
        db_path = os.getenv(
            "BIO_TOOLS_IPHOP_DB_PATH",
            "/home/zczhao/GAgent/data/databases/bio_tools/iphop/Aug_2023_pub_rw",
        )
        mounts.append(f"{db_path}:/work/database")

    #  checkm，
    if tool_name == "checkm":
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/checkm_data"
        mounts.append(f"{db_path}:/work/database")

    #  gtdbtk，
    if tool_name == "gtdbtk":
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data"
        mounts.append(f"{db_path}:/work/database")

    # 
    params = {
        "input": input_file or "",
        "output": output_file or "",
        "output_dir": "/work",
    }

    # 
    if validated_extra_params:
        params.update(validated_extra_params)

    required_fields = _template_fields(command_template)
    if "query" in required_fields and not params.get("query") and params.get("input"):
        params["query"] = params["input"]
    if "output" in required_fields and not params.get("output"):
        params["output"] = f"{tool_name}_{operation}.out"
    if "output_dir" in required_fields and not params.get("output_dir"):
        params["output_dir"] = "/work"

    #  db ，
    db_path = params.get('db')
    if db_path:
        db_path_obj = Path(db_path)
        # ，
        if db_path_obj.is_absolute() and db_path_obj.exists():
            db_dir_abs = str(db_path_obj.parent.resolve())
            # 
            mount_exists = any(m.startswith(f"{db_dir_abs}:") for m in mounts)
            if not mount_exists:
                mounts.append(f"{db_dir_abs}:/db:ro")
                # 
                params['db'] = f"/db/{db_path_obj.name}"
        elif not db_path_obj.is_absolute():
            # ， tool_dir 
            params['db'] = f"/work/{db_path}"

    #  bakta，
    if tool_name == "bakta":
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/bakta/db/db"
        mounts.append(f"{db_path}:/work/database")

    #  minimap2 filter，
    if tool_name == "minimap2" and operation == "filter":
        ref_path = params.get('reference', '')
        if ref_path.endswith('.mmi'):
            ref_dir = str(Path(ref_path).parent.resolve())
            mounts.append(f"{ref_dir}:/work/reference:ro")

    # Avoid duplicated /work prefix when templates already include /work/{param}
    for key, value in list(params.items()):
        if not isinstance(value, str):
            continue
        if value.startswith("/work/") and f"/work/{{{key}}}" in command_template:
            params[key] = value[len("/work/") :]

    missing_fields = [name for name in required_fields if params.get(name) in ("", None)]
    if missing_fields:
        raise ValueError(
            f"Missing required parameters for {tool_name}.{operation}: {', '.join(sorted(missing_fields))}"
        )

    safe_params: Dict[str, str] = {}
    for key, value in params.items():
        safe_params[key] = _sanitize_param_value(
            key,
            value,
            path_like=(key in INPUT_PATH_PARAM_KEYS or key in OUTPUT_PATH_PARAM_KEYS),
            multi_path=(key in MULTI_PATH_PARAM_KEYS),
        )

    # 
    container_command = _render_safe_command(command_template, safe_params)
    container_args: List[str]
    if needs_container_shell:
        container_args = ["sh", "-lc", container_command]
    else:
        container_args = shlex.split(container_command)

    #  Docker 
    env_values = ["HOME=/work"]
    if tool_name == "nextflow":
        nextflow_home = os.getenv("BIO_TOOLS_NEXTFLOW_HOME_IN_CONTAINER", "/tmp/.nextflow").strip() or "/tmp/.nextflow"
        env_values.extend(
            [
                f"NXF_HOME={nextflow_home}",
                f"NXF_ASSETS={nextflow_home}/assets",
            ]
        )
    if tool_name == "virsorter2":
        env_values.extend(
            [
                "SNAKEMAKE_CONDA_PREFIX=/work/output/conda_envs",
                "CONDA_PKGS_DIRS=/work/output/.conda/pkgs",
            ]
        )

    if run_as_user is not None:
        uid, gid = int(run_as_user[0]), int(run_as_user[1])
    else:
        uid = os.getuid()
        gid = os.getgid()
    docker_args: List[str] = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{uid}:{gid}",
    ]
    for env_value in env_values:
        docker_args.extend(["-e", env_value])
    for mount in mounts:
        docker_args.extend(["-v", mount])
    docker_args.extend(["-w", "/work", image])
    docker_args.extend(container_args)
    return docker_args


def build_docker_command(
    tool_name: str,
    operation: str,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
    work_dir_override: Optional[str] = None,
    validate_input_exists: bool = True,
    run_as_user: Optional[Tuple[int, int]] = None,
) -> str:
    docker_args = _build_docker_command_args(
        tool_name=tool_name,
        operation=operation,
        input_file=input_file,
        output_file=output_file,
        extra_params=extra_params,
        work_dir_override=work_dir_override,
        validate_input_exists=validate_input_exists,
        run_as_user=run_as_user,
    )
    return shlex.join(docker_args)


async def execute_docker_command(
    command: Union[str, Sequence[str]],
    timeout: Optional[int] = DEFAULT_TIMEOUT,
    capture_output: bool = True,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Execute a Docker command.

    When *log_callback* is provided the process's stdout and stderr are read
    line-by-line as they arrive and each non-empty line is forwarded to the
    callback as ``log_callback(line: str, stream: str)`` where *stream* is
    ``"stdout"`` or ``"stderr"``.  The full combined output is still returned
    in the result dict so that callers that rely on it continue to work.
    """
    use_shell = False
    shell_command: Optional[str] = None
    exec_args: Optional[List[str]] = None
    if isinstance(command, str):
        shell_command = command
        try:
            exec_args = shlex.split(command)
        except ValueError:
            exec_args = None
            use_shell = True
    else:
        exec_args = [str(arg) for arg in command]
    if exec_args is not None and not exec_args:
        return {"success": False, "error": "Command is empty", "command": ""}

    command_display = shell_command or (shlex.join(exec_args) if exec_args else "")
    logger.info(f"Executing: {command_display}")
    start_time = datetime.now()

    try:
        if use_shell:
            logger.warning("Using deprecated shell execution path for docker command")
            process = await asyncio.create_subprocess_shell(
                shell_command or "",
                stdout=asyncio.subprocess.PIPE if capture_output else None,
                stderr=asyncio.subprocess.PIPE if capture_output else None,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *(exec_args or []),
                stdout=asyncio.subprocess.PIPE if capture_output else None,
                stderr=asyncio.subprocess.PIPE if capture_output else None,
            )

        if log_callback and capture_output:
            # ── Streaming mode: read lines as they arrive ────────────────────
            stdout_lines: List[str] = []
            stderr_lines: List[str] = []

            async def _drain(stream: Optional[asyncio.StreamReader], chunks: List[str], label: str) -> None:
                if stream is None:
                    return
                while True:
                    try:
                        raw = await stream.readline()
                    except Exception:
                        break
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    chunks.append(line)
                    if line.strip():
                        try:
                            log_callback(line, label)
                        except Exception:
                            pass

            drain_coro = asyncio.gather(
                _drain(process.stdout, stdout_lines, "stdout"),
                _drain(process.stderr, stderr_lines, "stderr"),
            )

            timed_out = False
            try:
                if timeout is not None:
                    await asyncio.wait_for(drain_coro, timeout=timeout)
                else:
                    await drain_coro
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    await process.wait()
                except Exception:
                    pass

            if not timed_out:
                await process.wait()

            duration = (datetime.now() - start_time).total_seconds()

            if timed_out:
                logger.error(f"Command timed out after {timeout}s: {command_display}")
                return {
                    "success": False,
                    "error": f"Command timed out after {timeout} seconds",
                    "exit_code": -1,
                    "duration_seconds": duration,
                    "command": command_display,
                    "stdout": "\n".join(stdout_lines),
                    "stderr": "\n".join(stderr_lines),
                }

            result: Dict[str, Any] = {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "duration_seconds": duration,
                "command": command_display,
                "stdout": "\n".join(stdout_lines),
                "stderr": "\n".join(stderr_lines),
            }

        else:
            # ── Original blocking mode ────────────────────────────────────────
            if timeout is None:
                stdout, stderr = await process.communicate()
            else:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )

            duration = (datetime.now() - start_time).total_seconds()
            result = {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "duration_seconds": duration,
                "command": command_display,
            }
            if capture_output:
                result["stdout"] = stdout.decode("utf-8", errors="replace") if stdout else ""
                result["stderr"] = stderr.decode("utf-8", errors="replace") if stderr else ""

        if process.returncode != 0:
            logger.warning(
                f"Command failed with exit code {process.returncode}: "
                f"{result.get('stderr', '')[:500]}"
            )

        return result

    except asyncio.TimeoutError:
        logger.error(f"Command timed out after {timeout}s: {command_display}")
        return {
            "success": False,
            "error": f"Command timed out after {timeout} seconds",
            "command": command_display,
        }
    except Exception as e:
        logger.exception(f"Command execution failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "command": command_display,
        }


def _normalize_execution_mode(value: Optional[str]) -> str:
    mode = (value or DEFAULT_EXECUTION_MODE).strip().lower()
    if mode not in {"local", "remote", "auto"}:
        return DEFAULT_EXECUTION_MODE
    return mode


def _effective_execution_mode(config: RemoteExecutionConfig) -> str:
    configured = _normalize_execution_mode(os.getenv("BIO_TOOLS_EXECUTION_MODE"))
    if configured in {"local", "remote"}:
        return configured

    # auto mode: use remote only when minimal remote config is available
    if not config.missing_required():
        return "remote"
    return "local"


def _is_definitely_local_missing(path_value: str) -> bool:
    p = (path_value or "").strip()
    if not p:
        return False
    # macOS absolute paths are highly likely local user paths.
    if p.startswith("./") or p.startswith("../"):
        return True
    if p.startswith("~"):
        return True
    if p.startswith("/Users/") or p.startswith("/Volumes/"):
        return True
    if not p.startswith("/"):
        # Plain tokens like database/index prefixes are not filesystem paths.
        if "/" not in p and "\\" not in p:
            return False
        return True
    return False


def _normalize_output_fragment(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "output"
    text = text.replace("\\", "/")
    if text.startswith("/work/"):
        text = text[len("/work/") :]
    text = text.lstrip("/")
    if text == "output" or text.startswith("output/"):
        return text
    return f"output/{text}"


def _allocate_upload_name(
    local_path: str,
    *,
    used_names: set,
    index: int,
) -> str:
    base = Path(local_path).name
    if not base:
        base = f"input_{index}"
    candidate = base
    if candidate in used_names:
        candidate = f"{index}_{base}"
    used_names.add(candidate)
    return candidate


def _register_upload(
    local_path: str,
    remote_run_dir: str,
    uploads: List[Tuple[str, str]],
    used_names: set,
) -> str:
    local_abs = str(Path(local_path).expanduser().resolve())
    for existing_local, existing_remote in uploads:
        if existing_local == local_abs:
            return f"/work/input/{Path(existing_remote).name}"
    idx = len(uploads) + 1
    upload_name = _allocate_upload_name(local_abs, used_names=used_names, index=idx)
    remote_target = f"{remote_run_dir}/input/{upload_name}"
    uploads.append((local_abs, remote_target))
    return f"/work/input/{upload_name}"


def _register_hmmer_sidecars(
    *,
    db_path: Path,
    remote_run_dir: str,
    uploads: List[Tuple[str, str]],
    used_upload_names: set,
) -> None:
    # hmmscan expects hmmpress-generated sidecars next to the DB path.
    sidecar_suffixes = (".h3f", ".h3i", ".h3m", ".h3p")
    for suffix in sidecar_suffixes:
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists() and sidecar.is_file():
            _register_upload(
                str(sidecar),
                remote_run_dir,
                uploads,
                used_upload_names,
            )


def _register_bam_index_sidecars(
    *,
    bam_path: Path,
    remote_run_dir: str,
    uploads: List[Tuple[str, str]],
    used_upload_names: set,
) -> None:
    # Sniffles2 and other BAM consumers expect index sidecars near the BAM path.
    candidates = [
        bam_path.with_name(bam_path.name + ".bai"),
        bam_path.with_name(bam_path.name + ".csi"),
        bam_path.with_suffix(".bai"),
        bam_path.with_suffix(".csi"),
    ]
    seen: set = set()
    for sidecar in candidates:
        sidecar_str = str(sidecar.resolve()) if sidecar.exists() else str(sidecar)
        if sidecar_str in seen:
            continue
        seen.add(sidecar_str)
        if sidecar.exists() and sidecar.is_file():
            _register_upload(
                str(sidecar),
                remote_run_dir,
                uploads,
                used_upload_names,
            )


def _rewrite_remote_path_value(
    *,
    key: str,
    value: str,
    remote_run_dir: str,
    uploads: List[Tuple[str, str]],
    used_upload_names: set,
) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw

    parts = [p.strip() for p in raw.split(",")] if key in MULTI_PATH_PARAM_KEYS else [raw]
    rewritten_parts: List[str] = []
    for part in parts:
        if not part:
            continue
        path_obj = Path(part).expanduser()
        if path_obj.exists() and path_obj.is_file():
            resolved_path = path_obj.resolve()
            rewritten = _register_upload(
                str(resolved_path),
                remote_run_dir,
                uploads,
                used_upload_names,
            )
            if resolved_path.suffix.lower() == ".bam":
                _register_bam_index_sidecars(
                    bam_path=resolved_path,
                    remote_run_dir=remote_run_dir,
                    uploads=uploads,
                    used_upload_names=used_upload_names,
                )
            if key == "db":
                _register_hmmer_sidecars(
                    db_path=resolved_path,
                    remote_run_dir=remote_run_dir,
                    uploads=uploads,
                    used_upload_names=used_upload_names,
                )
            rewritten_parts.append(rewritten)
            continue
        if _is_definitely_local_missing(part):
            raise ValueError(f"Local input path not found: {part}")
        rewritten_parts.append(part)

    if key in MULTI_PATH_PARAM_KEYS:
        return ",".join(rewritten_parts)
    return rewritten_parts[0] if rewritten_parts else raw


def _prepare_remote_io(
    *,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    remote_run_dir: str,
) -> Tuple[Optional[str], Optional[str], Dict[str, Any], List[Tuple[str, str]]]:
    uploads: List[Tuple[str, str]] = []
    used_upload_names: set = set()
    rewritten_params: Dict[str, Any] = dict(params or {})

    rewritten_input = input_file
    if input_file:
        rewritten_input = _rewrite_remote_path_value(
            key="input",
            value=input_file,
            remote_run_dir=remote_run_dir,
            uploads=uploads,
            used_upload_names=used_upload_names,
        )

    rewritten_output_file = output_file
    if output_file:
        rewritten_output_file = _normalize_output_fragment(output_file)

    for key, value in list(rewritten_params.items()):
        if not isinstance(value, str):
            continue
        if key in INPUT_PATH_PARAM_KEYS:
            rewritten_params[key] = _rewrite_remote_path_value(
                key=key,
                value=value,
                remote_run_dir=remote_run_dir,
                uploads=uploads,
                used_upload_names=used_upload_names,
            )
        elif key in OUTPUT_PATH_PARAM_KEYS:
            rewritten_params[key] = _normalize_output_fragment(value)

    return rewritten_input, rewritten_output_file, rewritten_params, uploads


def _apply_execution_metadata(result: Dict[str, Any], *, mode: str, host: str) -> Dict[str, Any]:
    result["execution_mode"] = mode
    result["execution_host"] = host
    return result


async def _execute_local_bio_tool(
    *,
    tool_name: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    docker_args = _build_docker_command_args(
        tool_name=tool_name,
        operation=operation,
        input_file=input_file,
        output_file=output_file,
        extra_params=params,
    )
    result = await execute_docker_command(docker_args, timeout=timeout, log_callback=log_callback)
    result["command"] = shlex.join(docker_args)
    result["tool"] = tool_name
    result["operation"] = operation
    if output_file and result.get("success"):
        tool_dir = ensure_tool_directory(tool_name)
        result["output_path"] = str(tool_dir / output_file)
    return _apply_execution_metadata(result, mode="local", host="local")


async def _execute_remote_bio_tool(
    *,
    tool_name: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
    remote_config: RemoteExecutionConfig,
) -> Dict[str, Any]:
    missing = remote_config.missing_required()
    if missing:
        return {
            "success": False,
            "tool": tool_name,
            "operation": operation,
            "execution_mode": "remote",
            "execution_host": remote_config.host or "unknown",
            "error": "Remote execution configuration incomplete",
            "missing_configuration": missing,
        }

    run_id = uuid.uuid4().hex[:12]
    remote_run_dir = f"{remote_config.runtime_dir}/_runs/{tool_name}/{run_id}"
    local_artifact_dir = (
        Path(remote_config.local_artifact_root).expanduser() / tool_name / run_id
    )

    try:
        rewritten_input, rewritten_output, rewritten_params, uploads = _prepare_remote_io(
            input_file=input_file,
            output_file=output_file,
            params=params,
            remote_run_dir=remote_run_dir,
        )
    except ValueError as exc:
        return {
            "success": False,
            "tool": tool_name,
            "operation": operation,
            "execution_mode": "remote",
            "execution_host": remote_config.host,
            "run_id": run_id,
            "remote_run_dir": remote_run_dir,
            "local_artifact_dir": str(local_artifact_dir),
            "error": str(exc),
        }

    try:
        auth = await resolve_auth(remote_config)
    except Exception as exc:
        return {
            "success": False,
            "tool": tool_name,
            "operation": operation,
            "execution_mode": "remote",
            "execution_host": remote_config.host,
            "run_id": run_id,
            "remote_run_dir": remote_run_dir,
            "local_artifact_dir": str(local_artifact_dir),
            "error": f"Remote authentication failed: {exc}",
        }

    try:
        remote_uid, remote_gid = await resolve_remote_uid_gid(remote_config, auth)
    except Exception as exc:
        return {
            "success": False,
            "tool": tool_name,
            "operation": operation,
            "execution_mode": "remote",
            "execution_host": remote_config.host,
            "run_id": run_id,
            "remote_run_dir": remote_run_dir,
            "local_artifact_dir": str(local_artifact_dir),
            "error": f"Failed to resolve remote uid/gid: {exc}",
        }

    mk_result = await create_remote_run_dirs(remote_config, auth, remote_run_dir)
    if not mk_result.get("success"):
        return {
            "success": False,
            "tool": tool_name,
            "operation": operation,
            "execution_mode": "remote",
            "execution_host": remote_config.host,
            "run_id": run_id,
            "remote_run_dir": remote_run_dir,
            "local_artifact_dir": str(local_artifact_dir),
            "error": mk_result.get("stderr") or mk_result.get("error") or "Failed to create remote run directory",
        }

    upload_result = await upload_files(remote_config, auth, uploads)
    failed_upload = next((item for item in upload_result if not item.get("success")), None)
    if failed_upload:
        return {
            "success": False,
            "tool": tool_name,
            "operation": operation,
            "execution_mode": "remote",
            "execution_host": remote_config.host,
            "run_id": run_id,
            "remote_run_dir": remote_run_dir,
            "local_artifact_dir": str(local_artifact_dir),
            "uploaded_files": upload_result,
            "error": failed_upload.get("stderr")
            or failed_upload.get("error")
            or f"Failed to upload file: {failed_upload.get('local_path', 'unknown')}",
        }

    docker_args = _build_docker_command_args(
        tool_name=tool_name,
        operation=operation,
        input_file=rewritten_input,
        output_file=rewritten_output,
        extra_params=rewritten_params,
        work_dir_override=remote_run_dir,
        validate_input_exists=False,
        run_as_user=(remote_uid, remote_gid),
    )
    docker_cmd = shlex.join(docker_args)

    command_result = await execute_remote_command(
        remote_config,
        auth,
        docker_args,
        timeout=timeout,
    )

    sync_result = await download_remote_run_dir(
        remote_config,
        auth,
        remote_run_dir=remote_run_dir,
        local_target_dir=str(local_artifact_dir),
    )

    result: Dict[str, Any] = {
        "success": bool(command_result.get("success")),
        "tool": tool_name,
        "operation": operation,
        "command": docker_cmd,
        "run_id": run_id,
        "remote_run_dir": remote_run_dir,
        "local_artifact_dir": str(local_artifact_dir),
        "remote_uid": remote_uid,
        "remote_gid": remote_gid,
        "uploaded_files": upload_result,
        "sync_result": sync_result,
    }
    for key in ("stdout", "stderr", "exit_code", "duration_seconds", "error"):
        if key in command_result:
            result[key] = command_result[key]
    if rewritten_output:
        result["output_path"] = str(local_artifact_dir / rewritten_output)
    if not sync_result.get("success"):
        result["sync_warning"] = sync_result.get("stderr") or sync_result.get("error") or "Failed to sync remote artifacts"
    if command_result.get("sudo_retry"):
        result["sudo_retry"] = True
    result = _apply_execution_metadata(result, mode="remote", host=remote_config.host)
    return result


async def _execute_bio_tool_once(
    *,
    tool_name: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    remote_config = RemoteExecutionConfig.from_env()
    requested_mode = _normalize_execution_mode(os.getenv("BIO_TOOLS_EXECUTION_MODE"))
    execution_mode = _effective_execution_mode(remote_config)

    if execution_mode == "remote":
        remote_result = await _execute_remote_bio_tool(
            tool_name=tool_name,
            operation=operation,
            input_file=input_file,
            output_file=output_file,
            params=params,
            timeout=timeout,
            remote_config=remote_config,
        )
        if requested_mode == "auto" and not remote_result.get("success"):
            local_result = await _execute_local_bio_tool(
                tool_name=tool_name,
                operation=operation,
                input_file=input_file,
                output_file=output_file,
                params=params,
                timeout=timeout,
                log_callback=log_callback,
            )
            local_result["remote_fallback"] = True
            local_result["remote_fallback_error"] = remote_result.get(
                "error", "remote execution failed"
            )
            return local_result
        return remote_result

    return await _execute_local_bio_tool(
        tool_name=tool_name,
        operation=operation,
        input_file=input_file,
        output_file=output_file,
        params=params,
        timeout=timeout,
        log_callback=log_callback,
    )


def _get_plan_job_manager() -> Any:
    from app.services.plans.decomposition_jobs import plan_decomposition_jobs

    return plan_decomposition_jobs


def _set_current_job_context(job_id: str) -> Any:
    from app.services.plans.decomposition_jobs import set_current_job

    return set_current_job(job_id)


def _reset_current_job_context(token: Any) -> None:
    from app.services.plans.decomposition_jobs import reset_current_job

    reset_current_job(token)


def _resolve_bio_tools_job_payload(job_id: str) -> Optional[Dict[str, Any]]:
    manager = _get_plan_job_manager()
    payload = manager.get_job_payload(job_id)
    if payload is None:
        return None
    if payload.get("job_type") != BIO_TOOLS_BACKGROUND_JOB_TYPE:
        return None
    return payload


def _build_job_status_result(job_id: str) -> Dict[str, Any]:
    job_payload = _resolve_bio_tools_job_payload(job_id)
    if job_payload is None:
        return {
            "success": False,
            "operation": JOB_STATUS_OPERATION,
            "job_id": job_id,
            "error": f"Background bio_tools job '{job_id}' not found.",
        }

    return {
        "success": True,
        "operation": JOB_STATUS_OPERATION,
        "job_id": job_id,
        "status": job_payload.get("status"),
        "job": job_payload,
    }


def _run_background_bio_tools_job(
    *,
    job_id: str,
    tool_name: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
) -> None:
    manager = _get_plan_job_manager()
    ctx_token = _set_current_job_context(job_id)

    # ── Throttled log-forwarding callback ────────────────────────────────────
    _log_buf: List[str] = []
    _log_lock = threading.Lock()
    _last_flush: List[float] = [time.monotonic()]
    _FLUSH_LINES = 20   # flush after this many buffered lines
    _FLUSH_SECS = 4.0   # or after this many seconds

    def _flush_log_buf() -> None:
        with _log_lock:
            if not _log_buf:
                return
            lines = _log_buf[:]
            _log_buf.clear()
            _last_flush[0] = time.monotonic()
        text = "\n".join(lines)
        try:
            manager.append_log(job_id, "debug", text, {"source": "docker_stream"})
        except Exception:
            pass

    def _docker_log_callback(line: str, stream: str) -> None:
        with _log_lock:
            _log_buf.append(line)
            buf_len = len(_log_buf)
            elapsed = time.monotonic() - _last_flush[0]
        if buf_len >= _FLUSH_LINES or elapsed >= _FLUSH_SECS:
            _flush_log_buf()

    # ─────────────────────────────────────────────────────────────────────────

    try:
        manager.mark_running(job_id)
        manager.append_log(
            job_id,
            "info",
            "Bio-tools background execution started.",
            {
                "tool_name": tool_name,
                "operation": operation,
                "timeout_seconds": timeout,
                "timeout_disabled": timeout is None,
            },
        )

        result = asyncio.run(
            _execute_bio_tool_once(
                tool_name=tool_name,
                operation=operation,
                input_file=input_file,
                output_file=output_file,
                params=params,
                timeout=timeout,
                log_callback=_docker_log_callback,
            )
        )
        # Flush any remaining buffered lines before processing result
        _flush_log_buf()
        stats = {
            "tool_progress": {
                "tool": "bio_tools",
                "tool_name": tool_name,
                "operation": operation,
                "phase": "done" if result.get("success") else "failed",
                "run_id": result.get("run_id"),
            }
        }

        if result.get("success"):
            manager.mark_success(job_id, result=result, stats=stats)
            manager.append_log(
                job_id,
                "info",
                "Bio-tools background execution completed successfully.",
                {
                    "tool_name": tool_name,
                    "operation": operation,
                    "run_id": result.get("run_id"),
                },
            )
            return

        error_message = (
            str(result.get("error")).strip()
            if result.get("error") is not None
            else ""
        ) or (
            str(result.get("stderr")).strip()
            if result.get("stderr") is not None
            else ""
        ) or f"{tool_name}:{operation} execution failed."
        manager.mark_failure(job_id, error_message, result=result, stats=stats)
    except Exception as exc:  # pragma: no cover - defensive
        manager.mark_failure(
            job_id,
            f"Background bio_tools execution crashed: {exc}",
            result={"success": False, "error": str(exc)},
        )
    finally:
        _reset_current_job_context(ctx_token)


def _submit_background_bio_tools_job(
    *,
    tool_name: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
) -> Dict[str, Any]:
    manager = _get_plan_job_manager()
    job_id = f"bio_{uuid.uuid4().hex}"
    safe_params = dict(params or {})
    manager.create_job(
        plan_id=None,
        task_id=None,
        mode=BIO_TOOLS_BACKGROUND_MODE,
        job_type=BIO_TOOLS_BACKGROUND_JOB_TYPE,
        params={
            "tool_name": tool_name,
            "operation": operation,
            "input_file": input_file,
            "output_file": output_file,
            "params": safe_params,
            "timeout": timeout,
        },
        metadata={
            "tool_name": tool_name,
            "operation": operation,
            "timeout_seconds": timeout,
            "timeout_disabled": timeout is None,
        },
        job_id=job_id,
    )
    manager.append_log(
        job_id,
        "info",
        "Bio-tools background job queued.",
        {"tool_name": tool_name, "operation": operation},
    )

    thread = threading.Thread(
        target=_run_background_bio_tools_job,
        kwargs={
            "job_id": job_id,
            "tool_name": tool_name,
            "operation": operation,
            "input_file": input_file,
            "output_file": output_file,
            "params": safe_params,
            "timeout": timeout,
        },
        daemon=True,
    )
    thread.start()

    return {
        "success": True,
        "background": True,
        "job_id": job_id,
        "status": "queued",
        "tool": tool_name,
        "operation": operation,
        "timeout_seconds": timeout,
        "timeout_disabled": timeout is None,
        "query_hint": (
            "Use bio_tools with operation='job_status' and params={'job_id': '<job_id>'} to query status."
        ),
    }


async def bio_tools_handler(
    tool_name: str,
    operation: str = "help",
    input_file: Optional[str] = None,
    sequence_text: Optional[str] = None,
    output_file: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = DEFAULT_TIMEOUT,
    background: Optional[bool] = None,
    job_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    
    
    Args:
        tool_name:  ( "seqkit", "blast", "prodigal")
        operation:  ( "stats", "blastn", "predict")
        input_file: 
        output_file: （）
        params: 
        timeout: （）
    
    Returns:
        
    """
    logger.info(f"Bio tools handler called: tool={tool_name}, operation={operation}")
    clean_params, run_in_background, requested_job_id = _extract_control_flags(
        params=params,
        background=background,
        job_id=job_id,
    )
    effective_timeout = _normalize_handler_timeout(timeout)
    
    # ：
    if tool_name == "list" or operation == "list":
        tools = get_available_bio_tools()
        return {
            "success": True,
            "operation": "list",
            "tools": tools,
            "count": len(tools),
        }

    if operation == JOB_STATUS_OPERATION:
        if not requested_job_id:
            return _build_bio_tools_error(
                tool_name=tool_name,
                operation=JOB_STATUS_OPERATION,
                error="job_id is required for operation='job_status'.",
                error_code="missing_job_id",
            )
        return _build_job_status_result(requested_job_id)
    
    # ：
    if operation == "help":
        config = get_tools_config()
        if tool_name not in config:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(config.keys()),
            }
        
        tool_config = config[tool_name]
        operations_detail = {}
        for op, info in tool_config.get("operations", {}).items():
            raw_params = info.get("extra_params", [])
            # 兼容旧格式（列表）和新格式（字典）
            if isinstance(raw_params, list):
                params_detail = {
                    k: {"type": "string", "required": True, "description": ""}
                    for k in raw_params
                }
            else:
                params_detail = raw_params
            operations_detail[op] = {
                "description": info.get("description", ""),
                "params": params_detail,
                "command_template": info.get("command", ""),
                "notes": info.get("notes", ""),
            }

        return {
            "success": True,
            "tool": tool_name,
            "description": tool_config.get("description", ""),
            "notes": tool_config.get("notes", ""),
            "image": tool_config.get("image", ""),
            "operations": operations_detail,
        }
    
    # 
    config = get_tools_config()
    if tool_name not in config:
        return {
            "success": False,
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(config.keys()),
        }

    operations = config[tool_name].get("operations", {})
    if operation not in operations:
        return _build_bio_tools_error(
            tool_name=tool_name,
            operation=operation,
            error=f"Unknown operation '{operation}' for {tool_name}. Available: {list(operations.keys())}",
            error_code="unknown_operation",
        )

    normalized_input_file = str(input_file).strip() if isinstance(input_file, str) else ""
    has_input_file = bool(normalized_input_file)
    normalized_sequence_text = (
        str(sequence_text).strip() if isinstance(sequence_text, str) else ""
    )
    has_sequence_text = bool(normalized_sequence_text)
    generated_input_file: Optional[str] = None
    input_origin: Optional[str] = None

    if has_input_file and has_sequence_text:
        return _build_bio_tools_error(
            tool_name=tool_name,
            operation=operation,
            error="Provide either input_file or sequence_text, not both.",
            error_code="sequence_input_ambiguous",
            error_stage="input_preparation",
            no_claude_fallback=True,
        )

    if has_sequence_text:
        try:
            generated_input_file, input_origin = _prepare_sequence_input(
                tool_name=tool_name,
                operation=operation,
                sequence_text=normalized_sequence_text,
                session_id=session_id,
            )
            normalized_input_file = generated_input_file
        except ValueError as exc:
            return _build_bio_tools_error(
                tool_name=tool_name,
                operation=operation,
                error=str(exc),
                error_code="invalid_sequence_text",
                error_stage="input_preparation",
                no_claude_fallback=True,
            )
    elif has_input_file:
        normalized_input_file = normalized_input_file

    if _operation_requires_input(tool_name, operation) and not normalized_input_file:
        return _build_bio_tools_error(
            tool_name=tool_name,
            operation=operation,
            error=(
                f"{tool_name}.{operation} requires input data. "
                "Provide input_file or sequence_text."
            ),
            error_code="missing_input_file",
            error_stage="input_preparation",
        )
    
    try:
        validated_params = _validate_and_normalize_operation_params(
            tool_name,
            operation,
            clean_params,
        )
        result: Dict[str, Any]
        if run_in_background:
            result = _submit_background_bio_tools_job(
                tool_name=tool_name,
                operation=operation,
                input_file=normalized_input_file or None,
                output_file=output_file,
                params=validated_params,
                timeout=effective_timeout,
            )
        else:
            result = await _execute_bio_tool_once(
                tool_name=tool_name,
                operation=operation,
                input_file=normalized_input_file or None,
                output_file=output_file,
                params=validated_params,
                timeout=effective_timeout,
            )

        if input_origin:
            result.setdefault("input_origin", input_origin)
        if generated_input_file:
            result.setdefault("generated_input_file", generated_input_file)
        return result
        
    except UnsafeParameterError as e:
        return _build_bio_tools_error(
            tool_name=tool_name,
            operation=operation,
            error=str(e),
            error_code="invalid_parameter",
        )
    except ValueError as e:
        return _build_bio_tools_error(
            tool_name=tool_name,
            operation=operation,
            error=str(e),
            error_code="validation_error",
        )
    except Exception as e:
        logger.exception(f"Bio tools execution failed: {e}")
        return _build_bio_tools_error(
            tool_name=tool_name,
            operation=operation,
            error=f"Execution failed: {str(e)}",
            error_code="execution_failed",
        )


# （ tool_box）
bio_tools_tool = {
    "name": "bio_tools",
    "description": """Execute bioinformatics tools in Docker containers.
    
Supports 35+ tools including:
- SeqKit: FASTA/Q sequence manipulation
- BLAST: Sequence alignment
- Prodigal: Prokaryotic gene prediction  
- HMMER: HMM-based sequence analysis
- CheckV: Viral genome quality assessment
- And many more...

Use operation="list" to see all available tools.
Use operation="help" with a tool_name to see available operations.""",
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Name of the bioinformatics tool (e.g., seqkit, blast, prodigal)"
            },
            "operation": {
                "type": "string",
                "description": (
                    "Operation to perform (e.g., stats, blastn, predict). "
                    "Use 'help' to see available operations or 'job_status' to query a background job."
                )
            },
            "input_file": {
                "type": "string",
                "description": "Path to input file (FASTA, FASTQ, etc.)"
            },
            "sequence_text": {
                "type": "string",
                "description": "Inline FASTA or raw sequence text. Use this when no input_file is available."
            },
            "output_file": {
                "type": "string",
                "description": "Name for output file (saved in tool's runtime directory)"
            },
            "params": {
                "type": "object",
                "description": "Additional parameters (e.g., database, pattern)"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 3600). Set <=0 to disable execution timeout."
            },
            "background": {
                "type": "boolean",
                "description": (
                    "If true, submit execution in background and return job_id immediately. "
                    "Recommended for long-running operations only."
                )
            },
            "job_id": {
                "type": "string",
                "description": "Background job id, mainly used with operation='job_status'."
            },
            "session_id": {
                "type": "string",
                "description": "Optional session id used for session-scoped inline sequence storage."
            }
        },
        "required": ["tool_name"]
    },
    "handler": bio_tools_handler,
    "tags": ["bioinformatics", "docker", "sequence", "genomics"],
    "examples": [
        {
            "description": "List available tools",
            "params": {"tool_name": "list"}
        },
        {
            "description": "Get SeqKit stats for a FASTA file",
            "params": {
                "tool_name": "seqkit",
                "operation": "stats",
                "input_file": "/data/sequences.fasta"
            }
        },
        {
            "description": "Run Prodigal gene prediction",
            "params": {
                "tool_name": "prodigal",
                "operation": "predict",
                "input_file": "/data/genome.fasta",
                "output_file": "genes.gff",
                "params": {"protein_output": "proteins.faa"}
            }
        }
    ]
}
