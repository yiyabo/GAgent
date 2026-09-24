"""PhageScope submit action branches (input_check, submit, cluster_submit)."""

from pathlib import Path
from typing import Any, Dict, Optional


async def _action_input_check(
    *,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    phageid: Optional[str],
    phageids: Optional[str],
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    data = facade._build_phage_payload(phageid, phageids)
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
        status_code, payload = await facade._request(
            "POST", base_url, "/analyze/inputcheck/", data=data, files=files, headers=headers, timeout=timeout
        )
    finally:
        if files:
            files["submitfile"].close()
    return facade._response_with_business_layer("input_check", status_code, payload)


async def _action_submit(
    *,
    action: str,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    userid: Optional[str],
    modulelist: Optional[Any],
    analysistype: str,
    phageid: Optional[str],
    phageids: Optional[str],
    inputtype: str,
    rundemo: str,
    comparedatabase: Optional[str],
    neednum: Optional[str],
    sequence: Optional[str],
    file_path: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    if not userid:
        return {"success": False, "status_code": 400, "error": "userid is required", "action": action}
    if not modulelist:
        return {"success": False, "status_code": 400, "error": "modulelist is required", "action": action}

    requested_module_probe = facade._coerce_module_items(modulelist, analysistype=analysistype)

    # Auto-select the correct endpoint.
    if action == "cluster_submit":
        endpoint = "/analyze/clusterpipline/"
        actual_analysistype = "Genome Comparison"
    else:
        endpoint = facade._get_analysis_endpoint(analysistype, requested_module_probe)
        actual_analysistype = analysistype

    (
        requested_module_items,
        normalized_modulelist_json,
        module_items,
        module_warnings,
    ) = facade._normalize_submit_module_request(
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
    is_valid, dep_error = facade._validate_module_dependencies(module_items)
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

    data = facade._build_phage_payload(phageid, phageids)
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
        status_code, payload = await facade._request(
            "POST", base_url, endpoint, data=data, files=files, headers=headers, timeout=timeout
        )
    finally:
        if files:
            files["submitfile"].close()
    return facade._response_with_business_layer(
        action,
        status_code,
        payload,
        endpoint=endpoint,
        analysistype=actual_analysistype,
        requested_modules=requested_module_items,
        normalized_modules=module_items,
        warnings=module_warnings or None,
    )


__all__ = [
    "_action_input_check",
    "_action_submit",
]
