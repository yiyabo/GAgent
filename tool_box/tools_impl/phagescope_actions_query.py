"""PhageScope read/query action branches (ping, task list/detail/log, query alias)."""

from typing import Any, Dict, List, NamedTuple, Optional


class _QueryOutcome(NamedTuple):
    """Resolved dispatch state for the ``query`` action alias.

    ``response`` is set when the alias must short-circuit with a payload; the
    remaining fields carry the resolved action/taskid/result_kind otherwise.
    """

    response: Optional[Dict[str, Any]] = None
    action: Optional[str] = None
    taskid: Optional[str] = None
    result_kind: Optional[str] = None


async def _action_ping(
    *,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    from . import phagescope as facade

    status_code, payload = await facade._request("GET", base_url, "/", headers=headers, timeout=timeout)
    return facade._response_with_business_layer("ping", status_code, payload)


async def _action_task_list(
    *,
    action: str,
    userid: Optional[str],
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    from . import phagescope as facade

    if not userid:
        return {"success": False, "status_code": 400, "error": "userid is required", "action": action}
    status_code, payload = await facade._request(
        "GET", base_url, "/tasks/list/", params={"userid": userid}, headers=headers, timeout=timeout
    )
    return facade._response_with_business_layer(action, status_code, payload)


async def _action_task_detail(
    *,
    action: str,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    taskid: str,
) -> Dict[str, Any]:
    from . import phagescope as facade

    status_code, payload = await facade._request(
        "GET", base_url, "/tasks/detail/", params={"taskid": taskid}, headers=headers, timeout=timeout
    )
    if isinstance(payload, dict):
        results = payload.get("results", {})
        modulelist_value = results.get("modulelist")
        payload["parsed_modulelist"] = facade._parse_modulelist(modulelist_value)
        task_detail = results.get("task_detail")
        parsed_detail = facade._safe_json_loads(task_detail) if isinstance(task_detail, str) else None
        if parsed_detail is not None:
            payload["parsed_task_detail"] = parsed_detail
    return facade._with_api_only_artifact_hint(
        facade._response_with_business_layer(action, status_code, payload),
        taskid,
    )


async def _action_task_log(
    *,
    action: str,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    taskid: Optional[str],
    modulename: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    if not taskid or not modulename:
        return {
            "success": False,
            "status_code": 400,
            "error": "taskid and modulename are required",
            "action": action,
        }
    status_code, payload = await facade._request(
        "GET",
        base_url,
        "/tasks/detail/log/",
        params={"taskid": taskid, "moudlename": modulename},
        headers=headers,
        timeout=timeout,
    )
    return facade._with_api_only_artifact_hint(
        facade._response_with_business_layer(action, status_code, payload),
        taskid,
    )


async def _resolve_query_action(
    *,
    taskid: Optional[str],
    result_kind: Optional[str],
    modulelist: Optional[Any],
    userid: Optional[str],
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
) -> _QueryOutcome:
    from . import phagescope as facade

    # Heuristic alias to avoid failing when the caller uses "query".
    resolved_taskid = taskid
    resolved_result = result_kind

    module_items: List[str] = []
    if modulelist is not None:
        if isinstance(modulelist, (list, tuple)):
            module_items = [str(item) for item in modulelist]
        elif isinstance(modulelist, str):
            parsed_modules = facade._safe_json_loads(modulelist.replace("'", '"'))
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
        status_code, payload = await facade._request(
            "GET", base_url, "/tasks/list/", params={"userid": userid}, headers=headers, timeout=timeout
        )
        if status_code >= 400:
            return _QueryOutcome(
                response={
                    "success": False,
                    "status_code": status_code,
                    "action": "query",
                    "error": "Failed to list tasks for query",
                    "data": payload,
                }
            )
        q_ok, q_meta = facade._merge_http_and_business_success(status_code, payload)
        if not q_ok:
            return _QueryOutcome(
                response={
                    "success": False,
                    "status_code": status_code,
                    "action": "query",
                    "data": payload,
                    "error": q_meta.get("error")
                    or f"PhageScope task list returned business code {q_meta.get('business_code')}",
                    **{k: v for k, v in q_meta.items() if k in ("business_code", "business_failure")},
                }
            )
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
            return _QueryOutcome(action="result", taskid=resolved_taskid, result_kind=resolved_result)
        return _QueryOutcome(action="task_detail", taskid=resolved_taskid, result_kind=result_kind)
    return _QueryOutcome(
        response={
            "success": False,
            "status_code": 400,
            "action": "query",
            "error": "query requires taskid or userid",
        }
    )


__all__ = [
    "_QueryOutcome",
    "_action_ping",
    "_action_task_detail",
    "_action_task_list",
    "_action_task_log",
    "_resolve_query_action",
]
