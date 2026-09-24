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

from app.services.tool_output_resolver import get_tool_output_resolver

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://phageapi.deepomics.org"

from .phagescope_taskid import (
    _PHAGESCOPE_TASKID_HINT_RE,
    _PHAGESCOPE_TASKID_RE,
    _PHAGESCOPE_TRACKING_JOB_RE,
    _extract_taskid_from_payload,
    _lookup_remote_taskid_by_action_run,
    _lookup_remote_taskid_by_tracking_job,
    _normalize_phagescope_taskid,
    _resolve_phagescope_taskid,
)

from .phagescope_protocol import (
    ANALYSIS_TYPES,
    CLUSTER_MODULES,
    DOWNLOAD_TSV_FALLBACKS,
    FILENAME_TO_RESULT_KIND,
    MODULE_DEPENDENCIES,
    RESULT_DERIVED_SUBMIT_MODULES,
    RESULT_ENDPOINTS,
    RESULT_KIND_ALIASES,
    RESULT_KIND_TO_FILENAME,
    RESULT_ONLY_QUERY_KINDS,
    _extract_error_message,
    _infer_result_kind_from_path,
    _is_result_not_ready_error,
    _is_retriable_result_error,
    _merge_http_and_business_success,
    _module_completed,
    _normalize_result_kind_and_module,
    _parse_task_detail,
    _response_with_business_layer,
    _safe_json_loads,
)

from .phagescope_artifacts import (
    ARTIFACT_SCOPE_API_ONLY,
    LOCAL_BUNDLE_HINT_EN,
    _attach_local_bundle_artifact_fields,
    _attach_local_file_artifact_fields,
    _attach_output_location_fields,
    _dedupe_string_list,
    _resolve_session_phagescope_root,
    _resolve_session_root,
    _session_relative_path,
    _with_api_only_artifact_hint,
)


from .phagescope_transport import (
    _TLS_RETRY_WARNING,
    _attach_transport_warning,
    _decode_httpx_response,
    _do_httpx_request,
    _get_base_url,
    _request,
    _should_retry_without_ssl_verify,
    _ssl_verify_enabled,
)


def _get_manifests_directory(session_id: Optional[str]) -> Tuple[Path, Optional[str]]:
    """Return ``.../work/phagescope/manifests`` under the session, or ToolOutputResolver fallback."""
    warning: Optional[str] = None
    token = str(session_id or "").strip()
    if token:
        root = _resolve_session_phagescope_root(token)
        if root is not None:
            mdir = root / "manifests"
            mdir.mkdir(parents=True, exist_ok=True)
            return mdir.resolve(), None
    resolver = get_tool_output_resolver()
    mdir = resolver.resolve(session_id=None, tool_name="phagescope", create=True) / "manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    warning = "no session_id: manifest stored via ToolOutputResolver fallback"
    return mdir.resolve(), warning


from .phagescope_batch import (
    _DEFAULT_BATCH_SUBMIT_MODULELIST,
    _coerce_modulelist_for_manifest,
    _dedupe_phage_ids_preserve_order,
    _extract_taskid_from_submit_result,
    _load_manifest_json,
    _normalize_phage_id_list,
    _phage_accession_ids_from_result_payload,
    _phagescope_batch_reconcile,
    _phagescope_batch_retry,
    _phagescope_batch_submit,
    _results_payload_to_tsv_text,
    _save_manifest_json,
)


from .phagescope_normalize import (
    _ALL_ANNOTATION_MODULES,
    _apply_sequence_ids_alias,
    _build_phage_payload,
    _coerce_accession_ids_from_sequence,
    _coerce_module_items,
    _coerce_sequence_ids,
    _ensure_json_list_string,
    _ensure_semicolon_list_string,
    _get_analysis_endpoint,
    _is_cluster_analysis,
    _normalize_module_token,
    _normalize_modulelist,
    _normalize_submit_module_request,
    _parse_modulelist,
    _safe_json_loads,
    _validate_module_dependencies,
)

from .phagescope_actions_batch import (
    _action_batch_reconcile,
    _action_batch_retry,
    _action_batch_submit,
)

from .phagescope_actions_query import (
    _QueryOutcome,
    _action_ping,
    _action_task_detail,
    _action_task_list,
    _action_task_log,
    _resolve_query_action,
)

from .phagescope_actions_results import (
    _action_result,
)

from .phagescope_actions_submit import (
    _action_input_check,
    _action_submit,
)


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
        return await _action_batch_submit(
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
            phage_ids=phage_ids,
            phageids=phageids,
            phageid=phageid,
            phage_ids_file=phage_ids_file,
            batch_id=batch_id,
            strategy=strategy,
            manifest_path=manifest_path,
        )

    if action == "quality":
        action = "result"
        result_kind = result_kind or "quality"
    raw_taskid = taskid
    taskid = _resolve_phagescope_taskid(taskid, session_id=session_id)

    if action == "batch_reconcile":
        return await _action_batch_reconcile(
            base_url=base_url,
            token=token,
            timeout=timeout,
            session_id=session_id,
            batch_id=batch_id,
            taskid=taskid,
            wait=wait,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            manifest_path=manifest_path,
        )

    if action == "batch_retry":
        return await _action_batch_retry(
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
            batch_id=batch_id,
            retry_phage_ids=retry_phage_ids,
            manifest_path=manifest_path,
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
        outcome = await _resolve_query_action(
            taskid=taskid,
            result_kind=result_kind,
            modulelist=modulelist,
            userid=userid,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )
        if outcome.response is not None:
            return outcome.response
        action = outcome.action
        taskid = outcome.taskid
        result_kind = outcome.result_kind

    try:
        if action == "ping":
            return await _action_ping(base_url=base_url, headers=headers, timeout=timeout)

        if action == "input_check":
            return await _action_input_check(
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                phageid=phageid,
                phageids=phageids,
                inputtype=inputtype,
                sequence=sequence,
                file_path=file_path,
            )

        if action == "submit" or action == "cluster_submit":
            return await _action_submit(
                action=action,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                userid=userid,
                modulelist=modulelist,
                analysistype=analysistype,
                phageid=phageid,
                phageids=phageids,
                inputtype=inputtype,
                rundemo=rundemo,
                comparedatabase=comparedatabase,
                neednum=neednum,
                sequence=sequence,
                file_path=file_path,
            )

        if action == "task_list":
            return await _action_task_list(
                action=action,
                userid=userid,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
            )

        if action == "task_detail":
            if not taskid and (phageid or phageids):
                # LLM often calls task_detail with phageid instead of taskid.
                # Redirect to phage_detail result query which works with phageid.
                pass  # fall through to the result branch below
            elif not taskid:
                return {"success": False, "status_code": 400, "error": "taskid is required", "action": action}
            else:
                return await _action_task_detail(
                    action=action,
                    base_url=base_url,
                    headers=headers,
                    timeout=timeout,
                    taskid=taskid,
                )

            # Fallback: task_detail called with phageid -> redirect to result/phage_detail
            action = "result"
            result_kind = "phage_detail"

        if action == "task_log":
            return await _action_task_log(
                action=action,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                taskid=taskid,
                modulename=modulename,
            )

        if action == "result":
            return await _action_result(
                action=action,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                taskid=taskid,
                result_kind=result_kind,
                module=module,
                userid=userid,
                phageid=phageid,
                page=page,
                pagesize=pagesize,
                seq_type=seq_type,
                wait=wait,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )

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
                resolver = get_tool_output_resolver()
                default_output_dir = resolver.resolve(session_id=None, tool_name="phagescope", create=True) / f"task_{taskid}_{timestamp_str}"
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
