"""All supplied gateway operations, without a content acceptance stage."""
import asyncio
import hashlib
import json
import math
import time

import httpx

from . import phagescope_gateway_client as client
from .phagescope_gateway_artifacts import Output, input_file
from ..phagescope_gateway_contract import MODULES, NEEDS_ANNOTATION, OPERATIONS, ROUTES


def submission(params, context):
    unknown = set(params) - {"modules", "fasta", "file_path", "accession"}
    if unknown:
        raise client.GatewayError("invalid_parameter", "Unsupported submit parameters: " + ", ".join(sorted(unknown)))
    modules = params.get("modules")
    if not isinstance(modules, list) or not modules or not all(isinstance(m, str) and m in MODULES for m in modules):
        raise client.GatewayError("invalid_parameter", "modules must be a nonempty list of gateway module names.")
    modules = list(dict.fromkeys(modules))
    if set(modules) & NEEDS_ANNOTATION and "annotation" not in modules:
        modules.insert(0, "annotation")
    if "annotation" in modules:
        modules = ["annotation"] + [m for m in modules if m != "annotation"]
    if len(modules) > 12:
        raise client.GatewayError("invalid_parameter", "The gateway accepts at most 12 modules including required annotation.")
    sources = [key for key in ("fasta", "file_path", "accession") if params.get(key)]
    if len(sources) != 1:
        raise client.GatewayError("invalid_parameter", "Provide exactly one of fasta, file_path, accession.")
    kind = sources[0]
    value = params[kind]
    if not isinstance(value, str):
        raise client.GatewayError("invalid_parameter", f"{kind} must be a string.")
    if kind == "file_path":
        kind, value = "fasta", input_file(value, context)
    if kind == "fasta":
        if len(value.encode("utf-8")) > 20 * 1024**2:
            raise client.GatewayError("payload_too_large", "FASTA exceeds the gateway's 20 MB limit.")
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        headers = sum(line.startswith(">") for line in lines)
        if not lines or not lines[0].startswith(">") or not 1 <= headers <= 50:
            raise client.GatewayError("invalid_fasta", "FASTA needs a header and at most 50 sequences.")
        if not any(not line.startswith(">") for line in lines) or any(set(line.upper()) - set("ACGTRYSWKMBDHVN") for line in lines if not line.startswith(">")):
            raise client.GatewayError("invalid_fasta", "FASTA must contain nucleotide sequence text.")
    return {"modules": modules, kind: value}


