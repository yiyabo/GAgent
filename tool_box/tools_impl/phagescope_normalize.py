"""PhageScope LLM input normalization helpers."""

import ast
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .phagescope_protocol import (
    ANALYSIS_TYPES,
    CLUSTER_MODULES,
    MODULE_DEPENDENCIES,
    RESULT_DERIVED_SUBMIT_MODULES,
    RESULT_ONLY_QUERY_KINDS,
)


def _parse_modulelist(value: Optional[str]) -> List[str]:
    if not value or not isinstance(value, str):
        return []
    value = value.strip()
    if not value:
        return []
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, (list, tuple)):
            return [str(item) for item in parsed]
    except (ValueError, SyntaxError):
        pass
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


def _safe_json_loads(value: Optional[str]) -> Optional[Any]:
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


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
        items = [_normalize_module_token(key) for key, enabled in value.items() if enabled and _normalize_module_token(key)]
    elif isinstance(value, str):
        raw = value.strip()
        parsed = _safe_json_loads(raw.replace("'", '"')) if raw.startswith(("{", "[")) else None
        if isinstance(parsed, dict):
            items = [_normalize_module_token(key) for key, enabled in parsed.items() if enabled and _normalize_module_token(key)]
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
    modulelist: Any, *, analysistype: str
) -> Tuple[List[str], str, List[str], List[str]]:
    requested_items = _coerce_module_items(modulelist, analysistype=analysistype)
    allowed_modules = [_normalize_module_token(item) for item in ANALYSIS_TYPES.get(analysistype, {}).get("modules", []) if _normalize_module_token(item)]
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
                warnings.append(f"module '{item}' is a result/output name, not a submit module; normalized to '{mapped}'.")
                continue
            if item in RESULT_ONLY_QUERY_KINDS:
                warnings.append(f"module '{item}' is a result/output name, not a submit module; it was removed from submit payload.")
                continue
            if item in allowed_set:
                if item not in normalized_items:
                    normalized_items.append(item)
                continue
            warnings.append(f"module '{item}' is not supported for analysistype '{analysistype}' and was removed.")
    modulelist_json = json.dumps({item: True for item in normalized_items}) if normalized_items else ""
    return requested_items, modulelist_json, normalized_items, warnings


_ALL_ANNOTATION_MODULES = {
    "quality": True, "host": True, "lifestyle": True, "annotation": True,
    "terminator": True, "taxonomic": True, "trna": True, "anticrispr": True,
    "crispr": True, "arvf": True, "transmembrane": True,
}


def _normalize_modulelist(value: Any) -> str:
    if value is None:
        return ""
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
    seen = set()
    deduped: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _coerce_accession_ids_from_sequence(value: Any) -> List[str]:
    if not isinstance(value, str):
        return []
    raw = value.strip()
    if not raw:
        return []
    if "\n" in raw and (">" in raw or len(raw) > 120):
        return []
    items = _coerce_sequence_ids(raw)
    if not items:
        return []
    accession_re = re.compile(r"^[A-Za-z]{1,6}_?\d+(?:\.\d+)?$")
    return items if all(accession_re.match(item) for item in items) else []


def _apply_sequence_ids_alias(
    phageid: Optional[str], phageids: Optional[str], sequence_ids: Any
) -> Tuple[Optional[str], Optional[str]]:
    ids = _coerce_sequence_ids(sequence_ids)
    if not ids:
        return phageid, phageids
    if not phageid:
        phageid = ids[0] if len(ids) == 1 else json.dumps(ids)
    if not phageids:
        phageids = ";".join(ids)
    return phageid, phageids


def _validate_module_dependencies(modules: List[str]) -> Tuple[bool, Optional[str]]:
    module_set = set(m.lower() for m in modules)
    for module, deps in MODULE_DEPENDENCIES.items():
        if module.lower() in module_set:
            for dep in deps:
                if dep.lower() not in module_set:
                    return False, f"Module '{module}' requires '{dep}' module"
    return True, None


def _is_cluster_analysis(analysistype: str, modules: Optional[List[str]] = None) -> bool:
    if analysistype == "Genome Comparison":
        return True
    if modules:
        return bool(set(m.lower() for m in modules) & CLUSTER_MODULES)
    return False


def _get_analysis_endpoint(analysistype: str, modules: Optional[List[str]] = None) -> str:
    config = ANALYSIS_TYPES.get(analysistype)
    if config:
        return config["endpoint"]
    if _is_cluster_analysis(analysistype, modules):
        return "/analyze/clusterpipline/"
    return "/analyze/pipline/"


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


def _build_phage_payload(phageid: Optional[str], phageids: Optional[str]) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    if phageid:
        payload["phageid"] = _ensure_json_list_string(phageid)
    if phageids:
        payload["phageids"] = _ensure_semicolon_list_string(phageids)
        if not phageid:
            parts = [p.strip() for p in phageids.replace(",", ";").split(";") if p.strip()]
            payload["phageid"] = json.dumps(parts) if parts else _ensure_json_list_string(phageids)
    elif phageid:
        payload["phageids"] = _ensure_semicolon_list_string(phageid)
    return payload


__all__ = [
    "_parse_modulelist", "_normalize_module_token", "_safe_json_loads",
    "_coerce_module_items", "_normalize_submit_module_request", "_ALL_ANNOTATION_MODULES",
    "_normalize_modulelist", "_coerce_sequence_ids", "_coerce_accession_ids_from_sequence",
    "_apply_sequence_ids_alias", "_validate_module_dependencies", "_is_cluster_analysis",
    "_get_analysis_endpoint", "_ensure_json_list_string", "_ensure_semicolon_list_string",
    "_build_phage_payload",
]
