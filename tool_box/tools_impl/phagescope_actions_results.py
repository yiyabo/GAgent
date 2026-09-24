"""PhageScope result action branch (result/quality, including wait polling)."""

import asyncio
import time
from typing import Any, Dict, Optional


async def _action_result(
    *,
    action: str,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    taskid: Optional[str],
    result_kind: Optional[str],
    module: Optional[str],
    userid: Optional[str],
    phageid: Optional[str],
    page: Optional[int],
    pagesize: Optional[int],
    seq_type: Optional[str],
    wait: bool,
    poll_interval: float,
    poll_timeout: float,
) -> Dict[str, Any]:
    from . import phagescope as facade

    if not result_kind:
        return {"success": False, "status_code": 400, "error": "result_kind is required", "action": action}
    result_kind, module = facade._normalize_result_kind_and_module(result_kind, module)
    endpoint = facade.RESULT_ENDPOINTS.get(result_kind)
    if not endpoint:
        return {
            "success": False,
            "status_code": 400,
            "error": f"unsupported result_kind: {result_kind}",
            "action": action,
        }
    if not taskid and userid:
        status_code, payload = await facade._request(
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
                facade.logger.warning(f"Invalid page parameter: {page}, must be >= 1, using 1 instead")
                page_val = 1
            params["page"] = page_val
        except (ValueError, TypeError):
            facade.logger.warning(f"Invalid page parameter: {page}, ignoring")

    if pagesize is not None:
        # Validate pagesize parameter (must be positive integer, max 1000)
        try:
            pagesize_val = int(pagesize)
            if pagesize_val < 1:
                facade.logger.warning(f"Invalid pagesize parameter: {pagesize}, must be >= 1, using 100 instead")
                pagesize_val = 100
            elif pagesize_val > 1000:
                facade.logger.warning(f"Pagesize parameter: {pagesize} exceeds max (1000), using 1000 instead")
                pagesize_val = 1000
            params["pagesize"] = pagesize_val
        except (ValueError, TypeError):
            facade.logger.warning(f"Invalid pagesize parameter: {pagesize}, ignoring")

    if seq_type:
        params["type"] = seq_type
    if result_kind == "phage_detail" and phageid:
        params["phageid"] = phageid

    # Log pagination parameters for debugging
    if params.get("page") or params.get("pagesize"):
        facade.logger.info(
            f"Result request with pagination: page={params.get('page')}, "
            f"pagesize={params.get('pagesize')}, endpoint={endpoint}, taskid={taskid}"
        )
    status_code, payload = await facade._request(
        "GET", base_url, endpoint, params=params, headers=headers, timeout=timeout
    )
    biz_ok, biz_meta = facade._merge_http_and_business_success(status_code, payload)
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
        return facade._with_api_only_artifact_hint(out, str(taskid) if taskid is not None else None)

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
    if isinstance(payload, dict) and facade._is_result_not_ready_error(status_code, payload) and not wait:
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

            td_status, td_payload = await facade._request(
                "GET",
                base_url,
                "/tasks/detail/",
                params={"taskid": taskid},
                headers=headers,
                timeout=timeout,
            )
            if isinstance(td_payload, dict):
                task_detail = facade._parse_task_detail(td_payload)
                if isinstance(task_detail, dict):
                    completed = facade._module_completed(task_detail, module_name)
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

            last_status_code, last_payload = await facade._request(
                "GET",
                base_url,
                endpoint,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            if last_status_code < 400:
                lb_ok, lb_meta = facade._merge_http_and_business_success(last_status_code, last_payload)
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
                    return facade._with_api_only_artifact_hint(out, str(taskid) if taskid is not None else None)
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
            if isinstance(last_payload, dict) and facade._is_result_not_ready_error(last_status_code, last_payload):
                continue

            if not (isinstance(last_payload, dict) and facade._is_retriable_result_error(last_status_code, last_payload)):
                break

        error_message = None
        if isinstance(last_payload, dict):
            error_message = facade._extract_error_message(last_payload)
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

    error_message = facade._extract_error_message(payload) if isinstance(payload, dict) else None
    return {
        "success": False,
        "status_code": status_code,
        "data": payload,
        "action": action,
        "result_kind": result_kind,
        "error": error_message or "Remote service returned an error.",
    }


__all__ = [
    "_action_result",
]
