"""
PhageScope API Tool

Provides access to the PhageScope phage analysis service.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.services.tool_output_resolver import get_tool_output_resolver

logger = logging.getLogger(__name__)

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
    DEFAULT_BASE_URL,
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

from .phagescope_actions_download import (
    _action_bulk_download,
    _action_download,
    _action_save_all,
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
        return await _action_bulk_download(
            base_url=base_url,
            timeout=timeout,
            session_id=session_id,
            task_id=task_id,
            ancestor_chain=ancestor_chain,
            save_path=save_path,
            pagesize=pagesize,
            phage_ids=phage_ids,
            phageids=phageids,
            phageid=phageid,
            modulelist=modulelist,
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
            return await _action_download(
                action=action,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                taskid=taskid,
                download_path=download_path,
                save_path=save_path,
                preview_bytes=preview_bytes,
                session_id=session_id,
                task_id=task_id,
                ancestor_chain=ancestor_chain,
            )

        if action == "save_all":
            return await _action_save_all(
                action=action,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                taskid=taskid,
                save_path=save_path,
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
