"""DeepPL lifecycle prediction tool.

Provides a native wrapper for DeepPL (DNABERT fine-tuned) inference with
session-scoped input preparation, local/remote execution modes, and optional
background execution with job-status polling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DEEPPL_DIR = _PROJECT_ROOT / "data" / "experiment_2" / "DeepPL"
_DEFAULT_LOCAL_PREDICT_SCRIPT = _DEFAULT_DEEPPL_DIR / "predict_lyso_vs_lytic.py"

_DEFAULT_TIMEOUT_SEC = 1800
_DEFAULT_EXECUTION_MODE = "local"
_DEFAULT_REMOTE_PROFILE = "gpu"
_DEFAULT_THRESH1 = 0.9
_DEFAULT_THRESH2 = 0.016
_MIN_SEQUENCE_LENGTH = 106

_DEEPPL_BACKGROUND_JOB_TYPE = "deeppl_run"
_DEEPPL_BACKGROUND_MODE = "deeppl_background"
_DEEPPL_JOB_STATUS_ACTION = "job_status"

_THRESHOLD_RE = re.compile(
    r"DeepPL\s+threshold1:\s*([0-9]*\.?[0-9]+)\s*threshold2:\s*([0-9]*\.?[0-9]+)",
    flags=re.IGNORECASE,
)
_PREDICTION_RE = re.compile(
    r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s+Predict:\s*(Lysogenic|Lytic)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


class DeepPLError(RuntimeError):
    def __init__(self, message: str, *, code: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


def _clip_text(value: Any, *, limit: int = 8000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_execution_mode(value: Optional[str]) -> str:
    raw = str(value or os.getenv("DEEPPL_EXECUTION_MODE") or _DEFAULT_EXECUTION_MODE).strip().lower()
    if raw not in {"local", "remote"}:
        raise DeepPLError(
            "execution_mode must be 'local' or 'remote'.",
            code="invalid_execution_mode",
            stage="input_validation",
        )
    return raw


def _normalize_remote_profile(value: Optional[str]) -> str:
    raw = str(value or os.getenv("DEEPPL_REMOTE_PROFILE") or _DEFAULT_REMOTE_PROFILE).strip().lower()
    if raw in {"default", "generic"}:
        return "default"
    if raw in {"gpu", "cpu"}:
        return raw
    raise DeepPLError(
        "remote_profile must be one of 'gpu', 'cpu', or 'default'.",
        code="invalid_remote_profile",
        stage="input_validation",
    )


def _normalize_timeout(timeout: Any) -> int:
    if timeout is None:
        return _DEFAULT_TIMEOUT_SEC
    try:
        parsed = int(timeout)
    except (TypeError, ValueError) as exc:
        raise DeepPLError(
            "timeout must be an integer (seconds).",
            code="invalid_timeout",
            stage="input_validation",
        ) from exc
    if parsed <= 0:
        raise DeepPLError(
            "timeout must be > 0 seconds.",
            code="invalid_timeout",
            stage="input_validation",
        )
    return parsed


def _deeppl_error_payload(
    *,
    action: str,
    error: str,
    error_code: str,
    error_stage: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": False,
        "tool": "deeppl",
        "action": action,
        "error": error,
        "error_code": error_code,
        "error_stage": error_stage,
        "no_claude_fallback": True,
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _resolve_output_root(session_id: Optional[str]) -> Path:
    if isinstance(session_id, str) and session_id.strip():
        try:
            from app.services.session_paths import get_runtime_session_dir

            session_root = get_runtime_session_dir(session_id.strip(), create=True)
            root = (session_root / "tool_outputs" / "deeppl").resolve()
            root.mkdir(parents=True, exist_ok=True)
            return root
        except Exception as exc:
            raise DeepPLError(
                f"Failed to resolve session output directory: {exc}",
                code="output_dir_unavailable",
                stage="output_preparation",
            ) from exc

    root = (_PROJECT_ROOT / "runtime" / "deeppl").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_session_relative_path(path: Path, session_id: Optional[str]) -> Optional[str]:
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        session_root = get_runtime_session_dir(session_id.strip(), create=True)
        return str(path.resolve().relative_to(session_root))
    except Exception:
        return None


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        raise DeepPLError(
            f"Failed to read input file '{path}': {exc}",
            code="input_read_failed",
            stage="input_preparation",
        ) from exc


def _extract_first_sequence(raw_text: str) -> Tuple[str, str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        raise DeepPLError(
            "Input sequence is empty.",
            code="empty_sequence",
            stage="input_preparation",
        )

    has_fasta_header = any(line.startswith(">") for line in lines)
    if not has_fasta_header:
        sample_id = "seq_1"
        seq = "".join(lines)
        return sample_id, seq

    sample_id = "seq_1"
    sequence_chunks = []
    in_first_record = False
    for line in lines:
        if line.startswith(">"):
            if not in_first_record:
                in_first_record = True
                header = line[1:].strip()
                if header:
                    sample_id = header.split()[0]
                continue
            break
        if in_first_record:
            sequence_chunks.append(line)

    if not sequence_chunks:
        raise DeepPLError(
            "No sequence content found in the first FASTA record.",
            code="empty_sequence",
            stage="input_preparation",
        )
    return sample_id, "".join(sequence_chunks)


def _normalize_sequence_text(sequence: str) -> Tuple[str, int, int]:
    compact = re.sub(r"\s+", "", sequence or "").upper()
    if not compact:
        raise DeepPLError(
            "Input sequence is empty after whitespace normalization.",
            code="empty_sequence",
            stage="input_preparation",
        )

    invalid_chars = sorted({char for char in compact if char not in {"A", "C", "T", "G", "N"}})
    if invalid_chars:
        raise DeepPLError(
            f"Sequence contains unsupported characters: {''.join(invalid_chars)}. Allowed: A/C/T/G/N.",
            code="invalid_sequence_chars",
            stage="input_preparation",
        )

    removed_n_count = compact.count("N")
    cleaned = compact.replace("N", "")
    if len(cleaned) < _MIN_SEQUENCE_LENGTH:
        raise DeepPLError(
            (
                "Sequence length is too short after removing N bases. "
                f"Need >= {_MIN_SEQUENCE_LENGTH} bases, got {len(cleaned)}."
            ),
            code="sequence_too_short",
            stage="input_preparation",
        )
    return cleaned, len(compact), removed_n_count


def _prepare_normalized_input_fasta(
    *,
    input_file: Optional[str],
    sequence_text: Optional[str],
    sample_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    has_input_file = bool(isinstance(input_file, str) and input_file.strip())
    has_sequence_text = bool(isinstance(sequence_text, str) and sequence_text.strip())
    if has_input_file and has_sequence_text:
        raise DeepPLError(
            "Provide either input_file or sequence_text, not both.",
            code="sequence_input_ambiguous",
            stage="input_preparation",
        )
    if not has_input_file and not has_sequence_text:
        raise DeepPLError(
            "predict action requires input_file or sequence_text.",
            code="missing_sequence_input",
            stage="input_preparation",
        )

    source_text = ""
    source_path: Optional[Path] = None
    if has_input_file:
        source_path = Path(str(input_file).strip()).expanduser()
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        if not source_path.exists() or not source_path.is_file():
            raise DeepPLError(
                f"input_file not found: {source_path}",
                code="input_file_not_found",
                stage="input_preparation",
            )
        source_text = _read_text_file(source_path)
    else:
        source_text = str(sequence_text or "")

    parsed_sample_id, parsed_sequence = _extract_first_sequence(source_text)
    cleaned_sequence, raw_length, removed_n = _normalize_sequence_text(parsed_sequence)

    final_sample_id = str(sample_id or parsed_sample_id or "seq_1").strip() or "seq_1"
    final_sample_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", final_sample_id)[:80] or "seq_1"

    output_root = _resolve_output_root(session_id)
    run_token = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = (output_root / run_token).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    normalized_input_path = (run_dir / f"{final_sample_id}.fasta").resolve()
    normalized_input_path.write_text(
        f">{final_sample_id}\n{cleaned_sequence}\n",
        encoding="utf-8",
    )

    return {
        "run_dir": run_dir,
        "normalized_input_path": normalized_input_path,
        "sample_id": final_sample_id,
        "input_source": "input_file" if has_input_file else "sequence_text",
        "input_file": str(source_path) if source_path is not None else None,
        "sequence_length_raw": raw_length,
        "sequence_length_actg": len(cleaned_sequence),
        "removed_n_count": removed_n,
    }


def _resolve_local_model_path(model_path: Optional[str]) -> Path:
    candidate = str(model_path or os.getenv("DEEPPL_MODEL_PATH") or "").strip()
    if not candidate:
        raise DeepPLError(
            "model_path is required for DeepPL prediction (or set DEEPPL_MODEL_PATH).",
            code="missing_model_path",
            stage="input_validation",
        )
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    if not path.exists():
        raise DeepPLError(
            f"model_path not found: {path}",
            code="model_path_not_found",
            stage="input_validation",
        )
    return path


def _resolve_local_predict_script_path(predict_script: Optional[str]) -> Path:
    candidate = str(predict_script or os.getenv("DEEPPL_PREDICT_SCRIPT") or "").strip()
    if candidate:
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = (_PROJECT_ROOT / path).resolve()
    else:
        path = _DEFAULT_LOCAL_PREDICT_SCRIPT.resolve()

    if not path.exists():
        raise DeepPLError(
            f"predict script not found: {path}",
            code="predict_script_not_found",
            stage="input_validation",
        )
    if not path.is_file():
        raise DeepPLError(
            f"predict script is not a file: {path}",
            code="predict_script_not_found",
            stage="input_validation",
        )
    return path


async def _run_local_prediction_command(
    *,
    normalized_input_path: Path,
    model_path: Optional[str],
    predict_script: Optional[str],
    python_bin: Optional[str],
    timeout_sec: int,
) -> Dict[str, Any]:
    resolved_model_path = _resolve_local_model_path(model_path)
    resolved_predict_script = _resolve_local_predict_script_path(predict_script)
    resolved_python_bin = str(python_bin or os.getenv("DEEPPL_PYTHON_BIN") or "python").strip() or "python"

    cmd = [
        resolved_python_bin,
        str(resolved_predict_script),
        "--model_path",
        str(resolved_model_path),
        "--fasta_file",
        str(normalized_input_path),
    ]

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(resolved_predict_script.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise DeepPLError(
            f"python executable not found: {resolved_python_bin}",
            code="python_not_found",
            stage="execution_start",
        ) from exc
    except Exception as exc:
        raise DeepPLError(
            f"Failed to start local DeepPL process: {exc}",
            code="execution_start_failed",
            stage="execution_start",
        ) from exc

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()

    duration = time.monotonic() - started
    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    success = (not timed_out) and process.returncode == 0

    return {
        "success": success,
        "mode": "local",
        "command": shlex.join(cmd),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "model_path": str(resolved_model_path),
        "predict_script": str(resolved_predict_script),
        "python_bin": resolved_python_bin,
    }


def _resolve_remote_string(value: Optional[str], *env_keys: str, fallback: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    for key in env_keys:
        env_val = os.getenv(key)
        if isinstance(env_val, str) and env_val.strip():
            return env_val.strip()
    return fallback


def _resolve_remote_int(value: Optional[Any], *env_keys: str, fallback: int) -> int:
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback
    for key in env_keys:
        env_val = os.getenv(key)
        if env_val is None:
            continue
        try:
            return int(env_val)
        except (TypeError, ValueError):
            continue
    return fallback


def _build_remote_env_keys(
    profile: str,
    suffix: str,
    *,
    include_bio_fallback: bool = False,
) -> List[str]:
    profile_norm = _normalize_remote_profile(profile)
    suffix_norm = str(suffix or "").strip().upper()
    keys: List[str] = []
    if profile_norm in {"gpu", "cpu"} and suffix_norm:
        keys.append(f"DEEPPL_REMOTE_{profile_norm.upper()}_{suffix_norm}")
    if suffix_norm:
        keys.append(f"DEEPPL_REMOTE_{suffix_norm}")
        if include_bio_fallback:
            keys.append(f"BIO_TOOLS_REMOTE_{suffix_norm}")
    return keys


async def _run_remote_prediction_command(
    *,
    normalized_input_path: Path,
    model_path: Optional[str],
    timeout_sec: int,
    remote_profile: Optional[str],
    remote_host: Optional[str],
    remote_user: Optional[str],
    remote_port: Optional[int],
    remote_runtime_dir: Optional[str],
    remote_project_dir: Optional[str],
    remote_predict_script: Optional[str],
    remote_python_bin: Optional[str],
    remote_password: Optional[str],
    remote_ssh_key_path: Optional[str],
) -> Dict[str, Any]:
    from tool_box.bio_tools.remote_executor import (
        RemoteExecutionConfig,
        create_remote_run_dirs,
        execute_remote_command,
        resolve_auth,
        upload_files,
    )

    base = RemoteExecutionConfig.from_env()
    profile = _normalize_remote_profile(remote_profile)
    host = _resolve_remote_string(
        remote_host,
        *_build_remote_env_keys(profile, "HOST", include_bio_fallback=True),
        fallback=base.host,
    )
    user = _resolve_remote_string(
        remote_user,
        *_build_remote_env_keys(profile, "USER", include_bio_fallback=True),
        fallback=base.user,
    )
    port = _resolve_remote_int(
        remote_port,
        *_build_remote_env_keys(profile, "PORT", include_bio_fallback=True),
        fallback=base.port,
    )
    runtime_dir = _resolve_remote_string(
        remote_runtime_dir,
        *_build_remote_env_keys(profile, "RUNTIME_DIR", include_bio_fallback=True),
        fallback=(base.runtime_dir.rstrip("/") + "/deeppl"),
    )
    local_artifact_root = _resolve_remote_string(
        None,
        *_build_remote_env_keys(profile, "LOCAL_ARTIFACT_ROOT", include_bio_fallback=True),
        fallback=base.local_artifact_root,
    )
    ssh_key_path = _resolve_remote_string(
        remote_ssh_key_path,
        *_build_remote_env_keys(profile, "SSH_KEY_PATH", include_bio_fallback=True),
        fallback=(base.ssh_key_path or ""),
    )
    password = _resolve_remote_string(
        remote_password,
        *_build_remote_env_keys(profile, "PASSWORD", include_bio_fallback=True),
        fallback=(base.password or ""),
    )

    config = RemoteExecutionConfig(
        host=host,
        user=user,
        port=port,
        runtime_dir=runtime_dir,
        local_artifact_root=local_artifact_root,
        ssh_key_path=(ssh_key_path or None),
        password=(password or None),
        sudo_policy="never",
        connect_timeout=max(
            5,
            _resolve_remote_int(
                None,
                *_build_remote_env_keys(profile, "CONNECT_TIMEOUT", include_bio_fallback=True),
                fallback=15,
            ),
        ),
        scp_retries=max(
            0,
            _resolve_remote_int(
                None,
                *_build_remote_env_keys(profile, "SCP_RETRIES", include_bio_fallback=True),
                fallback=2,
            ),
        ),
        scp_retry_delay=max(
            0.0,
            float(
                _resolve_remote_string(
                    None,
                    *_build_remote_env_keys(profile, "SCP_RETRY_DELAY", include_bio_fallback=True),
                    fallback=str(base.scp_retry_delay),
                )
            ),
        ),
    )

    missing = config.missing_required()
    if missing:
        raise DeepPLError(
            "Remote DeepPL configuration incomplete: " + ", ".join(missing),
            code="remote_config_missing",
            stage="remote_config",
        )

    remote_model_path = _resolve_remote_string(
        model_path,
        *_build_remote_env_keys(profile, "MODEL_PATH"),
        "DEEPPL_MODEL_PATH",
        fallback="",
    )
    if not remote_model_path:
        raise DeepPLError(
            "Remote mode requires model_path or DEEPPL_REMOTE_MODEL_PATH (or profile-specific MODEL_PATH).",
            code="missing_model_path",
            stage="input_validation",
        )

    remote_project = _resolve_remote_string(
        remote_project_dir,
        *_build_remote_env_keys(profile, "PROJECT_DIR"),
        fallback="/home/zczhao/GAgent/data/experiment_2/DeepPL",
    )
    remote_script = _resolve_remote_string(
        remote_predict_script,
        *_build_remote_env_keys(profile, "PREDICT_SCRIPT"),
        fallback=(remote_project.rstrip("/") + "/predict_lyso_vs_lytic.py"),
    )
    remote_python = _resolve_remote_string(
        remote_python_bin,
        *_build_remote_env_keys(profile, "PYTHON_BIN"),
        fallback="python",
    )
    remote_pythonpath = _resolve_remote_string(
        None,
        *_build_remote_env_keys(profile, "PYTHONPATH"),
        fallback="",
    )
    python_bin_dir = ""
    if "/" in remote_python:
        python_bin_dir = remote_python.rsplit("/", 1)[0].strip()
    path_export_prefix = ""
    if python_bin_dir:
        path_export_prefix = f"export PATH={shlex.quote(python_bin_dir)}:$PATH && "
    pythonpath_export_prefix = ""
    if remote_pythonpath:
        pythonpath_export_prefix = f"export PYTHONPATH={shlex.quote(remote_pythonpath)}:$PYTHONPATH && "

    auth = await resolve_auth(config)

    run_id = f"deeppl_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    remote_run_dir = f"{runtime_dir.rstrip('/')}/{run_id}"
    create_dirs_result = await create_remote_run_dirs(config, auth, remote_run_dir)
    if not create_dirs_result.get("success"):
        detail = create_dirs_result.get("stderr") or create_dirs_result.get("error") or "unknown error"
        raise DeepPLError(
            f"Failed to prepare remote run directory: {detail}",
            code="remote_prepare_failed",
            stage="remote_prepare",
        )

    remote_input_file = f"{remote_run_dir}/input/{normalized_input_path.name}"
    uploads = await upload_files(
        config,
        auth,
        [(str(normalized_input_path), remote_input_file)],
    )
    if not uploads or not uploads[0].get("success"):
        failed = uploads[0] if uploads else {}
        detail = failed.get("stderr") or failed.get("error") or "unknown error"
        raise DeepPLError(
            f"Failed to upload input file to remote host: {detail}",
            code="remote_upload_failed",
            stage="remote_prepare",
        )

    command = (
        f"cd {shlex.quote(remote_project)} && "
        f"{path_export_prefix}{pythonpath_export_prefix}{shlex.quote(remote_python)} {shlex.quote(remote_script)} "
        f"--model_path {shlex.quote(remote_model_path)} "
        f"--fasta_file {shlex.quote(remote_input_file)}"
    )
    remote_result = await execute_remote_command(config, auth, command, timeout=timeout_sec)
    success = bool(remote_result.get("success"))
    return {
        "success": success,
        "mode": "remote",
        "command": remote_result.get("command") or command,
        "stdout": str(remote_result.get("stdout") or ""),
        "stderr": str(remote_result.get("stderr") or ""),
        "exit_code": remote_result.get("exit_code"),
        "timed_out": bool(
            "timed out" in str(remote_result.get("error") or "").lower()
            or "timed out" in str(remote_result.get("stderr") or "").lower()
        ),
        "duration_seconds": remote_result.get("duration_seconds"),
        "model_path": remote_model_path,
        "predict_script": remote_script,
        "python_bin": remote_python,
        "pythonpath": remote_pythonpath or None,
        "remote_host": host,
        "remote_user": user,
        "remote_port": port,
        "remote_run_dir": remote_run_dir,
        "remote_input_file": remote_input_file,
        "auth_mode": getattr(auth, "mode", None),
        "remote_profile": profile,
    }


def _parse_prediction_output(command_result: Dict[str, Any]) -> Dict[str, Any]:
    stdout = str(command_result.get("stdout") or "")
    stderr = str(command_result.get("stderr") or "")
    combined = "\n".join(part for part in (stdout, stderr) if part)

    thresh1 = _DEFAULT_THRESH1
    thresh2 = _DEFAULT_THRESH2
    threshold_match = _THRESHOLD_RE.search(combined)
    if threshold_match:
        try:
            thresh1 = float(threshold_match.group(1))
            thresh2 = float(threshold_match.group(2))
        except (TypeError, ValueError):
            thresh1 = _DEFAULT_THRESH1
            thresh2 = _DEFAULT_THRESH2

    prediction_match = _PREDICTION_RE.search(combined)
    if not prediction_match:
        raise DeepPLError(
            "Unable to parse DeepPL prediction output.",
            code="prediction_parse_failed",
            stage="output_parsing",
        )

    positive_fraction = float(prediction_match.group(1))
    raw_label = prediction_match.group(2).strip().lower()
    if raw_label.startswith("lyso"):
        predicted_label = "lysogenic"
        predicted_lifestyle = "temperate"
    else:
        predicted_label = "lytic"
        predicted_lifestyle = "virulent"

    return {
        "positive_window_fraction": positive_fraction,
        "predicted_label": predicted_label,
        "predicted_lifestyle": predicted_lifestyle,
        "thresholds": {
            "window_score_threshold": thresh1,
            "positive_window_fraction_threshold": thresh2,
        },
    }


async def _predict_once(
    *,
    input_file: Optional[str],
    sequence_text: Optional[str],
    sample_id: Optional[str],
    execution_mode: Optional[str],
    model_path: Optional[str],
    predict_script: Optional[str],
    python_bin: Optional[str],
    timeout: Optional[int],
    session_id: Optional[str],
    remote_profile: Optional[str],
    remote_host: Optional[str],
    remote_user: Optional[str],
    remote_port: Optional[int],
    remote_runtime_dir: Optional[str],
    remote_project_dir: Optional[str],
    remote_predict_script: Optional[str],
    remote_python_bin: Optional[str],
    remote_password: Optional[str],
    remote_ssh_key_path: Optional[str],
) -> Dict[str, Any]:
    timeout_sec = _normalize_timeout(timeout)
    prepared = _prepare_normalized_input_fasta(
        input_file=input_file,
        sequence_text=sequence_text,
        sample_id=sample_id,
        session_id=session_id,
    )
    mode = _normalize_execution_mode(execution_mode)
    normalized_input_path = prepared["normalized_input_path"]

    if mode == "local":
        command_result = await _run_local_prediction_command(
            normalized_input_path=normalized_input_path,
            model_path=model_path,
            predict_script=predict_script,
            python_bin=python_bin,
            timeout_sec=timeout_sec,
        )
    else:
        command_result = await _run_remote_prediction_command(
            normalized_input_path=normalized_input_path,
            model_path=model_path,
            timeout_sec=timeout_sec,
            remote_profile=remote_profile,
            remote_host=remote_host,
            remote_user=remote_user,
            remote_port=remote_port,
            remote_runtime_dir=remote_runtime_dir,
            remote_project_dir=remote_project_dir,
            remote_predict_script=remote_predict_script,
            remote_python_bin=remote_python_bin,
            remote_password=remote_password,
            remote_ssh_key_path=remote_ssh_key_path,
        )

    if not command_result.get("success"):
        error_message = (
            str(command_result.get("stderr") or "").strip()
            or str(command_result.get("error") or "").strip()
            or "DeepPL prediction command failed."
        )
        if command_result.get("timed_out"):
            error_message = f"DeepPL prediction timed out after {timeout_sec}s."
        return _deeppl_error_payload(
            action="predict",
            error=error_message,
            error_code="prediction_command_failed",
            error_stage="execution",
            extra={
                "execution_mode": mode,
                "input_file_prepared": str(normalized_input_path),
                "stdout": _clip_text(command_result.get("stdout")),
                "stderr": _clip_text(command_result.get("stderr")),
                "command": _clip_text(command_result.get("command"), limit=1200),
                "exit_code": command_result.get("exit_code"),
                "timeout_seconds": timeout_sec,
                "duration_seconds": command_result.get("duration_seconds"),
            },
        )

    parsed = _parse_prediction_output(command_result)
    input_file_rel = _resolve_session_relative_path(normalized_input_path, session_id)
    run_dir = Path(prepared["run_dir"]).resolve()
    run_dir_rel = _resolve_session_relative_path(run_dir, session_id)

    result: Dict[str, Any] = {
        "success": True,
        "tool": "deeppl",
        "action": "predict",
        "execution_mode": mode,
        "sample_id": prepared["sample_id"],
        "predicted_label": parsed["predicted_label"],
        "predicted_lifestyle": parsed["predicted_lifestyle"],
        "positive_window_fraction": parsed["positive_window_fraction"],
        "thresholds": parsed["thresholds"],
        "input_source": prepared["input_source"],
        "input_file_original": prepared["input_file"],
        "input_file_prepared": str(normalized_input_path),
        "run_directory": str(run_dir),
        "sequence_length_raw": prepared["sequence_length_raw"],
        "sequence_length_actg": prepared["sequence_length_actg"],
        "removed_n_count": prepared["removed_n_count"],
        "command": _clip_text(command_result.get("command"), limit=1200),
        "model_path": command_result.get("model_path"),
        "predict_script": command_result.get("predict_script"),
        "python_bin": command_result.get("python_bin"),
        "pythonpath": command_result.get("pythonpath"),
        "duration_seconds": command_result.get("duration_seconds"),
        "stdout": _clip_text(command_result.get("stdout")),
        "stderr": _clip_text(command_result.get("stderr")),
    }
    if input_file_rel:
        result["input_file_prepared_rel"] = input_file_rel
    if run_dir_rel:
        result["run_directory_rel"] = run_dir_rel
    if mode == "remote":
        for key in (
            "remote_host",
            "remote_user",
            "remote_port",
            "remote_run_dir",
            "remote_input_file",
            "auth_mode",
        ):
            if command_result.get(key) is not None:
                result[key] = command_result.get(key)
        result["remote_profile"] = command_result.get("remote_profile")
    return result


def _get_plan_job_manager() -> Any:
    from app.services.plans.decomposition_jobs import plan_decomposition_jobs

    return plan_decomposition_jobs


def _set_current_job_context(job_id: str) -> Any:
    from app.services.plans.decomposition_jobs import set_current_job

    return set_current_job(job_id)


def _reset_current_job_context(token: Any) -> None:
    from app.services.plans.decomposition_jobs import reset_current_job

    reset_current_job(token)


def _resolve_deeppl_job_payload(job_id: str) -> Optional[Dict[str, Any]]:
    manager = _get_plan_job_manager()
    payload = manager.get_job_payload(job_id)
    if payload is None:
        return None
    if payload.get("job_type") != _DEEPPL_BACKGROUND_JOB_TYPE:
        return None
    return payload


def _build_deeppl_job_status(job_id: str) -> Dict[str, Any]:
    payload = _resolve_deeppl_job_payload(job_id)
    if payload is None:
        return _deeppl_error_payload(
            action=_DEEPPL_JOB_STATUS_ACTION,
            error=f"Background deeppl job '{job_id}' not found.",
            error_code="job_not_found",
            error_stage="job_lookup",
            extra={"job_id": job_id},
        )

    return {
        "success": True,
        "tool": "deeppl",
        "action": _DEEPPL_JOB_STATUS_ACTION,
        "job_id": job_id,
        "status": payload.get("status"),
        "job": payload,
    }


def _sanitize_background_params(params: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in params.items():
        lowered = str(key).lower()
        if any(token in lowered for token in ("password", "token", "secret", "key")):
            safe[key] = "<redacted>"
            continue
        if key == "sequence_text" and isinstance(value, str):
            safe["sequence_text_length"] = len(value)
            continue
        safe[key] = value
    return safe


def _run_background_deeppl_job(*, job_id: str, predict_kwargs: Dict[str, Any]) -> None:
    manager = _get_plan_job_manager()
    ctx_token = _set_current_job_context(job_id)
    try:
        manager.mark_running(job_id)
        manager.append_log(
            job_id,
            "info",
            "DeepPL background prediction started.",
            {
                "execution_mode": predict_kwargs.get("execution_mode"),
                "timeout": predict_kwargs.get("timeout"),
            },
        )
        result = asyncio.run(_predict_once(**predict_kwargs))
        stats = {
            "tool_progress": {
                "tool": "deeppl",
                "phase": "done" if result.get("success") else "failed",
                "sample_id": result.get("sample_id"),
            }
        }
        if result.get("success"):
            manager.mark_success(job_id, result=result, stats=stats)
            manager.append_log(
                job_id,
                "info",
                "DeepPL background prediction completed successfully.",
                {
                    "sample_id": result.get("sample_id"),
                    "predicted_label": result.get("predicted_label"),
                    "predicted_lifestyle": result.get("predicted_lifestyle"),
                },
            )
            return

        error_message = str(result.get("error") or "DeepPL background prediction failed.")
        manager.mark_failure(job_id, error_message, result=result, stats=stats)
    except Exception as exc:  # pragma: no cover - defensive
        manager.mark_failure(
            job_id,
            f"DeepPL background prediction crashed: {exc}",
            result={"success": False, "error": str(exc), "tool": "deeppl", "action": "predict"},
        )
    finally:
        _reset_current_job_context(ctx_token)


def _submit_background_deeppl_job(*, predict_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    manager = _get_plan_job_manager()
    job_id = f"deeppl_{uuid.uuid4().hex}"
    manager.create_job(
        plan_id=None,
        task_id=None,
        mode=_DEEPPL_BACKGROUND_MODE,
        job_type=_DEEPPL_BACKGROUND_JOB_TYPE,
        params=_sanitize_background_params(predict_kwargs),
        metadata={
            "tool": "deeppl",
            "action": "predict",
            "execution_mode": predict_kwargs.get("execution_mode"),
        },
        job_id=job_id,
    )
    manager.append_log(
        job_id,
        "info",
        "DeepPL background job queued.",
        {"execution_mode": predict_kwargs.get("execution_mode")},
    )

    thread = threading.Thread(
        target=_run_background_deeppl_job,
        kwargs={"job_id": job_id, "predict_kwargs": dict(predict_kwargs)},
        daemon=True,
    )
    thread.start()

    return {
        "success": True,
        "tool": "deeppl",
        "action": "predict",
        "background": True,
        "status": "queued",
        "job_id": job_id,
        "execution_mode": predict_kwargs.get("execution_mode"),
        "query_hint": "Use deeppl with action='job_status' and job_id to query progress.",
    }


def _help_payload() -> Dict[str, Any]:
    return {
        "success": True,
        "tool": "deeppl",
        "action": "help",
        "description": (
            "DeepPL lifecycle prediction wrapper. Supports local/remote execution, "
            "input normalization to 2-line ACTG FASTA, and optional background jobs."
        ),
        "actions": {
            "help": "Show this help payload.",
            "predict": "Run DeepPL prediction on input_file or sequence_text.",
            "job_status": "Query a background DeepPL job by job_id.",
        },
        "defaults": {
            "execution_mode": os.getenv("DEEPPL_EXECUTION_MODE", _DEFAULT_EXECUTION_MODE),
            "remote_profile": os.getenv("DEEPPL_REMOTE_PROFILE", _DEFAULT_REMOTE_PROFILE),
            "timeout_seconds": _DEFAULT_TIMEOUT_SEC,
            "threshold1": _DEFAULT_THRESH1,
            "threshold2": _DEFAULT_THRESH2,
        },
        "requirements": {
            "predict": [
                "Provide exactly one of input_file or sequence_text.",
                "Provide model_path (or set DEEPPL_MODEL_PATH / DEEPPL_REMOTE_MODEL_PATH).",
                "For split CPU/GPU servers in remote mode, set remote_profile='cpu' or 'gpu' (or DEEPPL_REMOTE_PROFILE).",
                f"Sequence must be at least {_MIN_SEQUENCE_LENGTH} bp after removing N.",
            ]
        },
        "execution_modes": {
            "local": {
                "python_env": "DeepPL/DNABERT environment required.",
                "predict_script_default": str(_DEFAULT_LOCAL_PREDICT_SCRIPT),
                "model_env": "DEEPPL_MODEL_PATH",
            },
            "remote": {
                "auth": "Uses SSH key first, password fallback (same adapter as bio_tools).",
                "core_env": [
                    "DEEPPL_REMOTE_PROFILE (gpu|cpu|default)",
                    "DEEPPL_REMOTE_GPU_HOST / DEEPPL_REMOTE_CPU_HOST",
                    "DEEPPL_REMOTE_GPU_USER / DEEPPL_REMOTE_CPU_USER",
                    "DEEPPL_REMOTE_GPU_MODEL_PATH / DEEPPL_REMOTE_CPU_MODEL_PATH",
                    "DEEPPL_REMOTE_GPU_PYTHONPATH / DEEPPL_REMOTE_CPU_PYTHONPATH (optional, e.g., DNABERT/src)",
                    "DEEPPL_REMOTE_HOST",
                    "DEEPPL_REMOTE_USER",
                    "DEEPPL_REMOTE_PORT",
                    "DEEPPL_REMOTE_MODEL_PATH",
                    "DEEPPL_REMOTE_PROJECT_DIR",
                ],
            },
        },
    }


async def deeppl_handler(
    action: str = "help",
    input_file: Optional[str] = None,
    sequence_text: Optional[str] = None,
    sample_id: Optional[str] = None,
    execution_mode: Optional[str] = None,
    model_path: Optional[str] = None,
    predict_script: Optional[str] = None,
    python_bin: Optional[str] = None,
    timeout: Optional[int] = _DEFAULT_TIMEOUT_SEC,
    background: Optional[bool] = None,
    job_id: Optional[str] = None,
    session_id: Optional[str] = None,
    remote_profile: Optional[str] = None,
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_port: Optional[int] = None,
    remote_runtime_dir: Optional[str] = None,
    remote_project_dir: Optional[str] = None,
    remote_predict_script: Optional[str] = None,
    remote_python_bin: Optional[str] = None,
    remote_password: Optional[str] = None,
    remote_ssh_key_path: Optional[str] = None,
) -> Dict[str, Any]:
    action_norm = str(action or "help").strip().lower()
    if action_norm not in {"help", "predict", _DEEPPL_JOB_STATUS_ACTION}:
        return _deeppl_error_payload(
            action=action_norm or "unknown",
            error=f"Unsupported action: {action}",
            error_code="unsupported_action",
            error_stage="input_validation",
        )

    if action_norm == "help":
        return _help_payload()

    if action_norm == _DEEPPL_JOB_STATUS_ACTION:
        requested_job_id = str(job_id or "").strip()
        if not requested_job_id:
            return _deeppl_error_payload(
                action=action_norm,
                error="job_id is required for action='job_status'.",
                error_code="missing_job_id",
                error_stage="input_validation",
            )
        return _build_deeppl_job_status(requested_job_id)

    try:
        mode = _normalize_execution_mode(execution_mode)
        run_in_background = _coerce_bool(background, default=False)
        predict_kwargs: Dict[str, Any] = {
            "input_file": input_file,
            "sequence_text": sequence_text,
            "sample_id": sample_id,
            "execution_mode": mode,
            "model_path": model_path,
            "predict_script": predict_script,
            "python_bin": python_bin,
            "timeout": timeout,
            "session_id": session_id,
            "remote_profile": remote_profile,
            "remote_host": remote_host,
            "remote_user": remote_user,
            "remote_port": remote_port,
            "remote_runtime_dir": remote_runtime_dir,
            "remote_project_dir": remote_project_dir,
            "remote_predict_script": remote_predict_script,
            "remote_python_bin": remote_python_bin,
            "remote_password": remote_password,
            "remote_ssh_key_path": remote_ssh_key_path,
        }
        if run_in_background:
            return _submit_background_deeppl_job(predict_kwargs=predict_kwargs)
        return await _predict_once(**predict_kwargs)
    except DeepPLError as exc:
        logger.warning("DeepPL predict failed at %s: %s", exc.stage, exc)
        return _deeppl_error_payload(
            action="predict",
            error=str(exc),
            error_code=exc.code,
            error_stage=exc.stage,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("DeepPL tool failed: %s", exc)
        return _deeppl_error_payload(
            action="predict",
            error=f"DeepPL internal error: {exc}",
            error_code="deeppl_internal_error",
            error_stage="internal",
        )


deeppl_tool = {
    "name": "deeppl",
    "description": (
        "DeepPL (DNABERT-based) phage lifecycle prediction. "
        "Supports local/remote inference and background job tracking."
    ),
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["help", "predict", "job_status"],
                "description": "Action to perform.",
                "default": "help",
            },
            "input_file": {
                "type": "string",
                "description": "Path to FASTA/raw sequence input file (mutually exclusive with sequence_text).",
            },
            "sequence_text": {
                "type": "string",
                "description": "Inline FASTA or raw sequence text (mutually exclusive with input_file).",
            },
            "sample_id": {
                "type": "string",
                "description": "Optional sample id used in normalized FASTA header.",
            },
            "execution_mode": {
                "type": "string",
                "enum": ["local", "remote"],
                "description": "Inference execution mode.",
                "default": _DEFAULT_EXECUTION_MODE,
            },
            "remote_profile": {
                "type": "string",
                "enum": ["gpu", "cpu", "default"],
                "description": (
                    "Remote server profile when execution_mode=remote. "
                    "Resolves profile-specific env keys first."
                ),
                "default": _DEFAULT_REMOTE_PROFILE,
            },
            "model_path": {
                "type": "string",
                "description": "Model directory path (local mode) or remote model path (remote mode).",
            },
            "predict_script": {
                "type": "string",
                "description": "Override local predict script path.",
            },
            "python_bin": {
                "type": "string",
                "description": "Override local python executable.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Prediction timeout in seconds (default {_DEFAULT_TIMEOUT_SEC}).",
                "default": _DEFAULT_TIMEOUT_SEC,
            },
            "background": {
                "type": "boolean",
                "description": "If true, run prediction in background and return job_id.",
            },
            "job_id": {
                "type": "string",
                "description": "Background job id for action='job_status'.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session id for session-scoped runtime outputs.",
            },
            "remote_host": {"type": "string", "description": "Remote host override for remote mode."},
            "remote_user": {"type": "string", "description": "Remote user override for remote mode."},
            "remote_port": {"type": "integer", "description": "Remote SSH port override for remote mode."},
            "remote_runtime_dir": {
                "type": "string",
                "description": "Remote runtime root used for staged input files.",
            },
            "remote_project_dir": {
                "type": "string",
                "description": "Remote DeepPL project directory containing prediction script.",
            },
            "remote_predict_script": {
                "type": "string",
                "description": "Remote predict script path override.",
            },
            "remote_python_bin": {
                "type": "string",
                "description": "Remote python executable override.",
            },
            "remote_password": {
                "type": "string",
                "description": "Remote SSH password override (optional; key auth preferred).",
            },
            "remote_ssh_key_path": {
                "type": "string",
                "description": "Remote SSH key path override.",
            },
        },
        "required": ["action"],
    },
    "handler": deeppl_handler,
    "tags": ["phage", "lifecycle", "dnabert", "deep-learning", "bioinformatics"],
    "examples": [
        "Get DeepPL usage instructions (action=help).",
        "Predict lifecycle from a FASTA file (action=predict, execution_mode=local).",
        "Submit remote background prediction then query job status (action=job_status).",
    ],
}
