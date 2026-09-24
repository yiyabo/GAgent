"""PhageScope protocol mappings and response/payload classification helpers."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RESULT_ENDPOINTS = {
    "phage": "/tasks/result/phage/",
    "proteins": "/tasks/result/proteins/",
    "quality": "/tasks/result/quality/",
    "modules": "/tasks/result/modules/",
    "tree": "/tasks/result/tree/",
    "phagefasta": "/tasks/result/phagefasta/",
    "phage_detail": "/tasks/result/phage/detail/",
}

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

DOWNLOAD_TSV_FALLBACKS: Dict[str, str] = {
    "/output/result/phage.tsv": "phage",
    "/output/result/protein.tsv": "proteins",
    "/output/result/proteins.tsv": "proteins",
}

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

ANALYSIS_TYPES = {
    "Annotation Pipline": {
        "endpoint": "/analyze/pipline/",
        "description": "Gene annotation pipeline",
        "modules": [
            "quality", "host", "lifestyle", "annotation", "terminator",
            "taxonomic", "trna", "anticrispr", "crispr", "arvf", "transmembrane"
        ],
    },
    "Phenotype Annotation": {"endpoint": "/analyze/pipline/", "description": "Phenotype annotation"},
    "Structural Annotation": {"endpoint": "/analyze/pipline/", "description": "Structural annotation"},
    "Functional Annotation": {"endpoint": "/analyze/pipline/", "description": "Functional annotation"},
    "Completeness Assessment": {"endpoint": "/analyze/pipline/", "description": "Completeness assessment"},
    "Host Assignment": {"endpoint": "/analyze/pipline/", "description": "Host assignment"},
    "Lifestyle Prediction": {"endpoint": "/analyze/pipline/", "description": "Lifestyle prediction"},
    "Genome Comparison": {
        "endpoint": "/analyze/clusterpipline/",
        "description": "Genome comparison (clustering, phylogenetic tree, sequence alignment)",
        "modules": ["clustering", "phylogenetic", "alignment"],
    },
}

MODULE_DEPENDENCIES = {
    "anticrispr": ["annotation"],
    "transmembrane": ["annotation"],
    "taxonomic": ["annotation"],
    "arvf": ["annotation"],
    "terminator": ["annotation"],
}
CLUSTER_MODULES = {"clustering", "phylogenetic", "alignment"}
RESULT_DERIVED_SUBMIT_MODULES: Dict[str, str] = {
    "protein": "annotation",
    "proteins": "annotation",
    "tree": "phylogenetic",
}
RESULT_ONLY_QUERY_KINDS = {"proteins", "phage_detail", "phagefasta", "modules", "tree"}


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
    for candidate in sorted(RESULT_KIND_TO_FILENAME.keys(), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", normalized):
            return candidate
    return fallback_kind


def _normalize_result_kind_and_module(
    result_kind: Optional[str], module: Optional[str]
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
    """Combine HTTP status with PhageScope JSON ``code`` when present."""
    http_ok = status_code < 400
    meta: Dict[str, Any] = {}
    if not isinstance(payload, dict) or "code" not in payload:
        return http_ok, meta
    try:
        code_int = int(payload["code"])
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


def _response_with_business_layer(action: str, status_code: int, payload: Any, **extra: Any) -> Dict[str, Any]:
    """HTTP 200 with business ``code`` >= 2 => ``success`` False."""
    success, biz_meta = _merge_http_and_business_success(status_code, payload)
    out: Dict[str, Any] = {"success": success, "status_code": status_code, "data": payload, "action": action}
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
    return "filenotfounderror" in raw_lower or "no such file or directory" in raw_lower or "file not found" in raw_lower


def _is_result_not_ready_error(status_code: int, payload: Dict[str, Any]) -> bool:
    """Detect PhageScope server-side 'result file not ready yet' errors."""
    if status_code < 400:
        return False
    raw = payload.get("raw")
    if not isinstance(raw, str) or not raw.strip():
        return False
    raw_lower = raw.lower()
    if "filenotfounderror" not in raw_lower and "no such file or directory" not in raw_lower:
        return False
    has_output_path = "/output/result/" in raw_lower or "/output/rawdata/" in raw_lower or "workspace/user_task" in raw_lower or "/tasks/result/" in raw_lower
    has_result_ext = any(ext in raw_lower for ext in (".tsv", ".txt", ".fasta", ".fa", ".nwk", ".json"))
    return bool(has_output_path and has_result_ext)


def _safe_json_loads(value: Optional[str]) -> Optional[Any]:
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


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
    """Check whether a module has completed and data is valid."""
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
        if not isinstance(module, str) or module.lower() != module_name_lower:
            continue
        status_value = item.get("module_satus") or item.get("module_status") or item.get("status")
        if not isinstance(status_value, str):
            logger.debug(f"_module_completed: module '{module_name}' status is not a string, type={type(status_value)}")
            return None
        status_upper = status_value.strip().upper()
        logger.debug(f"_module_completed: module '{module_name}' status='{status_upper}'")
        if status_upper in {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}:
            has_data = False
            for data_key in ("result", "results", "data", "output", "uploadpath"):
                data_value = item.get(data_key)
                if data_value is not None:
                    if isinstance(data_value, (list, dict, str)):
                        if data_value:
                            has_data = True
                            break
                    else:
                        has_data = True
                        break
            if has_data:
                logger.info(f"_module_completed: module '{module_name}' completed with valid data")
                return True
            logger.warning(
                f"_module_completed: module '{module_name}' status is '{status_upper}' but data appears empty. "
                f"Item keys: {list(item.keys())}"
            )
            return True
        if status_upper in {"FAILED", "ERROR"}:
            error_msg = item.get("error") or item.get("message") or item.get("detail")
            if error_msg:
                logger.error(f"_module_completed: module '{module_name}' failed with error: {error_msg}")
            else:
                logger.error(f"_module_completed: module '{module_name}' failed")
            return False
        logger.debug(f"_module_completed: module '{module_name}' status is '{status_upper}', not yet completed")
        return None
    logger.debug(f"_module_completed: module '{module_name}' not found in task_que")
    return None


__all__ = [
    "RESULT_ENDPOINTS", "RESULT_KIND_ALIASES", "DOWNLOAD_TSV_FALLBACKS",
    "RESULT_KIND_TO_FILENAME", "FILENAME_TO_RESULT_KIND", "ANALYSIS_TYPES",
    "MODULE_DEPENDENCIES", "CLUSTER_MODULES", "RESULT_DERIVED_SUBMIT_MODULES",
    "RESULT_ONLY_QUERY_KINDS", "_infer_result_kind_from_path",
    "_normalize_result_kind_and_module", "_extract_error_message",
    "_merge_http_and_business_success", "_response_with_business_layer",
    "_is_retriable_result_error", "_is_result_not_ready_error", "_safe_json_loads",
    "_parse_task_detail", "_module_completed",
]

# The original file intentionally keeps its full public/private import surface.
# This list documents the moved names without changing runtime behavior.

# end