async def submit(params, context, timeout):
    body = submission(params, context)
    # Refuse missing configuration before making a pending submission receipt.
    if not client.configured("analysis")["ready"]:
        raise client.GatewayError("configuration_required", "Configure the analysis gateway URL and PHAGESCOPE_GATEWAY_ANALYSIS_KEY.", 503)
    output = Output(context, "submissions")
    receipt = {"state": "submission_unknown", "modules": body["modules"],
               "input_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}
    output.json("receipt.json", receipt)
    try:
        status, payload, retry = await client.request("analysis", "jobs/", method="POST", body=body, timeout=timeout)
    except (httpx.HTTPError, client.GatewayError) as exc:
        # A lost response cannot establish that sbatch was never reached. Do not repeat POST.
        receipt["diagnostic"] = type(exc).__name__
        output.json("receipt.json", receipt)
        return output.attach({"success": False, "action": "gateway", "gateway_action": "submit",
                              "status_code": 502, "error_code": "submission_unknown", "submission_unknown": True,
                              "error": "Submission response was lost. Keep this receipt; do not automatically resubmit the analysis."})
    result = client.response("submit", "analysis", status, payload, retry)
    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    if result["success"] and (isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1):
        result.update(success=False, error_code="submission_unknown", submission_unknown=True,
                      error="Gateway accepted the request without a usable job_id; do not automatically resubmit.")
    remote_status = payload.get("status") if isinstance(payload, dict) else None
    receipt.update(state="submission_unknown" if result.get("submission_unknown") else remote_status or ("rejected" if not result["success"] else "submitted"),
                   job_id=job_id, response=payload, status_code=status)
    try:
        output.json("receipt.json", receipt)
    except OSError as exc:
        # Keep a known remote job visible even when the local disk fills after POST.
        result["receipt_write_error"] = type(exc).__name__
    result.update(job_id=job_id, requested_modules=params["modules"], effective_modules=body["modules"],
                  execution_status=remote_status)
    if remote_status == "failed":
        result.update(success=False, error_code="remote_execution_failed", error="Gateway reported that submission failed.")
    if job_id:
        result["next_actions"] = ["status", "save_results"]
    return output.attach(result)


async def query(operation, params, context, timeout):
    channel, path, query_params = client.query_request(operation, params)
    status, payload, retry = await client.request(channel, path, query=query_params, timeout=timeout)
    result = client.response(operation, channel, status, payload, retry)
    if operation in {"status", "results"}:
        result["job_id"] = params["job_id"]
    if operation == "status" and isinstance(payload, dict):
        result["execution_status"] = payload.get("status")
    if params.get("save") and result["success"]:
        output = Output(context, operation)
        output.json("response.json", payload)
        if operation == "results":
            output.module(params["module"], payload)
        elif operation == "phage_sequence" and isinstance(payload, dict):
            truncated = bool(payload.get("truncated"))
            name = "sequence.preview.fasta" if truncated else "sequence.fasta"
            output.write(name, (payload.get("header") or ">sequence") + "\n" + payload.get("sequence", "") + "\n")
            if truncated:
                output.truncated.append(name)
        manifest = output.manifest({"operation": operation, "source": "phagescope_agent_gateway"})
        result = output.attach(result)
        result["truncated_files"] = manifest["truncated_files"]
    return result


async def save_results(params, context, timeout):
    if set(params) - {"job_id", "modules"}:
        raise client.GatewayError("invalid_parameter", "save_results accepts job_id and optional modules.")
    client.positive(params.get("job_id"), "job_id")
    deadline = time.monotonic() + timeout
    status = await asyncio.wait_for(query("status", {"job_id": params["job_id"]}, context, timeout), timeout)
    if not status["success"]:
        return status
    modules = params.get("modules")
    if modules is None:
        modules = [row["module"] for row in status["data"].get("modules", []) if row.get("module")]
    if not isinstance(modules, list) or not modules or not all(isinstance(m, str) and m in MODULES for m in modules):
        raise client.GatewayError("invalid_parameter", "No known modules found; supply the job's module list.")
    output = Output(context, f"job_{params['job_id']}")
    output.json("status.json", status["data"])
    errors = []
    for module in dict.fromkeys(modules):
        if context and context.is_cancelled:
            errors.append({"code": "cancelled", "message": "Local result collection cancelled; remote analysis may continue."})
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append({"module": module, "code": "collection_timeout", "message": "Result collection time budget exhausted; saved files retained."})
            break
        try:
            result = await asyncio.wait_for(query("results", {"job_id": params["job_id"], "module": module}, context, remaining), remaining)
            if result["success"]:
                output.module(module, result["data"])
            else:
                errors.append({"module": module, "code": result.get("error_code"), "message": result.get("error")})
        except (asyncio.TimeoutError, httpx.HTTPError, client.GatewayError) as exc:
            errors.append({"module": module, "code": "collection_failed", "message": type(exc).__name__})
    manifest = output.manifest({"job_id": params["job_id"], "remote_status": status.get("execution_status"),
                                "source": "phagescope_agent_gateway", "collection_errors": errors,
                                "download_scope": "gateway_returned_text", "full_file_download_supported": False})
    return output.attach({"success": not errors, "status_code": 207 if errors else 200, "action": "gateway",
                          "gateway_action": "save_results", "job_id": params["job_id"],
                          "execution_status": status.get("execution_status"), "collection_errors": errors,
                          "truncated_files": manifest["truncated_files"], "missing_files": manifest["missing_files"],
                          "full_file_download_supported": False,
                          "note": "Saved the files/text returned by the gateway. Truncated entries are .preview files; full downloads are not exposed by this gateway contract."})


async def gateway_handler(operation, params=None, *, timeout=60, context=None):
    operation = (operation or "capabilities").strip().lower()
    try:
        if not isinstance(params, (dict, type(None))):
            raise client.GatewayError("invalid_parameter", "gateway_params must be a JSON object.")
        params = params or {}
        if operation not in OPERATIONS:
            raise client.GatewayError("unsupported_operation", "Unknown gateway_action.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise client.GatewayError("invalid_parameter", "timeout must be a positive finite number.")
        timeout = min(timeout, 120)
        if context and context.is_cancelled:
            raise client.GatewayError("cancelled", "Gateway call cancelled before starting.", 499)
        if operation == "capabilities":
            return {"success": True, "action": "gateway", "gateway_action": operation,
                    "channels": {c: client.configured(c) for c in ("data", "analysis")},
                    "operations": list(OPERATIONS), "analysis_modules": list(MODULES),
                    "content_review_required": False, "full_file_download_supported": False,
                    "remote_cancel_supported": False, "remote_job_listing_supported": False,
                    "workflow": "submit -> status -> save_results; use gateway_params.save=true to save data query responses."}
        if operation == "submit":
            return await submit(params, context, timeout)
        if operation == "save_results":
            return await save_results(params, context, timeout)
        return await query(operation, params, context, timeout)
    except client.GatewayError as exc:
        return {"success": False, "action": "gateway", "gateway_action": operation,
                "status_code": exc.status, "error_code": exc.code, "error": exc.message}
    except httpx.HTTPError as exc:
        return {"success": False, "action": "gateway", "gateway_action": operation,
                "status_code": 502, "error_code": "gateway_unavailable", "error": type(exc).__name__}
    except asyncio.TimeoutError:
        return {"success": False, "action": "gateway", "gateway_action": operation,
                "status_code": 408, "error_code": "gateway_timeout", "error": "Gateway call timed out."}
    except OSError as exc:
        return {"success": False, "action": "gateway", "gateway_action": operation,
                "status_code": 500, "error_code": "local_io_error", "error": type(exc).__name__}
