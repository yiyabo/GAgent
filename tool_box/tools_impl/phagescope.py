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


def _get_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("PHAGESCOPE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _normalize_phagescope_taskid(value: Any) -> Optional[str]:
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
    # Set trust_env=False to ignore environment proxies (avoid SOCKS dependency issues).
    async with httpx.AsyncClient(timeout=timeout, headers=headers, trust_env=False) as client:
        response = await client.request(method, url, params=params, data=data, files=files)
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.status_code, response.json()
    return response.status_code, {"raw": response.text}


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
    preview_bytes: int = 4096,
    sequence: Optional[str] = None,
    file_path: Optional[str] = None,
    wait: bool = False,
    poll_interval: float = 2.0,
    poll_timeout: float = 120.0,
    # Cluster analysis specific parameters
    comparedatabase: Optional[str] = None,
    neednum: Optional[str] = None,
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

    if action == "quality":
        action = "result"
        result_kind = result_kind or "quality"
    raw_taskid = taskid
    taskid = _resolve_phagescope_taskid(taskid, session_id=session_id)

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
            return {"success": status_code < 400, "status_code": status_code, "data": payload, "action": "ping"}

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
            return {"success": status_code < 400, "status_code": status_code, "data": payload, "action": "input_check"}

        if action == "submit" or action == "cluster_submit":
            if not userid:
                return {"success": False, "status_code": 400, "error": "userid is required", "action": action}
            if not modulelist:
                return {"success": False, "status_code": 400, "error": "modulelist is required", "action": action}

            # Parse module list for validation.
            module_items: List[str] = []
            if isinstance(modulelist, (list, tuple)):
                module_items = [str(item) for item in modulelist]
            elif isinstance(modulelist, dict):
                module_items = list(modulelist.keys())
            elif isinstance(modulelist, str):
                parsed = _safe_json_loads(modulelist.replace("'", '"'))
                if isinstance(parsed, list):
                    module_items = [str(item) for item in parsed]
                elif isinstance(parsed, dict):
                    module_items = list(parsed.keys())

            # Validate module dependencies.
            is_valid, dep_error = _validate_module_dependencies(module_items)
            if not is_valid:
                return {"success": False, "status_code": 400, "error": dep_error, "action": action}

            # Auto-select the correct endpoint.
            if action == "cluster_submit":
                endpoint = "/analyze/clusterpipline/"
                actual_analysistype = "Genome Comparison"
            else:
                endpoint = _get_analysis_endpoint(analysistype, module_items)
                actual_analysistype = analysistype

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
                    "modulelist": _normalize_modulelist(modulelist),
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
            return {
                "success": status_code < 400,
                "status_code": status_code,
                "data": payload,
                "action": action,
                "endpoint": endpoint,
                "analysistype": actual_analysistype,
            }

        if action == "task_list":
            if not userid:
                return {"success": False, "status_code": 400, "error": "userid is required", "action": action}
            status_code, payload = await _request(
                "GET", base_url, "/tasks/list/", params={"userid": userid}, headers=headers, timeout=timeout
            )
            return {"success": status_code < 400, "status_code": status_code, "data": payload, "action": action}

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
                return {"success": status_code < 400, "status_code": status_code, "data": payload, "action": action}

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
            return {"success": status_code < 400, "status_code": status_code, "data": payload, "action": action}

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
            if status_code < 400:
                return {
                    "success": True,
                    "status_code": status_code,
                    "data": payload,
                    "action": action,
                    "result_kind": result_kind,
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
                        return {
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
            
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=headers,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = await client.get(url)
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
                                    async with httpx.AsyncClient(
                                        timeout=timeout,
                                        headers=headers,
                                        follow_redirects=True,
                                        trust_env=False,
                                    ) as dynamic_client:
                                        dynamic_response = await dynamic_client.get(dynamic_url)
                                    
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
                                return {
                                    "success": True,
                                    "status_code": 200,
                                    "action": action,
                                    "saved_path": str(dest),
                                    "content_type": content_type,
                                    "content_length": len(content),
                                    "fallback": "result_api_tsv",
                                    "taskid": str(taskid),
                                    "dynamic_rebuild_attempted": dynamic_rebuild_attempted,
                                }
                            preview = content[: max(preview_bytes, 0)]
                            return {
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
                            }
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
                return {
                    "success": response.status_code < 400,
                    "status_code": response.status_code,
                    "action": action,
                    "saved_path": str(dest),
                    "content_type": content_type,
                    "content_length": len(content),
                }
            preview = content[: max(preview_bytes, 0)]
            if "application/json" in content_type:
                try:
                    payload = json.loads(content.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    payload = {"raw": content.decode("utf-8", errors="replace")}
                return {
                    "success": response.status_code < 400,
                    "status_code": response.status_code,
                    "action": action,
                    "data": payload,
                    "content_type": content_type,
                    "content_length": len(content),
                }
            if content_type.startswith("text/"):
                return {
                    "success": response.status_code < 400,
                    "status_code": response.status_code,
                    "action": action,
                    "data": preview.decode("utf-8", errors="replace"),
                    "content_type": content_type,
                    "content_length": len(content),
                    "preview_bytes": len(preview),
                }
            return {
                "success": response.status_code < 400,
                "status_code": response.status_code,
                "action": action,
                "content_type": content_type,
                "content_length": len(content),
                "preview_bytes": len(preview),
            }

        if action == "save_all":
            # Requires taskid; optionally accepts output_dir
            if not taskid:
                return {"success": False, "status_code": 400, "error": "taskid is required", "action": action}

            # Determine output directory
            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            session_root = _resolve_session_phagescope_root(session_id)
            if session_root is not None:
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
                    return payload
                except Exception as e:
                    errors.append(f"{result_kind}: {str(e)}")
                    return None

            # 1. Fetch task detail first for metadata
            detail_status, detail_payload = await _request(
                "GET", base_url, "/tasks/detail/", params={"taskid": taskid}, headers=headers, timeout=timeout
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

            return {
                "success": True if (core_saved or len(errors) == 0) else False,
                "status_code": 200 if len(errors) == 0 else 207,  # 207 = Multi-Status
                "action": action,
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
            }

        return {"success": False, "status_code": 400, "error": f"unsupported action: {action}", "action": action}
    except httpx.TimeoutException:
        return {"success": False, "status_code": 408, "error": f"timeout after {timeout}s", "action": action}
    except Exception as exc:
        logger.error("PhageScope tool failed: %s", exc)
        return {"success": False, "status_code": 500, "error": str(exc), "action": action}


phagescope_tool = {
    "name": "phagescope",
    "description": "Access PhageScope phage database and analysis service. Supports annotation pipelines, genome comparison (clustering, phylogenetic tree, alignment), and various analysis types.",
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
                "description": "Module list (array/object/string supported)",
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
    ],
}
