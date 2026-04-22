"""
PhageScope API Tool

Provides access to the PhageScope phage analysis service.
"""

import asyncio
import ast
import csv
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://phageapi.deepomics.org"
_PHAGESCOPE_TASKID_RE = re.compile(r"(?<![A-Za-z0-9])(\d{4,})(?![A-Za-z0-9])")
_PHAGESCOPE_TRACKING_JOB_RE = re.compile(r"^act_[A-Za-z0-9]+$")
_PHAGESCOPE_TASKID_HINT_RE = re.compile(
    r"(?:remote[_\s-]?task[_\s-]?id|task[_\s-]?id|task)\s*[:=]?\s*['\"]?(\d{4,})",
    flags=re.IGNORECASE,
)

# batch_submit: models often omit modulelist; single-strain submit would fail with 400 otherwise.
_DEFAULT_BATCH_SUBMIT_MODULELIST: List[str] = ["quality"]

# Result endpoint map
RESULT_ENDPOINTS = {
    "phage": "/tasks/result/phage/",
    "proteins": "/tasks/result/proteins/",
    "quality": "/tasks/result/quality/",
    "modules": "/tasks/result/modules/",
    "tree": "/tasks/result/tree/",
    "phagefasta": "/tasks/result/phagefasta/",
    "phage_detail": "/tasks/result/phage/detail/",
}

# result_kind aliases used by agent prompts / user instructions.
# Value tuple: (canonical_result_kind, optional_module)
RESULT_KIND_ALIASES: Dict[str, Tuple[str, Optional[str]]] = {
    "protein": ("proteins", None),
    "phage-detail": ("phage_detail", None),
    "modules-trna": ("modules", "trna"),
    "modules_trna": ("modules", "trna"),
    "modules-anticrispr": ("modules", "anticrispr"),
    "modules_anticrispr": ("modules", "anticrispr"),
    "modules-anti_crispr": ("modules", "anticrispr"),
    "modules_anti_crispr": ("modules", "anticrispr"),
}

# Remote download path aliases that can be reconstructed from result APIs.
# These are fallback mappings used when dynamic path reconstruction fails.
DOWNLOAD_TSV_FALLBACKS: Dict[str, str] = {
    "/output/result/phage.tsv": "phage",
    "/output/result/protein.tsv": "proteins",
    "/output/result/proteins.tsv": "proteins",
}

# Mapping from result_kind to expected filename pattern for path reconstruction.
RESULT_KIND_TO_FILENAME: Dict[str, str] = {
    "phage": "phage.tsv",
    "proteins": "protein.tsv",
    "quality": "quality.tsv",
    "modules": "modules.tsv",
    "tree": "tree.nwk",
    "phagefasta": "phage.fasta",
    "phage_detail": "phage_detail.json",
}

FILENAME_TO_RESULT_KIND: Dict[str, str] = {
    filename.lower(): kind for kind, filename in RESULT_KIND_TO_FILENAME.items()
}

# Successful result/task_detail/download return JSON or a single file — not the same as save_all's folder tree.
ARTIFACT_SCOPE_API_ONLY = "api_response_only"
LOCAL_BUNDLE_HINT_EN = (
    "This call returns API/JSON (or one artifact) only. "
    "For a full local bundle (metadata/, annotation/, raw_api_responses/, etc.), use action=save_all with the same numeric taskid."
)


def _with_api_only_artifact_hint(result: Dict[str, Any], taskid: Optional[str] = None) -> Dict[str, Any]:
    """Tag non-save_all successes so agents do not equate them with a save_all disk bundle."""
    if not result.get("success"):
        return result
    action = str(result.get("action") or "").strip().lower()
    if action in {"save_all", "ping", "submit", "task_list", "input_check", "cluster_submit"}:
        return result
    if action not in {"result", "task_detail", "task_log", "download", "quality", "query"}:
        return result
    out = dict(result)
    out["artifact_scope"] = ARTIFACT_SCOPE_API_ONLY
    out["local_bundle_hint"] = LOCAL_BUNDLE_HINT_EN
    tid = taskid if taskid is not None else out.get("taskid")
    if tid:
        out["taskid"] = str(tid)
    return out

# Analysis type configuration
ANALYSIS_TYPES = {
    "Annotation Pipline": {
        "endpoint": "/analyze/pipline/",
        "description": "Gene annotation pipeline",
        "modules": [
            "quality", "host", "lifestyle", "annotation", "terminator",
            "taxonomic", "trna", "anticrispr", "crispr", "arvf", "transmembrane"
        ],
    },
    "Phenotype Annotation": {
        "endpoint": "/analyze/pipline/",
        "description": "Phenotype annotation",
    },
    "Structural Annotation": {
        "endpoint": "/analyze/pipline/",
        "description": "Structural annotation",
    },
    "Functional Annotation": {
        "endpoint": "/analyze/pipline/",
        "description": "Functional annotation",
    },
    "Completeness Assessment": {
        "endpoint": "/analyze/pipline/",
        "description": "Completeness assessment",
    },
    "Host Assignment": {
        "endpoint": "/analyze/pipline/",
        "description": "Host assignment",
    },
    "Lifestyle Prediction": {
        "endpoint": "/analyze/pipline/",
        "description": "Lifestyle prediction",
    },
    "Genome Comparison": {
        "endpoint": "/analyze/clusterpipline/",
        "description": "Genome comparison (clustering, phylogenetic tree, sequence alignment)",
        "modules": ["clustering", "phylogenetic", "alignment"],
    },
}

# Module dependency rules
MODULE_DEPENDENCIES = {
    "anticrispr": ["annotation"],
    "transmembrane": ["annotation"],
    "taxonomic": ["annotation"],
    "arvf": ["annotation"],
    "terminator": ["annotation"],
}

# Clustering analysis modules
CLUSTER_MODULES = {"clustering", "phylogenetic", "alignment"}

# Result-like names that users/models sometimes place into submit modulelist.
# They are not real submit modules and must be normalized before POST.
RESULT_DERIVED_SUBMIT_MODULES: Dict[str, str] = {
    "protein": "annotation",
    "proteins": "annotation",
    "tree": "phylogenetic",
}

RESULT_ONLY_QUERY_KINDS = {"proteins", "phage_detail", "phagefasta", "modules", "tree"}

_TLS_RETRY_WARNING = (
    "TLS certificate verification failed; PhageScope request retried with verify=False."
)


def _get_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("PHAGESCOPE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _ssl_verify_enabled(base_url: str) -> bool:
    raw = str(os.getenv("PHAGESCOPE_SSL_VERIFY", "true")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _should_retry_without_ssl_verify(base_url: str, exc: Exception) -> bool:
    base = str(base_url or "").lower()
    if "phageapi.deepomics.org" not in base:
        return False
    message = str(exc or "").lower()
    return "certificate verify failed" in message or "certificateverifyfailed" in message


def _attach_transport_warning(payload: Dict[str, Any], message: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"raw": str(payload), "_transport_warnings": [message]}
    warnings = payload.get("_transport_warnings")
    if not isinstance(warnings, list):
        warnings = []
        payload["_transport_warnings"] = warnings
    if message not in warnings:
        warnings.append(message)
    return payload


def _decode_httpx_response(response: httpx.Response) -> Dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return {"raw": response.text}


async def _do_httpx_request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
    follow_redirects: bool = False,
    verify: bool = True,
) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        follow_redirects=follow_redirects,
        trust_env=False,
        verify=verify,
    ) as client:
        return await client.request(method, url, params=params, data=data, files=files)


def _normalize_phagescope_taskid(value: Any) -> Optional[str]:
    # bool is a subclass of int in Python; never treat True/False as a task id.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return text
    match = _PHAGESCOPE_TASKID_RE.search(text)
    if match:
        return match.group(1)
    return None


def _lookup_remote_taskid_by_tracking_job(
    job_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    token = str(job_id or "").strip()
    if not token:
        return None
    try:
        from app.database import get_db  # lazy import to avoid tool bootstrap cycles

        with get_db() as conn:
            row = None
            if session_id:
                row = conn.execute(
                    """
                    SELECT remote_taskid
                    FROM phagescope_tracking
                    WHERE job_id=? AND session_id=?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (token, session_id),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT remote_taskid
                    FROM phagescope_tracking
                    WHERE job_id=?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (token,),
                ).fetchone()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve PhageScope tracking alias %s: %s", token, exc)
        return None

    if not row:
        return None
    return _normalize_phagescope_taskid(row["remote_taskid"])


def _extract_taskid_from_payload(value: Any) -> Optional[str]:
    if value is None:
        return None

    normalized = _normalize_phagescope_taskid(value)
    if normalized:
        return normalized

    if isinstance(value, dict):
        for key in ("taskid", "task_id", "remote_taskid", "remote_task_id"):
            candidate = value.get(key)
            normalized = _normalize_phagescope_taskid(candidate)
            if normalized:
                return normalized
        for nested in value.values():
            nested_taskid = _extract_taskid_from_payload(nested)
            if nested_taskid:
                return nested_taskid
        return None

    if isinstance(value, list):
        for item in value:
            nested_taskid = _extract_taskid_from_payload(item)
            if nested_taskid:
                return nested_taskid
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        hint_match = _PHAGESCOPE_TASKID_HINT_RE.search(text)
        if hint_match:
            return hint_match.group(1)
    return None


def _lookup_remote_taskid_by_action_run(
    run_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    token = str(run_id or "").strip()
    if not token:
        return None
    try:
        from app.database import get_db  # lazy import to avoid tool bootstrap cycles

        with get_db() as conn:
            row = None
            if session_id:
                row = conn.execute(
                    """
                    SELECT id, session_id, user_message, context_json, structured_json, result_json
                    FROM chat_action_runs
                    WHERE id=? AND session_id=?
                    LIMIT 1
                    """,
                    (token, session_id),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT id, session_id, user_message, context_json, structured_json, result_json
                    FROM chat_action_runs
                    WHERE id=?
                    LIMIT 1
                    """,
                    (token,),
                ).fetchone()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to resolve action run alias %s: %s", token, exc)
        return None

    if row is None:
        return None

    for field in ("result_json", "context_json", "structured_json"):
        raw = row[field]
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        taskid = _extract_taskid_from_payload(parsed)
        if taskid:
            return taskid

    user_message = row["user_message"]
    if isinstance(user_message, str) and user_message.strip():
        taskid = _extract_taskid_from_payload(user_message)
        if taskid:
            return taskid
    return None


def _resolve_phagescope_taskid(
    taskid: Any,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    normalized = _normalize_phagescope_taskid(taskid)
    if normalized:
        return normalized

    task_text = str(taskid or "").strip()
    if not task_text:
        return None
    if not _PHAGESCOPE_TRACKING_JOB_RE.fullmatch(task_text):
        return None

    resolved = _lookup_remote_taskid_by_tracking_job(task_text, session_id=session_id)
    if resolved:
        return resolved
    return _lookup_remote_taskid_by_action_run(task_text, session_id=session_id)


def _resolve_session_phagescope_root(session_id: Optional[str]) -> Optional[Path]:
    """Resolve runtime/session_<id>/work/phagescope for session-scoped saves."""
    token = str(session_id or "").strip()
    if not token:
        return None
    try:
        from app.services.session_paths import get_session_phagescope_work_dir

        return get_session_phagescope_work_dir(token, create=True)
    except Exception as exc:
        logger.warning("Failed to resolve session-scoped PhageScope root for %s: %s", token, exc)
        return None


def _resolve_session_root(session_id: Optional[str]) -> Optional[Path]:
    token = str(session_id or "").strip()
    if not token:
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        return get_runtime_session_dir(token, create=True)
    except Exception as exc:
        logger.warning("Failed to resolve session root for %s: %s", token, exc)
        return None


def _dedupe_string_list(items: List[str]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _session_relative_path(path: Path, session_id: Optional[str]) -> Optional[str]:
    session_root = _resolve_session_root(session_id)
    if session_root is None:
        return None
    try:
        return str(path.resolve().relative_to(session_root.resolve())).replace("\\", "/")
    except Exception:
        return None


def _attach_output_location_fields(
    result: Dict[str, Any],
    *,
    base_dir: Optional[Path],
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
) -> Dict[str, Any]:
    if base_dir is None:
        return result

    resolved_base = base_dir.expanduser().resolve()
    out = dict(result)
    session_artifact_paths = [
        str(item) for item in list(out.get("session_artifact_paths") or []) if str(item).strip()
    ]
    if not session_artifact_paths and resolved_base.exists() and resolved_base.is_dir():
        for candidate in sorted(resolved_base.rglob("*")):
            if not candidate.is_file():
                continue
            rel_path = _session_relative_path(candidate, session_id)
            session_artifact_paths.append(rel_path or str(candidate.resolve()))
    if session_artifact_paths:
        out["session_artifact_paths"] = _dedupe_string_list(session_artifact_paths)

    out["output_location"] = {
        "type": "task" if task_id is not None else "tmp",
        "session_id": session_id,
        "task_id": task_id,
        "ancestor_chain": ancestor_chain,
        "base_dir": str(resolved_base),
        "files": list(out.get("session_artifact_paths") or []),
    }
    return out


def _attach_local_file_artifact_fields(
    result: Dict[str, Any],
    *,
    local_path: Path,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    output_base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    resolved = local_path.expanduser().resolve()
    out = dict(result)
    out["saved_path"] = str(resolved)
    out["output_file"] = str(resolved)
    artifact_paths = list(out.get("artifact_paths") or [])
    artifact_paths.append(str(resolved))
    out["artifact_paths"] = _dedupe_string_list([str(item) for item in artifact_paths])
    rel_path = _session_relative_path(resolved, session_id)
    if rel_path:
        out["saved_path_rel"] = rel_path
        out["output_file_rel"] = rel_path
        session_artifact_paths = list(out.get("session_artifact_paths") or [])
        session_artifact_paths.append(rel_path)
        out["session_artifact_paths"] = _dedupe_string_list(
            [str(item) for item in session_artifact_paths]
        )
    return _attach_output_location_fields(
        out,
        base_dir=output_base_dir or resolved.parent,
        session_id=session_id,
        task_id=task_id,
        ancestor_chain=ancestor_chain,
    )


def _attach_local_bundle_artifact_fields(
    result: Dict[str, Any],
    *,
    output_dir: Path,
    saved_files: Dict[str, str],
    summary_file: Optional[Path] = None,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
) -> Dict[str, Any]:
    out = dict(result)
    artifact_paths: List[str] = [str(item) for item in list(out.get("artifact_paths") or [])]
    session_artifact_paths: List[str] = [
        str(item) for item in list(out.get("session_artifact_paths") or [])
    ]
    candidates: List[Path] = []
    if summary_file is not None:
        candidates.append(summary_file.expanduser().resolve())
    for raw_path in saved_files.values():
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (output_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        candidates.append(candidate)
    for candidate in candidates:
        artifact_paths.append(str(candidate))
        rel_path = _session_relative_path(candidate, session_id)
        if rel_path:
            session_artifact_paths.append(rel_path)
    if artifact_paths:
        out["artifact_paths"] = _dedupe_string_list(artifact_paths)
    if session_artifact_paths:
        out["session_artifact_paths"] = _dedupe_string_list(session_artifact_paths)
    return _attach_output_location_fields(
        out,
        base_dir=output_dir,
        session_id=session_id,
        task_id=task_id,
        ancestor_chain=ancestor_chain,
    )


def _get_manifests_directory(session_id: Optional[str]) -> Tuple[Path, Optional[str]]:
    """Return ``.../work/phagescope/manifests`` under the session, or ``runtime/phagescope/manifests`` fallback."""
    warning: Optional[str] = None
    token = str(session_id or "").strip()
    if token:
        root = _resolve_session_phagescope_root(token)
        if root is not None:
            mdir = root / "manifests"
            mdir.mkdir(parents=True, exist_ok=True)
            return mdir.resolve(), None
    mdir = Path("runtime/phagescope/manifests")
    mdir.mkdir(parents=True, exist_ok=True)
    warning = "no session_id: manifest stored under runtime/phagescope/manifests"
    return mdir.resolve(), warning


def _dedupe_phage_ids_preserve_order(ids: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in ids:
        t = str(item or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _normalize_phage_id_list(
    value: Any,
    *,
    file_path: Optional[str] = None,
) -> List[str]:
    """Coerce phage ID list from string (semicolon/comma/newline), list/tuple, or optional file path."""
    raw: List[str] = []
    if file_path:
        p = Path(str(file_path).strip()).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"phage_ids_file not found: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw.append(line)
    if value is not None:
        if isinstance(value, str):
            for part in re.split(r"[\s,;]+", value.replace("\n", ";")):
                part = part.strip()
                if part:
                    raw.append(part)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                s = str(item or "").strip()
                if s:
                    raw.append(s)
    return _dedupe_phage_ids_preserve_order(raw)


def _phage_accession_ids_from_result_payload(payload: Any) -> set:
    """Extract accession-like IDs from PhageScope ``result`` phage JSON."""
    if not isinstance(payload, dict):
        return set()
    rows = payload.get("results")
    if not isinstance(rows, list):
        return set()
    ids: set = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("Acession_ID", "Accession_ID", "phageid", "phage_id", "contig_id"):
            v = row.get(key)
            if isinstance(v, str) and v.strip():
                ids.add(v.strip())
    return ids


def _load_manifest_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def _save_manifest_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_taskid_from_submit_result(result: Dict[str, Any]) -> Optional[str]:
    """Parse remote task id from submit responses (only ``taskid`` / ``task_id`` fields, not status_code)."""

    def _coerce_taskid_value(value: Any) -> Optional[str]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            # Avoid confusing HTTP status codes (e.g. 200) with PhageScope task ids.
            if value < 1000:
                return None
            return str(value)
        if isinstance(value, str):
            s = value.strip()
            if s.isdigit() and int(s) >= 1000:
                return s
        return None

    data = result.get("data")
    if isinstance(data, dict):
        for key in ("taskid", "task_id", "remote_taskid"):
            if key in data:
                tid = _coerce_taskid_value(data.get(key))
                if tid:
                    return tid
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("taskid", "task_id", "remote_taskid"):
                if key in inner:
                    tid = _coerce_taskid_value(inner.get(key))
                    if tid:
                        return tid
        # Many PhageScope deployments return taskid under ``results``, not nested ``data``.
        results = data.get("results")
        if isinstance(results, dict):
            for key in ("taskid", "task_id", "remote_taskid", "id"):
                if key in results:
                    tid = _coerce_taskid_value(results.get(key))
                    if tid:
                        return tid
    return None


def _coerce_modulelist_for_manifest(modulelist: Any) -> Any:
    if modulelist is None:
        return None
    if isinstance(modulelist, (list, tuple)):
        return [str(x) for x in modulelist]
    if isinstance(modulelist, str):
        return modulelist
    if isinstance(modulelist, dict):
        return modulelist
    return str(modulelist)


async def _phagescope_batch_submit(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    userid: str,
    modulelist: Any,
    rundemo: str,
    analysistype: str,
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
    comparedatabase: Optional[str],
    neednum: Optional[str],
    phage_ids: Any,
    phage_ids_file: Optional[str],
    batch_id: Optional[str],
    strategy: str,
    manifest_path_override: Optional[str],
) -> Dict[str, Any]:
    try:
        ids = _normalize_phage_id_list(phage_ids, file_path=phage_ids_file)
    except OSError as exc:
        return {"success": False, "status_code": 400, "action": "batch_submit", "error": str(exc)}
    if not ids:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_submit",
            "error": "phage_ids (or phage_ids_file) is required and must list at least one phage id",
        }
    if not modulelist:
        modulelist = list(_DEFAULT_BATCH_SUBMIT_MODULELIST)

    bid = str(batch_id or "").strip() or str(uuid.uuid4())
    manifests_dir, path_warning = _get_manifests_directory(session_id)
    if manifest_path_override:
        mpath = Path(str(manifest_path_override).strip()).expanduser().resolve()
    else:
        mpath = manifests_dir / f"{bid}.json"

    strat = str(strategy or "multi_one_task").strip().lower()
    if strat not in {"multi_one_task", "per_strain"}:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_submit",
            "error": "strategy must be multi_one_task or per_strain",
        }

    manifest: Dict[str, Any] = {
        "version": 1,
        "batch_id": bid,
        "strategy": strat,
        "requested_phage_ids": ids,
        "userid": userid,
        "modulelist": _coerce_modulelist_for_manifest(modulelist),
        "rundemo": str(rundemo).lower(),
        "analysistype": analysistype,
        "inputtype": inputtype,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(mpath),
        "primary_taskid": None,
        "per_strain_tasks": [],
        "retries": [],
        "last_reconcile": None,
    }

    if strat == "multi_one_task":
        joined = ";".join(ids)
        sub = await phagescope_handler(
            action="submit",
            base_url=base_url,
            token=token,
            timeout=timeout,
            phageids=joined,
            userid=userid,
            modulelist=modulelist,
            rundemo=rundemo,
            analysistype=analysistype,
            inputtype=inputtype,
            sequence=sequence,
            file_path=file_path,
            session_id=session_id,
            comparedatabase=comparedatabase,
            neednum=neednum,
        )
        tid = _extract_taskid_from_submit_result(sub) if isinstance(sub, dict) else None
        manifest["primary_taskid"] = tid
        manifest["primary_submit"] = {"success": sub.get("success"), "status_code": sub.get("status_code")}
        _save_manifest_json(mpath, manifest)
        out: Dict[str, Any] = {
            "success": sub.get("success") is not False and bool(tid),
            "status_code": sub.get("status_code") or 200,
            "action": "batch_submit",
            "batch_id": bid,
            "strategy": strat,
            "requested_phage_ids": ids,
            "primary_taskid": tid,
            "manifest_path": str(mpath),
            "submit_result": sub,
        }
        if path_warning:
            out["warning"] = path_warning
        if not tid:
            out["success"] = False
            out["error"] = sub.get("error") or "could not extract taskid from submit response"
        return out

    per: List[Dict[str, Any]] = []
    all_ok = True
    for pid in ids:
        sub = await phagescope_handler(
            action="submit",
            base_url=base_url,
            token=token,
            timeout=timeout,
            phageid=pid,
            userid=userid,
            modulelist=modulelist,
            rundemo=rundemo,
            analysistype=analysistype,
            inputtype=inputtype,
            sequence=sequence,
            file_path=file_path,
            session_id=session_id,
            comparedatabase=comparedatabase,
            neednum=neednum,
        )
        tid = _extract_taskid_from_submit_result(sub) if isinstance(sub, dict) else None
        if sub.get("success") is False or not tid:
            all_ok = False
        per.append({"phage_id": pid, "taskid": tid, "submit": sub})
    manifest["per_strain_tasks"] = per
    _save_manifest_json(mpath, manifest)
    out = {
        "success": all_ok,
        "status_code": 200 if all_ok else 207,
        "action": "batch_submit",
        "batch_id": bid,
        "strategy": strat,
        "requested_phage_ids": ids,
        "per_strain_tasks": per,
        "manifest_path": str(mpath),
    }
    if path_warning:
        out["warning"] = path_warning
    return out


async def _phagescope_batch_reconcile(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    batch_id: str,
    taskid: Optional[str],
    wait: bool,
    poll_interval: float,
    poll_timeout: float,
    manifest_path_override: Optional[str],
) -> Dict[str, Any]:
    bid = str(batch_id or "").strip()
    if not bid:
        return {"success": False, "status_code": 400, "action": "batch_reconcile", "error": "batch_id is required"}
    manifests_dir, path_warning = _get_manifests_directory(session_id)
    if manifest_path_override:
        mpath = Path(str(manifest_path_override).strip()).expanduser().resolve()
    else:
        mpath = manifests_dir / f"{bid}.json"
    try:
        manifest = _load_manifest_json(mpath)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"success": False, "status_code": 400, "action": "batch_reconcile", "error": str(exc)}

    remote_tid = str(taskid or "").strip() or str(manifest.get("primary_taskid") or "").strip()
    if not remote_tid:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_reconcile",
            "error": "taskid is required (or manifest must contain primary_taskid)",
        }

    td = await phagescope_handler(
        action="task_detail",
        base_url=base_url,
        token=token,
        timeout=timeout,
        taskid=remote_tid,
        session_id=session_id,
    )
    if td.get("success") is False:
        return {
            "success": False,
            "status_code": td.get("status_code") or 400,
            "action": "batch_reconcile",
            "error": td.get("error") or "task_detail failed",
            "task_detail": td,
        }
    res_block = (td.get("data") or {}).get("results") if isinstance(td.get("data"), dict) else None
    remote_status = ""
    if isinstance(res_block, dict):
        remote_status = str(res_block.get("status") or "").strip()
    if remote_status.lower() != "success":
        return {
            "success": False,
            "status_code": 409,
            "action": "batch_reconcile",
            "error": f"remote task status is not Success (got {remote_status or 'unknown'})",
            "remote_status": remote_status,
            "task_detail": td,
        }

    pr = await phagescope_handler(
        action="result",
        base_url=base_url,
        token=token,
        timeout=timeout,
        taskid=remote_tid,
        result_kind="phage",
        session_id=session_id,
        wait=wait,
        poll_interval=poll_interval,
        poll_timeout=poll_timeout,
    )
    observed: set = set()
    if pr.get("success"):
        pdata = pr.get("data")
        if isinstance(pdata, dict):
            observed = _phage_accession_ids_from_result_payload(pdata)
    requested_list = manifest.get("requested_phage_ids") or []
    if not isinstance(requested_list, list):
        requested_list = []
    requested_set = {str(x).strip() for x in requested_list if str(x).strip()}
    missing = sorted(requested_set - observed)
    reconcile_note: Optional[str] = None
    if not observed and pr.get("success"):
        reconcile_note = (
            "No rows in phage result; requested_vs_observed diff may be meaningless until result phage is populated."
        )
    elif missing and manifest.get("rundemo") in ("true", "1", "yes"):
        reconcile_note = (
            "Some requested IDs are missing from phage results; with rundemo=true the platform may substitute demo "
            "contigs so observed IDs may not match requested accessions."
        )

    manifest["last_reconcile"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "primary_taskid": remote_tid,
        "remote_status": remote_status,
        "observed_phage_ids": sorted(observed),
        "missing_phage_ids": missing,
        "reconcile_note": reconcile_note,
        "result_phage_success": pr.get("success"),
        "result_phage_status_code": pr.get("status_code"),
    }
    _save_manifest_json(mpath, manifest)

    out: Dict[str, Any] = {
        "success": True,
        "status_code": 200,
        "action": "batch_reconcile",
        "batch_id": bid,
        "primary_taskid": remote_tid,
        "manifest_path": str(mpath),
        "requested_phage_ids": sorted(requested_set),
        "observed_phage_ids": sorted(observed),
        "missing_phage_ids": missing,
        "reconcile_note": reconcile_note,
        "result_phage": pr,
    }
    if path_warning:
        out["warning"] = path_warning
    return out


async def _phagescope_batch_retry(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    userid: str,
    modulelist: Any,
    rundemo: str,
    analysistype: str,
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
    comparedatabase: Optional[str],
    neednum: Optional[str],
    batch_id: str,
    retry_phage_ids: Any,
    manifest_path_override: Optional[str],
) -> Dict[str, Any]:
    bid = str(batch_id or "").strip()
    if not bid:
        return {"success": False, "status_code": 400, "action": "batch_retry", "error": "batch_id is required"}
    manifests_dir, path_warning = _get_manifests_directory(session_id)
    if manifest_path_override:
        mpath = Path(str(manifest_path_override).strip()).expanduser().resolve()
    else:
        mpath = manifests_dir / f"{bid}.json"
    try:
        manifest = _load_manifest_json(mpath)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"success": False, "status_code": 400, "action": "batch_retry", "error": str(exc)}

    to_retry: List[str]
    if retry_phage_ids is not None:
        to_retry = _normalize_phage_id_list(retry_phage_ids)
    else:
        lr = manifest.get("last_reconcile")
        if isinstance(lr, dict) and isinstance(lr.get("missing_phage_ids"), list):
            to_retry = [str(x).strip() for x in lr["missing_phage_ids"] if str(x).strip()]
        else:
            to_retry = []

    if not to_retry:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_retry",
            "error": "retry_phage_ids is required when last_reconcile.missing_phage_ids is empty or missing",
        }

    mod = modulelist if modulelist is not None else manifest.get("modulelist")
    uid = userid or str(manifest.get("userid") or "agent_default_user")
    rd = str(manifest.get("rundemo") or rundemo or "false").lower()
    atype = str(manifest.get("analysistype") or analysistype or "Annotation Pipline")
    itype = str(manifest.get("inputtype") or inputtype or "enter")

    retries = manifest.get("retries")
    if not isinstance(retries, list):
        retries = []

    results: List[Dict[str, Any]] = []
    for pid in to_retry:
        sub = await phagescope_handler(
            action="submit",
            base_url=base_url,
            token=token,
            timeout=timeout,
            phageid=pid,
            userid=uid,
            modulelist=mod,
            rundemo=rd,
            analysistype=atype,
            inputtype=itype,
            sequence=sequence,
            file_path=file_path,
            session_id=session_id,
            comparedatabase=comparedatabase,
            neednum=neednum,
        )
        tid = _extract_taskid_from_submit_result(sub) if isinstance(sub, dict) else None
        entry = {
            "phage_id": pid,
            "taskid": tid,
            "success": sub.get("success") is not False and bool(tid),
            "at": datetime.now(timezone.utc).isoformat(),
            "submit_status_code": sub.get("status_code"),
        }
        if sub.get("success") is False or not tid:
            entry["error"] = sub.get("error") or "submit failed"
        retries.append(entry)
        results.append({"phage_id": pid, "taskid": tid, "submit": sub})

    manifest["retries"] = retries
    manifest["last_batch_retry_at"] = datetime.now(timezone.utc).isoformat()
    _save_manifest_json(mpath, manifest)

    slice_entries = retries[-len(to_retry) :] if retries else []
    all_ok = bool(slice_entries) and all(bool(e.get("success")) for e in slice_entries)
    out: Dict[str, Any] = {
        "success": all_ok,
        "status_code": 200 if all_ok else 207,
        "action": "batch_retry",
        "batch_id": bid,
        "manifest_path": str(mpath),
        "retry_phage_ids": to_retry,
        "retry_results": results,
    }
    if path_warning:
        out["warning"] = path_warning
    return out


def _infer_result_kind_from_path(path: str, fallback_kind: Optional[str] = None) -> Optional[str]:
    """Infer canonical result_kind from a download path."""
    if not path:
        return fallback_kind

    normalized = path.strip().lower().split("?", 1)[0].rstrip("/")
    if not normalized:
        return fallback_kind

    basename = normalized.rsplit("/", 1)[-1]
    by_filename = FILENAME_TO_RESULT_KIND.get(basename)
    if by_filename:
        return by_filename

    # Match token boundaries to avoid partial collisions (e.g., "phagefasta" vs "phage").
    for candidate in sorted(RESULT_KIND_TO_FILENAME.keys(), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", normalized):
            return candidate

    return fallback_kind


def _parse_modulelist(value: Optional[str]) -> List[str]:
    """Parse a Python-list-like string into a Python list.

    Prefers `ast.literal_eval()` for safe literal parsing and falls back to
    JSON parsing when needed. Supports both list and tuple payloads.

    Args:
        value: String to parse (Python list literal or JSON array).

    Returns:
        Parsed string list, or an empty list on failure.
    """
    if not value or not isinstance(value, str):
        return []
    
    value = value.strip()
    if not value:
        return []
    
    # Prefer ast.literal_eval() for safe literal parsing.
    try:
        parsed = ast.literal_eval(value)
        # Support list and tuple payloads.
        if isinstance(parsed, (list, tuple)):
            return [str(item) for item in parsed]
    except (ValueError, SyntaxError):
        pass
    
    # Fallback to JSON parsing (including simple quote-normalization cases).
    try:
        parsed = json.loads(value.replace("'", '"'))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    
    return []


def _normalize_module_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text.replace("-", "_").replace(" ", "_")


def _coerce_module_items(value: Any, *, analysistype: Optional[str] = None) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str) and value.strip().lower() == "all":
        config = ANALYSIS_TYPES.get(str(analysistype or "").strip())
        if isinstance(config, dict):
            modules = config.get("modules")
            if isinstance(modules, list):
                return [_normalize_module_token(item) for item in modules if _normalize_module_token(item)]

    items: List[str] = []
    if isinstance(value, (list, tuple, set)):
        items = [_normalize_module_token(item) for item in value]
    elif isinstance(value, dict):
        items = [
            _normalize_module_token(key)
            for key, enabled in value.items()
            if enabled and _normalize_module_token(key)
        ]
    elif isinstance(value, str):
        raw = value.strip()
        parsed = _safe_json_loads(raw.replace("'", '"')) if raw.startswith(("{", "[")) else None
        if isinstance(parsed, dict):
            items = [
                _normalize_module_token(key)
                for key, enabled in parsed.items()
                if enabled and _normalize_module_token(key)
            ]
        elif isinstance(parsed, list):
            items = [_normalize_module_token(item) for item in parsed]
        elif "," in raw:
            items = [_normalize_module_token(item) for item in raw.split(",")]
        else:
            items = [_normalize_module_token(raw)]
    else:
        items = [_normalize_module_token(value)]

    deduped: List[str] = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _normalize_submit_module_request(
    modulelist: Any,
    *,
    analysistype: str,
) -> Tuple[List[str], str, List[str], List[str]]:
    requested_items = _coerce_module_items(modulelist, analysistype=analysistype)
    allowed_modules = [
        _normalize_module_token(item)
        for item in ANALYSIS_TYPES.get(analysistype, {}).get("modules", [])
        if _normalize_module_token(item)
    ]
    allowed_set = set(allowed_modules)
    normalized_items: List[str] = []
    warnings: List[str] = []

    if not allowed_set:
        normalized_items = list(requested_items)
    else:
        for item in requested_items:
            mapped = RESULT_DERIVED_SUBMIT_MODULES.get(item)
            if mapped:
                if mapped not in normalized_items:
                    normalized_items.append(mapped)
                warnings.append(
                    f"module '{item}' is a result/output name, not a submit module; normalized to '{mapped}'."
                )
                continue

            if item in RESULT_ONLY_QUERY_KINDS:
                warnings.append(
                    f"module '{item}' is a result/output name, not a submit module; it was removed from submit payload."
                )
                continue

            if item in allowed_set:
                if item not in normalized_items:
                    normalized_items.append(item)
                continue

            warnings.append(
                f"module '{item}' is not supported for analysistype '{analysistype}' and was removed."
            )

    modulelist_json = json.dumps({item: True for item in normalized_items}) if normalized_items else ""
    return requested_items, modulelist_json, normalized_items, warnings


def _safe_json_loads(value: Optional[str]) -> Optional[Any]:
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _ensure_json_list_string(value: str) -> str:
    value = value.strip()
    if value.startswith("["):
        return value
    return json.dumps([value])


def _ensure_semicolon_list_string(value: str) -> str:
    value = value.strip()
    if ";" in value:
        return value
    if value.startswith("["):
        parsed = _safe_json_loads(value.replace("'", '"'))
        if isinstance(parsed, list):
            return ";".join(str(item) for item in parsed)
    return value


_ALL_ANNOTATION_MODULES = {
    "quality": True,
    "host": True,
    "lifestyle": True,
    "annotation": True,
    "terminator": True,
    "taxonomic": True,
    "trna": True,
    "anticrispr": True,
    "crispr": True,
    "arvf": True,
    "transmembrane": True,
}


def _normalize_modulelist(value: Any) -> str:
    if value is None:
        return ""
    # "all" → expand to every annotation module
    if isinstance(value, str) and value.strip().lower() == "all":
        return json.dumps(_ALL_ANNOTATION_MODULES)
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return json.dumps({str(item): True for item in value})
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("{") or raw.startswith("["):
            parsed = _safe_json_loads(raw.replace("'", '"'))
            if isinstance(parsed, dict):
                return json.dumps(parsed)
            if isinstance(parsed, list):
                return json.dumps({str(item): True for item in parsed})
            return raw
        if "," in raw:
            items = [item.strip() for item in raw.split(",") if item.strip()]
            return json.dumps({item: True for item in items})
        return json.dumps({raw: True})
    return json.dumps({str(value): True})


def _coerce_sequence_ids(value: Any) -> List[str]:
    """Normalize loose sequence_ids payloads to a clean ID list."""
    if value is None:
        return []

    items: List[str] = []
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        parsed = _safe_json_loads(raw.replace("'", '"')) if raw.startswith("[") else None
        if isinstance(parsed, list):
            items = [str(v).strip() for v in parsed if str(v).strip()]
        else:
            items = [chunk.strip() for chunk in re.split(r"[;,\s]+", raw) if chunk.strip()]
    else:
        text = str(value).strip()
        if text:
            items = [text]

    # Keep order, drop duplicates.
    seen = set()
    deduped: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _coerce_accession_ids_from_sequence(value: Any) -> List[str]:
    """If sequence field actually carries accession IDs, extract them."""
    if not isinstance(value, str):
        return []
    raw = value.strip()
    if not raw:
        return []
    # Real sequence/FASTA content should not be treated as accession IDs.
    if "\n" in raw and (">" in raw or len(raw) > 120):
        return []

    items = _coerce_sequence_ids(raw)
    if not items:
        return []

    # Broad accession-like pattern (e.g., NC_001628.1, MN908947.3)
    accession_re = re.compile(r"^[A-Za-z]{1,6}_?\d+(?:\.\d+)?$")
    if all(accession_re.match(item) for item in items):
        return items
    return []


def _apply_sequence_ids_alias(
    phageid: Optional[str],
    phageids: Optional[str],
    sequence_ids: Any,
) -> Tuple[Optional[str], Optional[str]]:
    ids = _coerce_sequence_ids(sequence_ids)
    if not ids:
        return phageid, phageids

    if not phageid:
        phageid = ids[0] if len(ids) == 1 else json.dumps(ids)
    if not phageids:
        phageids = ";".join(ids)
    return phageid, phageids


def _normalize_result_kind_and_module(
    result_kind: Optional[str],
    module: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    if not result_kind:
        return result_kind, module

    raw = result_kind.strip()
    if not raw:
        return None, module

    canonical = raw.lower().replace(" ", "_")
    if canonical in RESULT_ENDPOINTS:
        return canonical, module

    if raw in RESULT_KIND_ALIASES:
        mapped_kind, mapped_module = RESULT_KIND_ALIASES[raw]
        if mapped_module and not module:
            module = mapped_module
        return mapped_kind, module

    if canonical in RESULT_KIND_ALIASES:
        mapped_kind, mapped_module = RESULT_KIND_ALIASES[canonical]
        if mapped_module and not module:
            module = mapped_module
        return mapped_kind, module

    dashed = canonical.replace("_", "-")
    if dashed in RESULT_KIND_ALIASES:
        mapped_kind, mapped_module = RESULT_KIND_ALIASES[dashed]
        if mapped_module and not module:
            module = mapped_module
        return mapped_kind, module

    return result_kind, module


def _results_payload_to_tsv_text(payload: Dict[str, Any]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("results")
    if rows is None:
        return None

    normalized_rows: List[Any]
    if isinstance(rows, list):
        normalized_rows = rows
    else:
        normalized_rows = [rows]

    buffer = StringIO()
    if normalized_rows and all(isinstance(item, dict) for item in normalized_rows):
        headers: List[str] = []
        for row in normalized_rows:
            for key in row.keys():
                key_str = str(key)
                if key_str not in headers:
                    headers.append(key_str)
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow({key: row.get(key, "") for key in headers})
        return buffer.getvalue()

    # Fallback for non-dict rows
    writer = csv.writer(buffer, delimiter="\t")
    writer.writerow(["value"])
    for row in normalized_rows:
        writer.writerow([row])
    return buffer.getvalue()


def _validate_module_dependencies(modules: List[str]) -> Tuple[bool, Optional[str]]:
    """Validate module dependencies. Returns (is_valid, error_message)."""
    module_set = set(m.lower() for m in modules)
    for module, deps in MODULE_DEPENDENCIES.items():
        if module.lower() in module_set:
            for dep in deps:
                if dep.lower() not in module_set:
                    return False, f"Module '{module}' requires '{dep}' module"
    return True, None


def _is_cluster_analysis(analysistype: str, modules: Optional[List[str]] = None) -> bool:
    """Determine whether this is a clustering analysis request."""
    if analysistype == "Genome Comparison":
        return True
    if modules:
        module_set = set(m.lower() for m in modules)
        return bool(module_set & CLUSTER_MODULES)
    return False


def _get_analysis_endpoint(analysistype: str, modules: Optional[List[str]] = None) -> str:
    """Resolve the correct API endpoint from analysis type/modules."""
    config = ANALYSIS_TYPES.get(analysistype)
    if config:
        return config["endpoint"]
    # If clustering modules are included, use clusterpipline.
    if _is_cluster_analysis(analysistype, modules):
        return "/analyze/clusterpipline/"
    return "/analyze/pipline/"


def _build_phage_payload(phageid: Optional[str], phageids: Optional[str]) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    if phageid:
        payload["phageid"] = _ensure_json_list_string(phageid)
    if phageids:
        payload["phageids"] = _ensure_semicolon_list_string(phageids)
        # Remote API requires both `phageid` (JSON list) and `phageids` (semicolon-separated).
        # When only phageids is provided (e.g. batch_submit multi_one_task), derive phageid from it.
        if not phageid:
            parts = [p.strip() for p in phageids.replace(",", ";").split(";") if p.strip()]
            payload["phageid"] = json.dumps(parts) if parts else _ensure_json_list_string(phageids)
    elif phageid:
        payload["phageids"] = _ensure_semicolon_list_string(phageid)
    return payload


def _extract_error_message(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("message", "error", "detail"):
        message = payload.get(key)
        if isinstance(message, str) and message.strip():
            return message.strip()[:240]
    raw = payload.get("raw")
    if isinstance(raw, str) and raw.strip():
        first_line = raw.strip().splitlines()[0]
        return first_line.strip()[:240]
    return None


def _merge_http_and_business_success(status_code: int, payload: Any) -> Tuple[bool, Dict[str, Any]]:
    """Combine HTTP status with PhageScope JSON ``code`` when present (phageapi.md).

    Documented semantics: ``code`` 0 = success, 1 = warning (non-fatal), >= 2 = error.
    When ``code`` is absent, success follows HTTP status only.
    """
    http_ok = status_code < 400
    meta: Dict[str, Any] = {}
    if not isinstance(payload, dict) or "code" not in payload:
        return http_ok, meta
    try:
        code_int = int(payload["code"])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return http_ok, meta
    meta["business_code"] = code_int
    if code_int >= 2:
        meta["business_failure"] = True
        err = _extract_error_message(payload)
        if err:
            meta["error"] = err
        return False, meta
    if code_int == 1:
        meta["business_warning"] = True
    return http_ok, meta


def _response_with_business_layer(
    action: str,
    status_code: int,
    payload: Any,
    **extra: Any,
) -> Dict[str, Any]:
    """HTTP 200 with business ``code`` >= 2 => ``success`` False."""
    success, biz_meta = _merge_http_and_business_success(status_code, payload)
    out: Dict[str, Any] = {
        "success": success,
        "status_code": status_code,
        "data": payload,
        "action": action,
    }
    if "business_code" in biz_meta:
        out["business_code"] = biz_meta["business_code"]
    if biz_meta.get("business_warning"):
        out["business_warning"] = True
    if biz_meta.get("business_failure"):
        out["business_failure"] = True
    be = biz_meta.get("error")
    if be:
        out["error"] = be
    elif not success and biz_meta.get("business_code") is not None:
        out["error"] = f"PhageScope API returned business code {biz_meta['business_code']} (expected 0 or 1)."
    out.update(extra)
    if not success and be and not out.get("error"):
        out["error"] = be
    return out


def _is_retriable_result_error(status_code: int, payload: Dict[str, Any]) -> bool:
    if status_code in {408, 429, 502, 503, 504}:
        return True
    candidates: List[str] = []
    for key in ("raw", "message", "error", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)
    if not candidates:
        return False
    raw_lower = "\n".join(candidates).lower()
    return (
        "filenotfounderror" in raw_lower
        or "no such file or directory" in raw_lower
        or "file not found" in raw_lower
    )


def _is_result_not_ready_error(status_code: int, payload: Dict[str, Any]) -> bool:
    """Detect PhageScope server-side 'result file not ready yet' errors.

    PhageScope sometimes returns 500 with a Django debug page containing a
    FileNotFoundError for result TSVs (e.g., phage.tsv / protein.tsv) while the
    pipeline is still running. Treat this as a soft 'still running' signal.
    """
    if status_code < 400:
        return False
    raw = payload.get("raw")
    if not isinstance(raw, str) or not raw.strip():
        return False
    raw_lower = raw.lower()
    if "filenotfounderror" not in raw_lower and "no such file or directory" not in raw_lower:
        return False
    # Heuristic: missing artifacts may live under output/result or output/rawdata
    # during execution (e.g., *.tsv / *.txt / *.fasta / *.nwk).
    has_output_path = (
        "/output/result/" in raw_lower
        or "/output/rawdata/" in raw_lower
        or "workspace/user_task" in raw_lower
        or "/tasks/result/" in raw_lower
    )
    has_result_ext = any(
        ext in raw_lower
        for ext in (".tsv", ".txt", ".fasta", ".fa", ".nwk", ".json")
    )
    if has_output_path and has_result_ext:
        return True
    return False

def _parse_task_detail(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    results = payload.get("results")
    if isinstance(results, dict):
        task_detail = results.get("task_detail")
        if isinstance(task_detail, str):
            parsed = _safe_json_loads(task_detail)
            if isinstance(parsed, dict):
                return parsed
    parsed_task_detail = payload.get("parsed_task_detail")
    if isinstance(parsed_task_detail, dict):
        return parsed_task_detail
    return None


def _module_completed(task_detail: Dict[str, Any], module_name: str) -> Optional[bool]:
    """Check whether a module has completed and data is valid.

    This checks both status and whether result-like fields are non-empty to
    avoid false positives.

    Args:
        task_detail: Task detail payload.
        module_name: Module name.

    Returns:
        True: module completed with valid data.
        False: module failed or data is invalid.
        None: unknown/indeterminate state.
    """
    if not module_name:
        logger.debug("_module_completed: module_name is empty")
        return None
    
    if not isinstance(task_detail, dict):
        logger.debug(f"_module_completed: task_detail is not a dict, type={type(task_detail)}")
        return None
    
    module_name_lower = module_name.lower()
    queue = task_detail.get("task_que")
    
    if not isinstance(queue, list):
        logger.debug(f"_module_completed: task_que is not a list, type={type(queue)}")
        return None
    
    if not queue:
        logger.debug(f"_module_completed: task_que is empty for module '{module_name}'")
        return None
    
    for idx, item in enumerate(queue):
        if not isinstance(item, dict):
            logger.debug(f"_module_completed: item {idx} in task_que is not a dict")
            continue
        
        module = item.get("module")
        if not isinstance(module, str):
            continue
        
        if module.lower() != module_name_lower:
            continue
        
        # Found the matching module; inspect status.
        status_value = item.get("module_satus") or item.get("module_status") or item.get("status")
        if not isinstance(status_value, str):
            logger.debug(f"_module_completed: module '{module_name}' status is not a string, type={type(status_value)}")
            return None
        
        status_upper = status_value.strip().upper()
        logger.debug(f"_module_completed: module '{module_name}' status='{status_upper}'")
        
        if status_upper in {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}:
            # For success-like status, validate data fields as well.
            # Check for result/data-like fields.
            has_data = False
            
            # Inspect common payload fields.
            for data_key in ("result", "results", "data", "output", "uploadpath"):
                data_value = item.get(data_key)
                if data_value is not None:
                    # Validate that data is non-empty.
                    if isinstance(data_value, (list, dict, str)):
                        if data_value:  # Non-empty container/string.
                            has_data = True
                            break
                    else:
                        # Non-container type: treat as data present.
                        has_data = True
                        break
            
            if has_data:
                logger.info(f"_module_completed: module '{module_name}' completed with valid data")
                return True
            else:
                # Success status but empty payload; log warning.
                logger.warning(
                    f"_module_completed: module '{module_name}' status is '{status_upper}' but data appears empty. "
                    f"Item keys: {list(item.keys())}"
                )
                # Keep backward compatibility by returning True, with warning.
                return True
        
        if status_upper in {"FAILED", "ERROR"}:
            # Capture failure details when available.
            error_msg = item.get("error") or item.get("message") or item.get("detail")
            if error_msg:
                logger.error(f"_module_completed: module '{module_name}' failed with error: {error_msg}")
            else:
                logger.error(f"_module_completed: module '{module_name}' failed")
            return False
        
        # Other statuses (for example RUNNING/PENDING) are not completed yet.
        logger.debug(f"_module_completed: module '{module_name}' status is '{status_upper}', not yet completed")
        return None
    
    # No matching module found.
    logger.debug(f"_module_completed: module '{module_name}' not found in task_que")
    return None


async def _request(
    method: str,
    base_url: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
) -> Tuple[int, Dict[str, Any]]:
    url = f"{base_url}{path}"
    verify = _ssl_verify_enabled(base_url)
    try:
        response = await _do_httpx_request(
            method,
            url,
            params=params,
            data=data,
            files=files,
            headers=headers,
            timeout=timeout,
            verify=verify,
        )
        return response.status_code, _decode_httpx_response(response)
    except httpx.HTTPError as exc:
        if verify and _should_retry_without_ssl_verify(base_url, exc):
            logger.warning("PhageScope TLS verification failed for %s; retrying with verify=False", url)
            response = await _do_httpx_request(
                method,
                url,
                params=params,
                data=data,
                files=files,
                headers=headers,
                timeout=timeout,
                verify=False,
            )
            payload = _attach_transport_warning(_decode_httpx_response(response), _TLS_RETRY_WARNING)
            return response.status_code, payload
        raise


async def phagescope_handler(
    action: str,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = 60.0,
    phageid: Optional[str] = None,
    phageids: Optional[str] = None,
    sequence_ids: Optional[Any] = None,
    inputtype: str = "enter",
    analysistype: str = "Annotation Pipline",
    userid: Optional[str] = None,
    modulelist: Optional[Any] = None,
    rundemo: str = "false",
    taskid: Optional[str] = None,
    modulename: Optional[str] = None,
    result_kind: Optional[str] = None,
    module: Optional[str] = None,
    page: Optional[int] = None,
    pagesize: Optional[int] = None,
    seq_type: Optional[str] = None,
    download_path: Optional[str] = None,
    save_path: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    preview_bytes: int = 4096,
    sequence: Optional[str] = None,
    file_path: Optional[str] = None,
    wait: bool = False,
    poll_interval: float = 2.0,
    poll_timeout: float = 120.0,
    # Cluster analysis specific parameters
    comparedatabase: Optional[str] = None,
    neednum: Optional[str] = None,
    # Batch orchestration (manifest under session work/phagescope/manifests)
    phage_ids: Optional[Any] = None,
    batch_id: Optional[str] = None,
    strategy: str = "multi_one_task",
    manifest_path: Optional[str] = None,
    retry_phage_ids: Optional[Any] = None,
    phage_ids_file: Optional[str] = None,
) -> Dict[str, Any]:
    base_url = _get_base_url(base_url)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    action = action.lower().strip()
    # Default userid so submit/task_list work even when LLM omits it.
    if not userid:
        userid = "agent_default_user"
    phageid, phageids = _apply_sequence_ids_alias(phageid, phageids, sequence_ids)
    # Compatibility: some callers misuse `sequence` to pass phage accession IDs.
    if sequence and not phageid and not phageids:
        accession_ids = _coerce_accession_ids_from_sequence(sequence)
        if accession_ids:
            phageid = accession_ids[0] if len(accession_ids) == 1 else json.dumps(accession_ids)
            phageids = ";".join(accession_ids)
            sequence = None

    if action == "batch_submit":
        # Submit-style names (phageids / phage_id) are common; batch_submit previously only read
        # phage_ids / phage_ids_file, so calls with phageids only looked "empty" and failed validation.
        effective_phage_ids = phage_ids
        if effective_phage_ids is None and phageids is not None and str(phageids).strip():
            effective_phage_ids = phageids
        if effective_phage_ids is None and phageid is not None and str(phageid).strip():
            effective_phage_ids = phageid
        return await _phagescope_batch_submit(
            base_url=base_url,
            token=token,
            timeout=timeout,
            session_id=session_id,
            userid=userid,
            modulelist=modulelist,
            rundemo=rundemo,
            analysistype=analysistype,
            inputtype=inputtype,
            sequence=sequence,
            file_path=file_path,
            comparedatabase=comparedatabase,
            neednum=neednum,
            phage_ids=effective_phage_ids,
            phage_ids_file=phage_ids_file,
            batch_id=batch_id,
            strategy=strategy,
            manifest_path_override=manifest_path,
        )

    if action == "quality":
        action = "result"
        result_kind = result_kind or "quality"
    raw_taskid = taskid
    taskid = _resolve_phagescope_taskid(taskid, session_id=session_id)

    if action == "batch_reconcile":
        return await _phagescope_batch_reconcile(
            base_url=base_url,
            token=token,
            timeout=timeout,
            session_id=session_id,
            batch_id=str(batch_id or "").strip(),
            taskid=taskid,
            wait=wait,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            manifest_path_override=manifest_path,
        )

    if action == "batch_retry":
        return await _phagescope_batch_retry(
            base_url=base_url,
            token=token,
            timeout=timeout,
            session_id=session_id,
            userid=userid,
            modulelist=modulelist,
            rundemo=rundemo,
            analysistype=analysistype,
            inputtype=inputtype,
            sequence=sequence,
            file_path=file_path,
            comparedatabase=comparedatabase,
            neednum=neednum,
            batch_id=str(batch_id or "").strip(),
            retry_phage_ids=retry_phage_ids,
            manifest_path_override=manifest_path,
        )

    if action == "bulk_download":
        from .phagescope_bulk_download import phagescope_bulk_download

        # Parse datasources / data_types from various input shapes
        def _coerce_list(val: Any) -> Optional[List[str]]:
            if val is None:
                return None
            if isinstance(val, (list, tuple)):
                return [str(v).strip() for v in val if str(v).strip()]
            if isinstance(val, str):
                text = val.strip()
                if not text or text.lower() == "all":
                    return None
                return [s.strip() for s in re.split(r"[;,\s]+", text) if s.strip()]
            return None

        return await phagescope_bulk_download(
            datasources=_coerce_list(phage_ids or phageids or phageid),
            data_types=_coerce_list(modulelist),
            base_url=base_url,
            proxy=None,  # resolved from env vars inside bulk_download
            session_id=session_id,
            task_id=task_id,
            ancestor_chain=ancestor_chain,
            save_path=save_path,
            concurrency=int(pagesize) if pagesize and int(pagesize) > 0 else 4,
            timeout=timeout,
        )

    # "download" without a concrete path is a common LLM mistake; batch-fetch artifacts instead.
    if action == "download" and not download_path and taskid:
        action = "save_all"

    if (
        action in {"save_all", "task_log"}
        and raw_taskid is not None
        and not taskid
    ):
        return {
            "success": False,
            "status_code": 400,
            "action": action,
            "error": (
                "taskid must be a numeric PhageScope task id (for example 37468), "
                "not a local job id alias."
            ),
            "error_code": "invalid_taskid",
        }

    if (
        action == "task_detail"
        and raw_taskid is not None
        and not taskid
        and not phageid
        and not phageids
    ):
        return {
            "success": False,
            "status_code": 400,
            "action": action,
            "error": (
                "task_detail requires a numeric taskid when phageid is not provided."
            ),
            "error_code": "invalid_taskid",
        }

    if action == "query":
        # Heuristic alias to avoid failing when the caller uses "query".
        resolved_taskid = taskid
        resolved_result = result_kind

        module_items: List[str] = []
        if modulelist is not None:
            if isinstance(modulelist, (list, tuple)):
                module_items = [str(item) for item in modulelist]
            elif isinstance(modulelist, str):
                parsed_modules = _safe_json_loads(modulelist.replace("'", '"'))
                if isinstance(parsed_modules, list):
                    module_items = [str(item) for item in parsed_modules]
                elif isinstance(parsed_modules, dict):
                    module_items = [str(key) for key in parsed_modules.keys()]
                else:
                    module_items = [modulelist]

        if not resolved_result and module_items:
            if "quality" in module_items:
                resolved_result = "quality"

        if not resolved_taskid and userid:
            status_code, payload = await _request(
                "GET", base_url, "/tasks/list/", params={"userid": userid}, headers=headers, timeout=timeout
            )
            if status_code >= 400:
                return {
                    "success": False,
                    "status_code": status_code,
                    "action": "query",
                    "error": "Failed to list tasks for query",
                    "data": payload,
                }
            q_ok, q_meta = _merge_http_and_business_success(status_code, payload)
            if not q_ok:
                return {
                    "success": False,
                    "status_code": status_code,
                    "action": "query",
                    "data": payload,
                    "error": q_meta.get("error")
                    or f"PhageScope task list returned business code {q_meta.get('business_code')}",
                    **{k: v for k, v in q_meta.items() if k in ("business_code", "business_failure")},
                }
            tasks = payload.get("results") if isinstance(payload, dict) else None
            if isinstance(tasks, list) and tasks:
                def _task_key(item: Any) -> int:
                    try:
                        return int(item.get("id", 0))
                    except Exception:
                        return 0

                latest = max(tasks, key=_task_key)
                resolved_taskid = str(latest.get("id"))

        if resolved_taskid:
            if resolved_result:
                action = "result"
                taskid = resolved_taskid
                result_kind = resolved_result
            else:
                action = "task_detail"
                taskid = resolved_taskid
        else:
            return {
                "success": False,
                "status_code": 400,
                "action": "query",
                "error": "query requires taskid or userid",
            }

    try:
        if action == "ping":
            status_code, payload = await _request("GET", base_url, "/", headers=headers, timeout=timeout)
            return _response_with_business_layer("ping", status_code, payload)

        if action == "input_check":
            data = _build_phage_payload(phageid, phageids)
            data["inputtype"] = inputtype
            if sequence:
                data["file"] = sequence
                data["inputtype"] = "paste"
            files = None
            if file_path:
                abs_path = Path(file_path).expanduser().resolve()
                file_handle = abs_path.open("rb")
                files = {"submitfile": file_handle}
                data["inputtype"] = "upload"
            try:
                status_code, payload = await _request(
                    "POST", base_url, "/analyze/inputcheck/", data=data, files=files, headers=headers, timeout=timeout
                )
            finally:
                if files:
                    files["submitfile"].close()
            return _response_with_business_layer("input_check", status_code, payload)

        if action == "submit" or action == "cluster_submit":
            if not userid:
                return {"success": False, "status_code": 400, "error": "userid is required", "action": action}
            if not modulelist:
                return {"success": False, "status_code": 400, "error": "modulelist is required", "action": action}

            requested_module_probe = _coerce_module_items(modulelist, analysistype=analysistype)

            # Auto-select the correct endpoint.
            if action == "cluster_submit":
                endpoint = "/analyze/clusterpipline/"
                actual_analysistype = "Genome Comparison"
            else:
                endpoint = _get_analysis_endpoint(analysistype, requested_module_probe)
                actual_analysistype = analysistype

            (
                requested_module_items,
                normalized_modulelist_json,
                module_items,
                module_warnings,
            ) = _normalize_submit_module_request(
                modulelist,
                analysistype=actual_analysistype,
            )

            if not module_items:
                return {
                    "success": False,
                    "status_code": 400,
                    "error": (
                        "modulelist does not contain any valid submit modules for "
                        f"analysistype '{actual_analysistype}'"
                    ),
                    "action": action,
                    "requested_modules": requested_module_items,
                    "warnings": module_warnings,
                }

            # Validate module dependencies after normalization.
            is_valid, dep_error = _validate_module_dependencies(module_items)
            if not is_valid:
                return {
                    "success": False,
                    "status_code": 400,
                    "error": dep_error,
                    "action": action,
                    "requested_modules": requested_module_items,
                    "normalized_modules": module_items,
                    "warnings": module_warnings,
                }

            # PhageScope cluster API expects sequence/file payloads and may raise 500
            # for phageid-only requests. Fail fast with a clear local validation error.
            if endpoint == "/analyze/clusterpipline/" and not sequence and not file_path:
                return {
                    "success": False,
                    "status_code": 400,
                    "error": (
                        "cluster_submit requires sequence (inputtype=paste) "
                        "or file_path (inputtype=upload); phageid-only input is not supported by remote API."
                    ),
                    "action": action,
                }

            data = _build_phage_payload(phageid, phageids)
            data.update(
                {
                    "inputtype": inputtype,
                    "analysistype": actual_analysistype,
                    "userid": userid,
                    "modulelist": normalized_modulelist_json,
                    "rundemo": str(rundemo).lower(),
                }
            )

            # Cluster analysis specific parameters.
            if endpoint == "/analyze/clusterpipline/":
                if comparedatabase:
                    data["comparedatabase"] = comparedatabase
                if neednum:
                    data["neednum"] = neednum

            if sequence:
                data["file"] = sequence
                data["inputtype"] = "paste"
            files = None
            if file_path:
                abs_path = Path(file_path).expanduser().resolve()
                file_handle = abs_path.open("rb")
                files = {"submitfile": file_handle}
                data["inputtype"] = "upload"
            try:
                status_code, payload = await _request(
                    "POST", base_url, endpoint, data=data, files=files, headers=headers, timeout=timeout
                )
            finally:
                if files:
                    files["submitfile"].close()
            return _response_with_business_layer(
                action,
                status_code,
                payload,
                endpoint=endpoint,
                analysistype=actual_analysistype,
                requested_modules=requested_module_items,
                normalized_modules=module_items,
                warnings=module_warnings or None,
            )

        if action == "task_list":
            if not userid:
                return {"success": False, "status_code": 400, "error": "userid is required", "action": action}
            status_code, payload = await _request(
                "GET", base_url, "/tasks/list/", params={"userid": userid}, headers=headers, timeout=timeout
            )
            return _response_with_business_layer(action, status_code, payload)

        if action == "task_detail":
            if not taskid and (phageid or phageids):
                # LLM often calls task_detail with phageid instead of taskid.
                # Redirect to phage_detail result query which works with phageid.
                pass  # fall through to the result branch below
            elif not taskid:
                return {"success": False, "status_code": 400, "error": "taskid is required", "action": action}
            else:
                status_code, payload = await _request(
                    "GET", base_url, "/tasks/detail/", params={"taskid": taskid}, headers=headers, timeout=timeout
                )
                if isinstance(payload, dict):
                    results = payload.get("results", {})
                    modulelist_value = results.get("modulelist")
                    payload["parsed_modulelist"] = _parse_modulelist(modulelist_value)
                    task_detail = results.get("task_detail")
                    parsed_detail = _safe_json_loads(task_detail) if isinstance(task_detail, str) else None
                    if parsed_detail is not None:
                        payload["parsed_task_detail"] = parsed_detail
                return _with_api_only_artifact_hint(
                    _response_with_business_layer(action, status_code, payload),
                    taskid,
                )

            # Fallback: task_detail called with phageid -> redirect to result/phage_detail
            action = "result"
            result_kind = "phage_detail"

        if action == "task_log":
            if not taskid or not modulename:
                return {
                    "success": False,
                    "status_code": 400,
                    "error": "taskid and modulename are required",
                    "action": action,
                }
            status_code, payload = await _request(
                "GET",
                base_url,
                "/tasks/detail/log/",
                params={"taskid": taskid, "moudlename": modulename},
                headers=headers,
                timeout=timeout,
            )
            return _with_api_only_artifact_hint(
                _response_with_business_layer(action, status_code, payload),
                taskid,
            )

        if action == "result":
            if not result_kind:
                return {"success": False, "status_code": 400, "error": "result_kind is required", "action": action}
            result_kind, module = _normalize_result_kind_and_module(result_kind, module)
            endpoint = RESULT_ENDPOINTS.get(result_kind)
            if not endpoint:
                return {
                    "success": False,
                    "status_code": 400,
                    "error": f"unsupported result_kind: {result_kind}",
                    "action": action,
                }
            if not taskid and userid:
                status_code, payload = await _request(
                    "GET", base_url, "/tasks/list/", params={"userid": userid}, headers=headers, timeout=timeout
                )
                if status_code >= 400:
                    return {
                        "success": False,
                        "status_code": status_code,
                        "action": action,
                        "error": "Failed to list tasks for result lookup",
                        "data": payload,
                    }
                tasks = payload.get("results") if isinstance(payload, dict) else None
                if isinstance(tasks, list) and tasks:
                    def _task_key(item: Any) -> int:
                        try:
                            return int(item.get("id", 0))
                        except Exception:
                            return 0

                    latest = max(tasks, key=_task_key)
                    taskid = str(latest.get("id"))
            if not taskid:
                return {"success": False, "status_code": 400, "error": "taskid is required", "action": action}
            params: Dict[str, Any] = {}
            if taskid:
                params["taskid"] = taskid
            if module:
                params["module"] = module
            
            # Bug #5 Fix: Add pagination parameter validation and logging
            if page is not None:
                # Validate page parameter (must be positive integer)
                try:
                    page_val = int(page)
                    if page_val < 1:
                        logger.warning(f"Invalid page parameter: {page}, must be >= 1, using 1 instead")
                        page_val = 1
                    params["page"] = page_val
                except (ValueError, TypeError):
                    logger.warning(f"Invalid page parameter: {page}, ignoring")
            
            if pagesize is not None:
                # Validate pagesize parameter (must be positive integer, max 1000)
                try:
                    pagesize_val = int(pagesize)
                    if pagesize_val < 1:
                        logger.warning(f"Invalid pagesize parameter: {pagesize}, must be >= 1, using 100 instead")
                        pagesize_val = 100
                    elif pagesize_val > 1000:
                        logger.warning(f"Pagesize parameter: {pagesize} exceeds max (1000), using 1000 instead")
                        pagesize_val = 1000
                    params["pagesize"] = pagesize_val
                except (ValueError, TypeError):
                    logger.warning(f"Invalid pagesize parameter: {pagesize}, ignoring")
            
            if seq_type:
                params["type"] = seq_type
            if result_kind == "phage_detail" and phageid:
                params["phageid"] = phageid
            
            # Log pagination parameters for debugging
            if params.get("page") or params.get("pagesize"):
                logger.info(
                    f"Result request with pagination: page={params.get('page')}, "
                    f"pagesize={params.get('pagesize')}, endpoint={endpoint}, taskid={taskid}"
                )
            status_code, payload = await _request(
                "GET", base_url, endpoint, params=params, headers=headers, timeout=timeout
            )
            biz_ok, biz_meta = _merge_http_and_business_success(status_code, payload)
            if status_code < 400 and biz_ok:
                out = {
                    "success": True,
                    "status_code": status_code,
                    "data": payload,
                    "action": action,
                    "result_kind": result_kind,
                }
                if "business_code" in biz_meta:
                    out["business_code"] = biz_meta["business_code"]
                if biz_meta.get("business_warning"):
                    out["business_warning"] = True
                return _with_api_only_artifact_hint(out, str(taskid) if taskid is not None else None)

            if status_code < 400 and not biz_ok:
                err = biz_meta.get("error") or (
                    f"PhageScope API returned business code {biz_meta.get('business_code')} (expected 0 or 1)."
                )
                return {
                    "success": False,
                    "status_code": status_code,
                    "data": payload,
                    "action": action,
                    "result_kind": result_kind,
                    "error": err,
                    **{
                        k: v
                        for k, v in biz_meta.items()
                        if k in ("business_code", "business_failure", "business_warning")
                    },
                }

            # Soft-fail: remote result file not ready yet (common for phage/proteins).
            # Return 202 so the agent/UI can treat it as "still running" instead of "failed".
            if isinstance(payload, dict) and _is_result_not_ready_error(status_code, payload) and not wait:
                return {
                    "success": True,
                    "status_code": 202,
                    "action": action,
                    "result_kind": result_kind,
                    "taskid": str(taskid) if taskid is not None else None,
                    "status": "running",
                    "message": "Result not ready yet. The remote pipeline is likely still running. Retry later, or set wait=true to poll.",
                    "data": payload,
                    "not_ready": True,
                }

            if wait and isinstance(payload, dict) and poll_timeout > 0:
                start = time.monotonic()
                attempts = 0
                last_status_code = status_code
                last_payload: Dict[str, Any] = payload if isinstance(payload, dict) else {"raw": str(payload)}
                module_name = result_kind

                while time.monotonic() - start < poll_timeout:
                    attempts += 1
                    await asyncio.sleep(max(poll_interval, 0.2))

                    td_status, td_payload = await _request(
                        "GET",
                        base_url,
                        "/tasks/detail/",
                        params={"taskid": taskid},
                        headers=headers,
                        timeout=timeout,
                    )
                    if isinstance(td_payload, dict):
                        task_detail = _parse_task_detail(td_payload)
                        if isinstance(task_detail, dict):
                            completed = _module_completed(task_detail, module_name)
                            if completed is False:
                                return {
                                    "success": False,
                                    "status_code": td_status,
                                    "action": action,
                                    "result_kind": result_kind,
                                    "taskid": str(taskid),
                                    "error": f"Remote module '{module_name}' reported failure.",
                                    "data": {"task_detail": task_detail, "task_detail_raw": td_payload},
                                }

                    last_status_code, last_payload = await _request(
                        "GET",
                        base_url,
                        endpoint,
                        params=params,
                        headers=headers,
                        timeout=timeout,
                    )
                    if last_status_code < 400:
                        lb_ok, lb_meta = _merge_http_and_business_success(last_status_code, last_payload)
                        if lb_ok:
                            out = {
                                "success": True,
                                "status_code": last_status_code,
                                "data": last_payload,
                                "action": action,
                                "result_kind": result_kind,
                                "polling": {
                                    "waited": True,
                                    "attempts": attempts,
                                    "poll_timeout": poll_timeout,
                                    "poll_interval": poll_interval,
                                },
                            }
                            if "business_code" in lb_meta:
                                out["business_code"] = lb_meta["business_code"]
                            if lb_meta.get("business_warning"):
                                out["business_warning"] = True
                            return _with_api_only_artifact_hint(out, str(taskid) if taskid is not None else None)
                        return {
                            "success": False,
                            "status_code": last_status_code,
                            "data": last_payload,
                            "action": action,
                            "result_kind": result_kind,
                            "error": lb_meta.get("error")
                            or f"PhageScope API returned business code {lb_meta.get('business_code')}",
                            **{
                                k: v
                                for k, v in lb_meta.items()
                                if k in ("business_code", "business_failure", "business_warning")
                            },
                        }

                    # If still not ready, keep polling until poll_timeout.
                    if isinstance(last_payload, dict) and _is_result_not_ready_error(last_status_code, last_payload):
                        continue

                    if not (isinstance(last_payload, dict) and _is_retriable_result_error(last_status_code, last_payload)):
                        break

                error_message = None
                if isinstance(last_payload, dict):
                    error_message = _extract_error_message(last_payload)
                return {
                    "success": False,
                    "status_code": last_status_code,
                    "data": last_payload,
                    "action": action,
                    "result_kind": result_kind,
                    "taskid": str(taskid),
                    "error": error_message
                    or f"Result not ready within {poll_timeout:.0f}s. Retry later with taskid={taskid}.",
                    "polling": {
                        "waited": True,
                        "attempts": attempts,
                        "poll_timeout": poll_timeout,
                        "poll_interval": poll_interval,
                    },
                }

            error_message = _extract_error_message(payload) if isinstance(payload, dict) else None
            return {
                "success": False,
                "status_code": status_code,
                "data": payload,
                "action": action,
                "result_kind": result_kind,
                "error": error_message or "Remote service returned an error.",
            }

        if action == "download":
            if not download_path:
                return {"success": False, "status_code": 400, "error": "download_path is required", "action": action}
            path = download_path if download_path.startswith("/") else f"/{download_path}"
            url = f"{base_url}{path}"
            
            # Bug #7 Fix: Track dynamic path reconstruction status
            dynamic_rebuild_attempted = False
            dynamic_rebuild_success = False
            
            verify = _ssl_verify_enabled(base_url)
            try:
                response = await _do_httpx_request(
                    "GET",
                    url,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True,
                    verify=verify,
                )
            except httpx.HTTPError as exc:
                if verify and _should_retry_without_ssl_verify(base_url, exc):
                    logger.warning("PhageScope TLS verification failed for %s; retrying download with verify=False", url)
                    response = await _do_httpx_request(
                        "GET",
                        url,
                        headers=headers,
                        timeout=timeout,
                        follow_redirects=True,
                        verify=False,
                    )
                else:
                    raise
            content_type = response.headers.get("content-type", "")
            content = response.content or b""

            # Fallback: some documented paths (e.g. output/result/phage.tsv) are
            # not directly downloadable from the API root. If taskid is provided,
            # rebuild TSV via structured result endpoint.
            fallback_kind = DOWNLOAD_TSV_FALLBACKS.get(path.lower())
            inferred_kind = _infer_result_kind_from_path(path, fallback_kind=fallback_kind)
            
            # Bug #7 Fix: Try dynamic path reconstruction from task detail uploadpath first
            if response.status_code >= 400 and taskid and not dynamic_rebuild_success:
                dynamic_rebuild_attempted = True
                # First, try to get task detail to extract uploadpath
                try:
                    td_status, td_payload = await _request(
                        "GET",
                        base_url,
                        "/tasks/detail/",
                        params={"taskid": taskid},
                        headers=headers,
                        timeout=timeout,
                    )
                    
                    if td_status < 400 and isinstance(td_payload, dict):
                        results = td_payload.get("results")
                        if isinstance(results, dict):
                            # Try to extract uploadpath from task detail
                            uploadpath = results.get("uploadpath")
                            
                            if uploadpath and isinstance(uploadpath, str):
                                logger.info(f"Download action: extracted uploadpath from task detail: {uploadpath}")
                                
                                # Reconstruct download path from uploadpath
                                # uploadpath is typically like "/workspace/user_task/xxx/output/result/"
                                # We need to append the expected filename
                                filename = RESULT_KIND_TO_FILENAME.get(inferred_kind or "")

                                if filename:
                                    # Reconstruct path: uploadpath + filename
                                    dynamic_path = uploadpath.rstrip("/") + "/" + filename
                                    logger.info(f"Download action: reconstructed dynamic path: {dynamic_path}")
                                    
                                    # Try downloading with the reconstructed path
                                    dynamic_url = f"{base_url}{dynamic_path}"
                                    try:
                                        dynamic_response = await _do_httpx_request(
                                            "GET",
                                            dynamic_url,
                                            headers=headers,
                                            timeout=timeout,
                                            follow_redirects=True,
                                            verify=verify,
                                        )
                                    except httpx.HTTPError as exc:
                                        if verify and _should_retry_without_ssl_verify(base_url, exc):
                                            logger.warning(
                                                "PhageScope TLS verification failed for %s; retrying dynamic download with verify=False",
                                                dynamic_url,
                                            )
                                            dynamic_response = await _do_httpx_request(
                                                "GET",
                                                dynamic_url,
                                                headers=headers,
                                                timeout=timeout,
                                                follow_redirects=True,
                                                verify=False,
                                            )
                                        else:
                                            raise
                                    
                                    if dynamic_response.status_code < 400:
                                        logger.info(f"Download action: dynamic path reconstruction succeeded")
                                        response = dynamic_response
                                        content_type = dynamic_response.headers.get("content-type", "")
                                        content = dynamic_response.content or b""
                                        dynamic_rebuild_success = True
                                    else:
                                        logger.warning(
                                            f"Download action: dynamic path '{dynamic_path}' failed with status {dynamic_response.status_code}"
                                        )
                                else:
                                    logger.debug(
                                        "Download action: could not infer result filename for path '%s' (kind=%s)",
                                        path,
                                        inferred_kind,
                                    )
                            else:
                                logger.debug(
                                    f"Download action: no uploadpath found in task detail for taskid={taskid}"
                                )
                except Exception as e:
                    logger.warning(f"Download action: failed to fetch task detail for path reconstruction: {e}")
                    # Continue to fallback mechanism
            
            # Fallback to hardcoded path mapping if dynamic reconstruction failed or wasn't attempted
            if response.status_code >= 400 and not dynamic_rebuild_success and inferred_kind and taskid:
                fallback_endpoint = RESULT_ENDPOINTS.get(inferred_kind)
                if not fallback_endpoint:
                    fallback_endpoint = RESULT_ENDPOINTS.get(fallback_kind or "")

                if fallback_endpoint:
                    logger.info(
                        "Download action: using fallback mapping for path '%s' -> result_kind '%s'",
                        path,
                        inferred_kind,
                    )
                    fb_status, fb_payload = await _request(
                        "GET",
                        base_url,
                        fallback_endpoint,
                        params={"taskid": taskid},
                        headers=headers,
                        timeout=timeout,
                    )
                    if fb_status < 400 and isinstance(fb_payload, dict):
                        tsv_text = _results_payload_to_tsv_text(fb_payload)
                        if tsv_text is not None:
                            content = tsv_text.encode("utf-8")
                            content_type = "text/tab-separated-values; charset=utf-8"
                            if save_path:
                                dest = Path(save_path).expanduser().resolve()
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                dest.write_bytes(content)
                                return _with_api_only_artifact_hint(
                                    _attach_local_file_artifact_fields(
                                        {
                                        "success": True,
                                        "status_code": 200,
                                        "action": action,
                                        "content_type": content_type,
                                        "content_length": len(content),
                                        "fallback": "result_api_tsv",
                                        "taskid": str(taskid),
                                        "dynamic_rebuild_attempted": dynamic_rebuild_attempted,
                                        },
                                        local_path=dest,
                                        session_id=session_id,
                                        task_id=task_id,
                                        ancestor_chain=ancestor_chain,
                                        output_base_dir=dest.parent,
                                    ),
                                    str(taskid),
                                )
                            preview = content[: max(preview_bytes, 0)]
                            return _with_api_only_artifact_hint(
                                {
                                    "success": True,
                                    "status_code": 200,
                                    "action": action,
                                    "data": preview.decode("utf-8", errors="replace"),
                                    "content_type": content_type,
                                    "content_length": len(content),
                                    "preview_bytes": len(preview),
                                    "fallback": "result_api_tsv",
                                    "taskid": str(taskid),
                                    "dynamic_rebuild_attempted": dynamic_rebuild_attempted,
                                },
                                str(taskid),
                            )
                else:
                    logger.warning(
                        "Download action: no fallback endpoint available for path '%s' (kind=%s)",
                        path,
                        inferred_kind,
                    )

            if save_path:
                if response.status_code >= 400:
                    preview = content[: max(preview_bytes, 0)].decode("utf-8", errors="replace")
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "action": action,
                        "error": f"Download failed: HTTP {response.status_code}",
                        "content_type": content_type,
                        "content_length": len(content),
                        "preview": preview,
                    }
                dest = Path(save_path).expanduser().resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                dl_ok = _attach_local_file_artifact_fields(
                    {
                    "success": response.status_code < 400,
                    "status_code": response.status_code,
                    "action": action,
                    "content_type": content_type,
                    "content_length": len(content),
                    },
                    local_path=dest,
                    session_id=session_id,
                    task_id=task_id,
                    ancestor_chain=ancestor_chain,
                    output_base_dir=dest.parent,
                )
                if taskid:
                    dl_ok["taskid"] = str(taskid)
                return _with_api_only_artifact_hint(dl_ok, str(taskid) if taskid else None)
            preview = content[: max(preview_bytes, 0)]
            if "application/json" in content_type:
                try:
                    payload = json.loads(content.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    payload = {"raw": content.decode("utf-8", errors="replace")}
                return _with_api_only_artifact_hint(
                    _response_with_business_layer(
                        action,
                        response.status_code,
                        payload,
                        content_type=content_type,
                        content_length=len(content),
                    ),
                    str(taskid) if taskid else None,
                )
            if content_type.startswith("text/"):
                return _with_api_only_artifact_hint(
                    {
                        "success": response.status_code < 400,
                        "status_code": response.status_code,
                        "action": action,
                        "data": preview.decode("utf-8", errors="replace"),
                        "content_type": content_type,
                        "content_length": len(content),
                        "preview_bytes": len(preview),
                    },
                    str(taskid) if taskid else None,
                )
            return _with_api_only_artifact_hint(
                {
                    "success": response.status_code < 400,
                    "status_code": response.status_code,
                    "action": action,
                    "content_type": content_type,
                    "content_length": len(content),
                    "preview_bytes": len(preview),
                },
                str(taskid) if taskid else None,
            )

        if action == "save_all":
            # Requires taskid; optionally accepts output_dir
            if not taskid:
                return {"success": False, "status_code": 400, "error": "taskid is required", "action": action}

            # Determine output directory
            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            session_root = _resolve_session_phagescope_root(session_id)
            if session_id and task_id is not None and not save_path:
                from app.services.path_router import get_path_router

                router = get_path_router()
                default_output_dir = router.get_task_output_dir(
                    session_id,
                    task_id,
                    ancestor_chain,
                    create=True,
                )
            elif session_root is not None:
                default_output_dir = session_root / f"task_{taskid}_{timestamp_str}"
            else:
                default_output_dir = Path("runtime/phagescope") / f"task_{taskid}_{timestamp_str}"
            output_dir = Path(save_path) if save_path else default_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            # Create subdirectories
            metadata_dir = output_dir / "metadata"
            annotation_dir = output_dir / "annotation"
            sequences_dir = output_dir / "sequences"
            phylogeny_dir = output_dir / "phylogeny"
            raw_dir = output_dir / "raw_api_responses"

            for d in [metadata_dir, annotation_dir, sequences_dir, phylogeny_dir, raw_dir]:
                d.mkdir(parents=True, exist_ok=True)

            saved_files: Dict[str, str] = {}
            raw_responses: Dict[str, Any] = {}
            errors: List[str] = []

            # Helper to fetch and save a result kind
            async def fetch_and_save(result_kind: str) -> Optional[Dict[str, Any]]:
                endpoint = RESULT_ENDPOINTS.get(result_kind)
                if not endpoint:
                    return None
                try:
                    status_code, payload = await _request(
                        "GET", base_url, endpoint, params={"taskid": taskid}, headers=headers, timeout=timeout
                    )
                    raw_responses[result_kind] = {"status_code": status_code, "payload": payload}

                    # Save raw response
                    raw_file = raw_dir / f"{result_kind}_raw.json"
                    raw_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

                    if status_code >= 400:
                        errors.append(f"{result_kind}: HTTP {status_code}")
                        return None
                    rk_ok, rk_meta = _merge_http_and_business_success(status_code, payload)
                    if not rk_ok:
                        err = rk_meta.get("error") or ""
                        suffix = f" ({err})" if err else ""
                        errors.append(
                            f"{result_kind}: business code {rk_meta.get('business_code')}{suffix}"
                        )
                        return None
                    return payload
                except Exception as e:
                    errors.append(f"{result_kind}: {str(e)}")
                    return None

            # 1. Fetch task detail first for metadata
            detail_status, detail_payload = await _request(
                "GET", base_url, "/tasks/detail/", params={"taskid": taskid}, headers=headers, timeout=timeout
            )
            if detail_status < 400:
                td_ok, td_meta = _merge_http_and_business_success(detail_status, detail_payload)
                if not td_ok:
                    err = td_meta.get("error") or ""
                    suffix = f" ({err})" if err else ""
                    errors.append(
                        f"task_detail: business code {td_meta.get('business_code')}{suffix}"
                    )
            raw_responses["task_detail"] = {"status_code": detail_status, "payload": detail_payload}
            (raw_dir / "task_detail_raw.json").write_text(
                json.dumps(detail_payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            # 2. Fetch phage info
            phage_data = await fetch_and_save("phage")
            if phage_data:
                phage_file = metadata_dir / "phage_info.json"
                phage_file.write_text(json.dumps(phage_data, indent=2, ensure_ascii=False), encoding="utf-8")
                saved_files["phage_info"] = str(phage_file.relative_to(output_dir))

            # 3. Fetch quality
            quality_data = await fetch_and_save("quality")
            if quality_data:
                quality_file = metadata_dir / "quality.json"
                quality_file.write_text(json.dumps(quality_data, indent=2, ensure_ascii=False), encoding="utf-8")
                saved_files["quality"] = str(quality_file.relative_to(output_dir))

            # 4. Fetch proteins and save as both JSON and TSV
            proteins_data = await fetch_and_save("proteins")
            if proteins_data:
                proteins_json_file = annotation_dir / "proteins.json"
                proteins_json_file.write_text(json.dumps(proteins_data, indent=2, ensure_ascii=False), encoding="utf-8")
                saved_files["proteins_json"] = str(proteins_json_file.relative_to(output_dir))

                # Convert to TSV if results is a list
                results_list = proteins_data.get("results") if isinstance(proteins_data, dict) else None
                if isinstance(results_list, list) and results_list:
                    proteins_tsv_file = annotation_dir / "proteins.tsv"
                    # Get all unique keys from all records
                    all_keys: List[str] = []
                    for record in results_list:
                        if isinstance(record, dict):
                            for key in record.keys():
                                if key not in all_keys:
                                    all_keys.append(key)
                    if all_keys:
                        output = StringIO()
                        writer = csv.DictWriter(output, fieldnames=all_keys, delimiter="\t", extrasaction="ignore")
                        writer.writeheader()
                        for record in results_list:
                            if isinstance(record, dict):
                                writer.writerow(record)
                        proteins_tsv_file.write_text(output.getvalue(), encoding="utf-8")
                        saved_files["proteins_tsv"] = str(proteins_tsv_file.relative_to(output_dir))

            # 5. Fetch phagefasta (FASTA sequences)
            fasta_data = await fetch_and_save("phagefasta")
            if fasta_data:
                fasta_content = None
                # Try to extract actual FASTA content
                if isinstance(fasta_data, dict):
                    fasta_content = fasta_data.get("results") or fasta_data.get("fasta") or fasta_data.get("data")
                if isinstance(fasta_content, str) and fasta_content.strip():
                    fasta_file = sequences_dir / "phage.fasta"
                    fasta_file.write_text(fasta_content, encoding="utf-8")
                    saved_files["fasta"] = str(fasta_file.relative_to(output_dir))
                else:
                    # Save as JSON if not plain text
                    fasta_json_file = sequences_dir / "phagefasta.json"
                    fasta_json_file.write_text(json.dumps(fasta_data, indent=2, ensure_ascii=False), encoding="utf-8")
                    saved_files["fasta_json"] = str(fasta_json_file.relative_to(output_dir))

            # 6. Fetch tree (phylogenetic tree)
            tree_data = await fetch_and_save("tree")
            if tree_data:
                tree_content = None
                if isinstance(tree_data, dict):
                    tree_content = tree_data.get("results") or tree_data.get("tree") or tree_data.get("newick")
                # Check if it looks like Newick format
                if isinstance(tree_content, str) and ("(" in tree_content and ")" in tree_content):
                    tree_file = phylogeny_dir / "tree.nwk"
                    tree_file.write_text(tree_content, encoding="utf-8")
                    saved_files["tree_newick"] = str(tree_file.relative_to(output_dir))
                else:
                    # Save as JSON
                    tree_json_file = phylogeny_dir / "tree.json"
                    tree_json_file.write_text(json.dumps(tree_data, indent=2, ensure_ascii=False), encoding="utf-8")
                    saved_files["tree_json"] = str(tree_json_file.relative_to(output_dir))

            # 7. Fetch modules info
            module_names: List[str] = []
            if isinstance(detail_payload, dict):
                detail_results = detail_payload.get("results")
                if isinstance(detail_results, dict):
                    module_names = _parse_modulelist(detail_results.get("modulelist"))

            if module_names:
                modules_payload: Dict[str, Any] = {}
                for module_name in module_names:
                    module_name = str(module_name).strip()
                    if not module_name:
                        continue
                    safe_module_name = "".join(
                        ch if (ch.isalnum() or ch in {"_", "-"}) else "_"
                        for ch in module_name
                    )
                    try:
                        status_code, payload = await _request(
                            "GET",
                            base_url,
                            RESULT_ENDPOINTS["modules"],
                            params={"taskid": taskid, "module": module_name},
                            headers=headers,
                            timeout=timeout,
                        )
                        raw_key = f"modules:{module_name}"
                        raw_responses[raw_key] = {"status_code": status_code, "payload": payload}
                        raw_file = raw_dir / f"modules_{safe_module_name}_raw.json"
                        raw_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                        if status_code >= 400:
                            errors.append(f"modules[{module_name}]: HTTP {status_code}")
                            continue
                        mod_ok, mod_meta = _merge_http_and_business_success(status_code, payload)
                        if not mod_ok:
                            err = mod_meta.get("error") or ""
                            suffix = f" ({err})" if err else ""
                            errors.append(
                                f"modules[{module_name}]: business code {mod_meta.get('business_code')}{suffix}"
                            )
                            continue

                        # Persist per-module payload for easier downstream debugging/consumption.
                        module_out_file = annotation_dir / f"module_{safe_module_name}.json"
                        module_out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                        saved_files[f"module_{safe_module_name}"] = str(module_out_file.relative_to(output_dir))
                        modules_payload[module_name] = payload

                        # Fallbacks for flaky result endpoints:
                        # - quality endpoint may 500 while modules[quality] is available
                        # - proteins endpoint may 500 while modules[annotation] holds annotation records
                        if module_name.lower() == "quality" and "quality" not in saved_files:
                            quality_fallback_file = metadata_dir / "quality_from_modules.json"
                            quality_fallback_file.write_text(
                                json.dumps(payload, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                            saved_files["quality"] = str(quality_fallback_file.relative_to(output_dir))

                        if module_name.lower() == "annotation" and "proteins_json" not in saved_files:
                            proteins_fallback_file = annotation_dir / "proteins_from_annotation.json"
                            proteins_fallback_file.write_text(
                                json.dumps(payload, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                            saved_files["proteins_json"] = str(proteins_fallback_file.relative_to(output_dir))

                            # Try deriving TSV when annotation payload has tabular-like records.
                            ann_results = payload.get("results") if isinstance(payload, dict) else None
                            if isinstance(ann_results, list) and ann_results:
                                all_keys: List[str] = []
                                for record in ann_results:
                                    if isinstance(record, dict):
                                        for key in record.keys():
                                            if key not in all_keys:
                                                all_keys.append(key)
                                if all_keys:
                                    out = StringIO()
                                    writer = csv.DictWriter(out, fieldnames=all_keys, delimiter="\t", extrasaction="ignore")
                                    writer.writeheader()
                                    for record in ann_results:
                                        if isinstance(record, dict):
                                            writer.writerow(record)
                                    proteins_tsv_fallback = annotation_dir / "proteins_from_annotation.tsv"
                                    proteins_tsv_fallback.write_text(out.getvalue(), encoding="utf-8")
                                    saved_files["proteins_tsv"] = str(proteins_tsv_fallback.relative_to(output_dir))
                    except Exception as e:
                        errors.append(f"modules[{module_name}]: {str(e)}")
                if modules_payload:
                    modules_file = metadata_dir / "modules.json"
                    modules_file.write_text(json.dumps(modules_payload, indent=2, ensure_ascii=False), encoding="utf-8")
                    saved_files["modules"] = str(modules_file.relative_to(output_dir))
            else:
                # Backward compatibility for servers that may support modules aggregation.
                modules_data = await fetch_and_save("modules")
                if modules_data:
                    modules_file = metadata_dir / "modules.json"
                    modules_file.write_text(json.dumps(modules_data, indent=2, ensure_ascii=False), encoding="utf-8")
                    saved_files["modules"] = str(modules_file.relative_to(output_dir))

            # 8. Create summary.json
            summary = {
                "taskid": taskid,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "output_directory": str(output_dir.resolve()),
                "files": saved_files,
                "errors": errors if errors else None,
                "task_detail": detail_payload.get("results") if isinstance(detail_payload, dict) else None,
            }
            summary_file = output_dir / "summary.json"
            summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

            # Decide success semantics:
            # - If any endpoint failed, we return 207 (Multi-Status).
            # - But if core artifacts are present, treat it as usable success with warnings.
            has_quality = "quality" in saved_files
            has_proteins = ("proteins_tsv" in saved_files) or ("proteins_json" in saved_files)
            has_phage_info = "phage_info" in saved_files
            requested_module_set = {
                str(module_name).strip().lower()
                for module_name in (module_names or [])
                if str(module_name).strip()
            }
            expects_quality = ("quality" in requested_module_set) or (not requested_module_set)
            expects_proteins = ("annotation" in requested_module_set) or (not requested_module_set)
            core_saved = ((not expects_quality) or has_quality) and ((not expects_proteins) or has_proteins)

            missing_artifacts: List[str] = []
            # Derive missing artifacts from errors (e.g. "phagefasta: HTTP 500")
            if errors:
                for item in errors:
                    if not isinstance(item, str):
                        continue
                    name = item.split(":", 1)[0].strip()
                    if name and name not in missing_artifacts:
                        missing_artifacts.append(name)

            # Also infer missing of core files if absent
            if expects_quality and (not has_quality) and "quality" not in missing_artifacts:
                missing_artifacts.append("quality")
            if expects_proteins and (not has_proteins) and "proteins" not in missing_artifacts:
                missing_artifacts.append("proteins")
            if not has_phage_info and "phage" not in missing_artifacts:
                missing_artifacts.append("phage")

            # If fallback files were successfully generated, remove stale core-missing markers.
            if has_quality:
                missing_artifacts = [m for m in missing_artifacts if m != "quality"]
            if has_proteins:
                missing_artifacts = [m for m in missing_artifacts if m != "proteins"]
            if has_phage_info:
                missing_artifacts = [m for m in missing_artifacts if m != "phage"]
            if not expects_quality:
                missing_artifacts = [m for m in missing_artifacts if m != "quality"]
            if not expects_proteins:
                missing_artifacts = [m for m in missing_artifacts if m != "proteins"]

            partial = len(errors) > 0
            warnings: List[str] = []
            if partial and missing_artifacts:
                warnings.append(
                    "Partial download: some result kinds failed. Core results are available; missing: "
                    + ", ".join(missing_artifacts[:6])
                    + ("..." if len(missing_artifacts) > 6 else "")
                )

            return _attach_local_bundle_artifact_fields(
                {
                "success": True if (core_saved or len(errors) == 0) else False,
                "status_code": 200 if len(errors) == 0 else 207,  # 207 = Multi-Status
                "action": action,
                "artifact_scope": "local_bundle",
                "taskid": taskid,
                "output_directory": str(output_dir.resolve()),
                "output_directory_rel": str(output_dir),
                "files_saved": saved_files,
                "errors": errors if errors else None,
                "partial": True if partial else False,
                "missing_artifacts": missing_artifacts if missing_artifacts else None,
                "warnings": warnings if warnings else None,
                "summary_file": str(summary_file.resolve()),
                "summary_file_rel": str(summary_file),
                },
                output_dir=output_dir,
                saved_files=saved_files,
                summary_file=summary_file,
                session_id=session_id,
                task_id=task_id,
                ancestor_chain=ancestor_chain,
            )

        return {"success": False, "status_code": 400, "error": f"unsupported action: {action}", "action": action}
    except httpx.TimeoutException:
        return {"success": False, "status_code": 408, "error": f"timeout after {timeout}s", "action": action}
    except Exception as exc:
        logger.error("PhageScope tool failed: %s", exc)
        return {"success": False, "status_code": 500, "error": str(exc), "action": action}


phagescope_tool = {
    "name": "phagescope",
    "description": "Access PhageScope phage database and analysis service. Supports annotation pipelines, genome comparison (clustering, phylogenetic tree, alignment), bulk dataset download from the PhageScope download page, and various analysis types.",
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action to perform",
                "enum": [
                    "ping",
                    "input_check",
                    "submit",
                    "cluster_submit",
                    "task_list",
                    "task_detail",
                    "task_log",
                    "result",
                    "quality",
                    "download",
                    "query",
                    "save_all",
                    "batch_submit",
                    "batch_reconcile",
                    "batch_retry",
                    "bulk_download",
                ],
            },
            "base_url": {"type": "string", "description": "API base URL"},
            "token": {"type": "string", "description": "Optional auth token"},
            "timeout": {"type": "number", "description": "Request timeout in seconds", "default": 60.0},
            "phageid": {"type": "string", "description": "Single Phage ID or JSON list string"},
            "phageids": {"type": "string", "description": "Semicolon-separated Phage ID list"},
            "sequence_ids": {"description": "Alias of phage IDs (array/string accepted)"},
            "inputtype": {
                "type": "string",
                "description": "Input type",
                "enum": ["enter", "paste", "upload"],
                "default": "enter",
            },
            "sequence": {"type": "string", "description": "Paste sequence when inputtype=paste"},
            "file_path": {"type": "string", "description": "Upload file path when inputtype=upload"},
            "analysistype": {
                "type": "string",
                "description": "Analysis type",
                "enum": list(ANALYSIS_TYPES.keys()),
                "default": "Annotation Pipline",
            },
            "userid": {"type": "string", "description": "User ID"},
            "modulelist": {
                "description": (
                    "Module names (array/object/string supported). "
                    "For submit/batch_submit (Annotation Pipeline), use real submit modules: quality, annotation, host, "
                    "lifestyle, terminator, taxonomic, trna, anticrispr, crispr, arvf, transmembrane. "
                    "Do not pass result/output names such as proteins, phage_detail, phagefasta, or tree; "
                    "proteins are derived from annotation outputs. "
                    "For action=bulk_download, pass dataset data-type names instead: "
                    "phage_meta_data, annotated_protein, transcription_terminator, trna_tmrna, "
                    "anticrispr_protein, crispr_array, antimicrobial_resistance_gene, "
                    "virulent_factor, transmembrane_protein, phage_fasta, protein_fasta, gff3. "
                    "Omit for all data types."
                ),
            },
            "rundemo": {"type": "string", "description": "Run demo task flag", "default": "false"},
            "taskid": {"type": "string", "description": "Task ID"},
            "modulename": {"type": "string", "description": "Module name for task logs"},
            "result_kind": {
                "type": "string",
                "description": "Result type (canonical or aliases like modules-trna/modules-anticrispr)",
                "enum": list(RESULT_ENDPOINTS.keys()) + list(RESULT_KIND_ALIASES.keys()),
            },
            "module": {"type": "string", "description": "Module name for result=modules"},
            "page": {"type": "integer", "description": "Page number"},
            "pagesize": {"type": "integer", "description": "Page size"},
            "seq_type": {"type": "string", "description": "Sequence type for phagefasta"},
            "download_path": {"type": "string", "description": "Download path relative to API root"},
            "save_path": {"type": "string", "description": "Save download to this path"},
            "session_id": {"type": "string", "description": "Optional session id for runtime-scoped output paths"},
            "preview_bytes": {"type": "integer", "description": "Download preview bytes", "default": 4096},
            "wait": {
                "type": "boolean",
                "description": "When true, poll for result readiness before returning",
                "default": False,
            },
            "poll_interval": {
                "type": "number",
                "description": "Polling interval in seconds when wait=true",
                "default": 2.0,
            },
            "poll_timeout": {
                "type": "number",
                "description": "Max total polling time in seconds when wait=true",
                "default": 120.0,
            },
            # Cluster analysis specific parameters.
            "comparedatabase": {
                "type": "string",
                "description": "Whether to compare with database (for cluster_submit)",
            },
            "neednum": {
                "type": "string",
                "description": "Number of results to return (for cluster_submit)",
            },
            "phage_ids": {
                "description": (
                    "For batch_submit: list of phage accessions or semicolon/newline-separated string. "
                    "For bulk_download: datasource names to download (e.g. 'refseq', 'genbank;embl'). "
                    "Valid datasources: refseq, genbank, embl, ddbj, phagesdb, gvd, gpd, mgv, "
                    "temphd, chvd, igvd, img_vr, gov2, stv. Omit for all datasources."
                ),
            },
            "batch_id": {
                "type": "string",
                "description": "Batch manifest id (UUID); used by batch_submit/batch_reconcile/batch_retry.",
            },
            "strategy": {
                "type": "string",
                "description": "batch_submit: multi_one_task (default) or per_strain",
                "default": "multi_one_task",
            },
            "manifest_path": {
                "type": "string",
                "description": "Optional explicit path to batch manifest JSON (advanced).",
            },
            "retry_phage_ids": {
                "description": "For batch_retry: explicit ids to retry; if omitted, uses last_reconcile.missing_phage_ids from manifest.",
            },
            "phage_ids_file": {
                "type": "string",
                "description": "Optional path to a text file with one phage id per line (batch_submit).",
            },
        },
        "required": ["action"],
    },
    "handler": phagescope_handler,
    "tags": ["phage", "bioinformatics", "external-api", "genome-comparison"],
    "examples": [
        "Check a Phage ID and submit an analysis task",
        "Submit genome comparison task with cluster_submit (clustering, phylogenetic, alignment)",
        "Fetch quality results for a completed task",
        "Retrieve task logs or download result files",
        "Save all results from a completed task to local files (save_all)",
        "Download all PhageScope datasets: action=bulk_download",
        "Download RefSeq meta data and GFF3: action=bulk_download, phageids='refseq', modulelist='phage_meta_data,gff3'",
    ],
}
